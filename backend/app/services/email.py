"""Email service used for password-reset delivery.

Phase A: configuration via environment variables. The factory picks
`SMTPEmailService` when `ENVIRONMENT=production` and `SMTP_HOST` is set;
otherwise it returns a `ConsoleEmailService` (which logs the rendered email
to stdout and stores it in memory for tests). Phase B (a separate PR) will
move the SMTP credentials behind the existing integration-settings UI with
the password encrypted at rest using `INTEGRATION_SECRETS_KEY`.
"""
from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from email.message import EmailMessage
from functools import lru_cache
from pathlib import Path

import aiosmtplib
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates" / "email"

_jinja_env = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR),
    autoescape=select_autoescape(["html", "xml"]),
)

# Informational only — the backend doesn't enforce token expiry yet. Surfaced
# in the email so the user knows they shouldn't sit on the link for hours.
PASSWORD_RESET_EXPIRES_MINUTES = 60


@dataclass
class CapturedEmail:
    to_email: str
    to_name: str
    subject: str
    text_body: str
    html_body: str


class EmailService(ABC):
    @abstractmethod
    def send_password_reset(self, *, to_email: str, to_name: str, token: str) -> None: ...

    # PR-OAuth-Permisos-Admin Item 9. Envío genérico para avisos
    # transaccionales (caducidad token Gmail, digest admin). Plantillas
    # inline — no requiere ficheros Jinja nuevos.
    @abstractmethod
    def send_notification(
        self,
        *,
        to_email: str,
        to_name: str,
        subject: str,
        text_body: str,
        html_body: str | None = None,
    ) -> None: ...


def _render_password_reset(
    *,
    settings: Settings,
    to_name: str,
    token: str,
) -> tuple[str, str, str]:
    """Render the (subject, text_body, html_body) tuple for the reset email."""
    reset_url = f"{settings.frontend_base_url.rstrip('/')}/password-reset?token={token}"
    context = {
        "user_name": to_name or "",
        "app_name": settings.app_name,
        "reset_url": reset_url,
        "expires_in_minutes": PASSWORD_RESET_EXPIRES_MINUTES,
    }
    text_body = _jinja_env.get_template("password_reset.txt").render(**context)
    html_body = _jinja_env.get_template("password_reset.html").render(**context)
    subject = f"Recuperación de contraseña — {settings.app_name}"
    return subject, text_body, html_body


def _build_message(
    *,
    sender: str,
    sender_name: str,
    to_email: str,
    subject: str,
    text_body: str,
    html_body: str,
) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{sender_name} <{sender}>" if sender_name else sender
    msg["To"] = to_email
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")
    return msg


@dataclass
class ConsoleEmailService(EmailService):
    """Development / test backend. Captures every email in `sent` and logs a preview."""

    sent: list[CapturedEmail] = field(default_factory=list)

    def send_password_reset(self, *, to_email: str, to_name: str, token: str) -> None:
        settings = get_settings()
        subject, text_body, html_body = _render_password_reset(
            settings=settings, to_name=to_name, token=token
        )
        captured = CapturedEmail(
            to_email=to_email,
            to_name=to_name,
            subject=subject,
            text_body=text_body,
            html_body=html_body,
        )
        self.sent.append(captured)
        first_line = next((line for line in text_body.splitlines() if line.strip()), "")
        logger.info(
            "[email console] to=%s subject=%s preview=%s",
            to_email,
            subject,
            first_line,
        )

    def send_notification(
        self,
        *,
        to_email: str,
        to_name: str,
        subject: str,
        text_body: str,
        html_body: str | None = None,
    ) -> None:
        captured = CapturedEmail(
            to_email=to_email,
            to_name=to_name,
            subject=subject,
            text_body=text_body,
            html_body=html_body or f"<pre>{text_body}</pre>",
        )
        self.sent.append(captured)
        logger.info(
            "[email console] to=%s subject=%s (notification)",
            to_email, subject,
        )


@dataclass
class SMTPEmailService(EmailService):
    host: str
    port: int
    user: str | None
    password: str | None
    sender: str
    sender_name: str
    use_tls: bool
    use_ssl: bool
    timeout: int = 10

    @classmethod
    def from_settings(cls, settings: Settings) -> SMTPEmailService:
        return cls(
            host=settings.smtp_host or "",
            port=settings.smtp_port,
            user=settings.smtp_user,
            password=settings.smtp_password,
            sender=settings.smtp_from or settings.smtp_user or "",
            sender_name=settings.smtp_from_name,
            use_tls=settings.smtp_use_tls,
            use_ssl=settings.smtp_use_ssl,
        )

    def send_password_reset(self, *, to_email: str, to_name: str, token: str) -> None:
        settings = get_settings()
        subject, text_body, html_body = _render_password_reset(
            settings=settings, to_name=to_name, token=token
        )
        msg = _build_message(
            sender=self.sender,
            sender_name=self.sender_name,
            to_email=to_email,
            subject=subject,
            text_body=text_body,
            html_body=html_body,
        )
        # Sync handlers bridge to the async aiosmtplib API via a fresh event
        # loop. Password reset is rare so the cost of asyncio.run is fine.
        asyncio.run(self._send(msg))

    def send_notification(
        self,
        *,
        to_email: str,
        to_name: str,
        subject: str,
        text_body: str,
        html_body: str | None = None,
    ) -> None:
        msg = _build_message(
            sender=self.sender,
            sender_name=self.sender_name,
            to_email=to_email,
            subject=subject,
            text_body=text_body,
            html_body=html_body or f"<pre>{text_body}</pre>",
        )
        asyncio.run(self._send(msg))

    async def _send(self, msg: EmailMessage) -> None:
        await aiosmtplib.send(
            msg,
            hostname=self.host,
            port=self.port,
            username=self.user or None,
            password=self.password or None,
            # use_tls = implicit SSL (port 465). start_tls = STARTTLS upgrade (port 587).
            use_tls=self.use_ssl,
            start_tls=self.use_tls and not self.use_ssl,
            timeout=self.timeout,
        )


@lru_cache(maxsize=1)
def get_email_service() -> EmailService:
    settings = get_settings()
    is_production = settings.environment.lower() == "production"
    if is_production and settings.smtp_host:
        logger.info(
            "Email service: SMTPEmailService host=%s port=%s tls=%s ssl=%s",
            settings.smtp_host,
            settings.smtp_port,
            settings.smtp_use_tls,
            settings.smtp_use_ssl,
        )
        return SMTPEmailService.from_settings(settings)
    if is_production:
        logger.warning(
            "ENVIRONMENT=production but SMTP_HOST is not set; falling back to "
            "ConsoleEmailService. Password reset emails will be logged to stdout "
            "instead of delivered. Set SMTP_HOST/SMTP_USER/SMTP_PASSWORD/SMTP_FROM "
            "in .env.production to enable real delivery."
        )
    return ConsoleEmailService()
