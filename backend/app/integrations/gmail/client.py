"""Thin wrapper around `googleapiclient.discovery.build("gmail")`.

Reuses the same per-user OAuth + token-refresh path the calendar
client uses (`GoogleCalendarClient._refresh_token`). The differences
are:

- Build target: `("gmail", "v1")`.
- Public surface: list_send_as_aliases, send_message, get_message,
  list_thread_messages, watch_mailbox, stop_watch, list_history.

All call sites take the per-request `Session` so a token refresh
persists inside the caller's transaction.
"""
from __future__ import annotations

import base64
import logging
from datetime import UTC, datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from sqlalchemy.orm import Session

from app.core.crypto import decrypt, encrypt
from app.integrations.google_calendar.client import GoogleAuthExpiredError
from app.models.crm import UserGoogleIntegration

logger = logging.getLogger(__name__)


class GmailClient:
    """Per-user Gmail facade. Instantiated for the lifetime of one
    request / worker job."""

    def __init__(
        self,
        session: Session,
        integration: UserGoogleIntegration,
    ) -> None:
        self._session = session
        self._integration = integration
        self._service: Any | None = None

    # ------------------------------------------------------------------
    # Service builder + refresh — shared logic with the calendar client.

    def _build_service(self) -> Any:
        if self._service is not None:
            return self._service
        self._ensure_fresh_token()
        from google.oauth2.credentials import Credentials  # noqa: PLC0415
        from googleapiclient.discovery import build  # noqa: PLC0415

        from app.core.config import get_settings  # noqa: PLC0415

        settings = get_settings()
        credentials = Credentials(
            token=decrypt(self._integration.access_token_encrypted),
            refresh_token=decrypt(self._integration.refresh_token_encrypted),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=settings.google_oauth_client_id,
            client_secret=settings.google_oauth_client_secret,
            scopes=self._integration.scopes.split(),
        )
        self._service = build(
            "gmail", "v1", credentials=credentials, cache_discovery=False
        )
        return self._service

    def _ensure_fresh_token(self) -> None:
        token_expires_at = self._integration.token_expires_at
        if token_expires_at.tzinfo is None:
            token_expires_at = token_expires_at.replace(tzinfo=UTC)
        if token_expires_at - datetime.now(UTC) > timedelta(seconds=60):
            return
        self._refresh_token()

    def _refresh_token(self) -> None:
        from google.auth.exceptions import RefreshError  # noqa: PLC0415
        from google.auth.transport.requests import Request  # noqa: PLC0415
        from google.oauth2.credentials import Credentials  # noqa: PLC0415

        from app.core.config import get_settings  # noqa: PLC0415

        settings = get_settings()
        credentials = Credentials(
            token=None,
            refresh_token=decrypt(self._integration.refresh_token_encrypted),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=settings.google_oauth_client_id,
            client_secret=settings.google_oauth_client_secret,
            scopes=self._integration.scopes.split(),
        )
        try:
            credentials.refresh(Request())
        except RefreshError as exc:
            logger.warning(
                "gmail.refresh_failed user_id=%s", self._integration.user_id
            )
            raise GoogleAuthExpiredError(
                "Refresh token rejected by Google"
            ) from exc
        self._integration.access_token_encrypted = encrypt(credentials.token)
        expires_at = credentials.expiry or datetime.now(UTC) + timedelta(minutes=55)
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        self._integration.token_expires_at = expires_at
        self._session.flush()
        self._service = None

    # ------------------------------------------------------------------
    # Public API

    def list_send_as_aliases(self) -> list[dict[str, Any]]:
        """Aliases configured under Gmail Settings → "Send mail as".

        Filters to verified ones — Gmail refuses to send from an
        unverified address regardless of what the operator picks.
        """
        service = self._build_service()
        response = (
            service.users().settings().sendAs().list(userId="me").execute()
        )
        out: list[dict[str, Any]] = []
        for item in response.get("sendAs", []):
            if item.get("verificationStatus", "accepted") not in (
                "accepted",
                "verified",
            ):
                continue
            out.append(
                {
                    "send_as_email": item.get("sendAsEmail"),
                    "display_name": item.get("displayName") or "",
                    "is_primary": bool(item.get("isPrimary")),
                    "is_default": bool(item.get("isDefault")),
                    "verification_status": item.get("verificationStatus"),
                }
            )
        return out

    def send_message(
        self,
        *,
        from_alias: str,
        from_name: str | None,
        to: list[str],
        cc: list[str] | None,
        bcc: list[str] | None,
        subject: str,
        body_html: str | None,
        body_text: str | None,
        in_reply_to_message_id: str | None = None,
        references: list[str] | None = None,
        thread_id: str | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Build the RFC 822 MIME message and dispatch via Gmail
        `users.messages.send`. Returns the upstream `{id, threadId,
        labelIds, ...}` response."""
        mime = _build_mime(
            from_alias=from_alias,
            from_name=from_name,
            to=to,
            cc=cc,
            bcc=bcc,
            subject=subject,
            body_html=body_html,
            body_text=body_text,
            in_reply_to_message_id=in_reply_to_message_id,
            references=references,
            extra_headers=extra_headers,
        )
        raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()
        body: dict[str, Any] = {"raw": raw}
        if thread_id:
            body["threadId"] = thread_id
        service = self._build_service()
        return (
            service.users()
            .messages()
            .send(userId="me", body=body)
            .execute()
        )

    def get_message(self, message_id: str) -> dict[str, Any]:
        service = self._build_service()
        return (
            service.users()
            .messages()
            .get(userId="me", id=message_id, format="full")
            .execute()
        )

    def list_draft_templates(
        self, *, query: str | None = None, max_results: int = 30
    ) -> list[dict[str, Any]]:
        """Lista drafts del user. Una sola página (max_results).
        Para enumerar TODOS los drafts del buzón usa
        `list_all_drafts()` que pagina con `nextPageToken`."""
        service = self._build_service()
        response = (
            service.users()
            .drafts()
            .list(
                userId="me",
                q=query,
                maxResults=max_results,
            )
            .execute()
        )
        out: list[dict[str, Any]] = []
        for entry in response.get("drafts", []) or []:
            out.append({"id": entry["id"]})
        return out

    def list_all_drafts(self, *, page_size: int = 100) -> list[str]:
        """Itera con `nextPageToken` hasta agotar todos los drafts del
        usuario. Usado por `gmail-templates/import` que necesita
        inspeccionar el buzón completo para filtrar por subject.
        Devuelve sólo los ids; el caller hace `drafts.get` para los
        que vayan a importarse."""
        service = self._build_service()
        ids: list[str] = []
        page_token: str | None = None
        while True:
            kwargs: dict[str, Any] = {
                "userId": "me",
                "maxResults": page_size,
            }
            if page_token:
                kwargs["pageToken"] = page_token
            response = service.users().drafts().list(**kwargs).execute()
            for entry in response.get("drafts", []) or []:
                ids.append(entry["id"])
            page_token = response.get("nextPageToken")
            if not page_token:
                break
        return ids

    def delete_draft(self, draft_id: str) -> None:
        """Borra el draft. Usado por la flag `delete_after=true` del
        importador para limpiar Gmail después de copiar el template."""
        service = self._build_service()
        service.users().drafts().delete(userId="me", id=draft_id).execute()

    def get_draft_metadata(self, draft_id: str) -> dict[str, Any]:
        """Solo headers + labelIds — más barato que `format=full`.
        Usado para filtrar drafts por etiqueta antes de pedir el body
        completo."""
        service = self._build_service()
        return (
            service.users()
            .drafts()
            .get(userId="me", id=draft_id, format="metadata")
            .execute()
        )

    def get_draft_template(self, draft_id: str) -> dict[str, Any]:
        """Pull subject + body de un draft template. `format=raw`
        devuelve el MIME completo en `message.raw` (base64url) para
        que el caller parsee subject/body con email.message_from_bytes
        sin tener que navegar la estructura `payload.parts[].body.data`
        de `format=full`."""
        service = self._build_service()
        return (
            service.users()
            .drafts()
            .get(userId="me", id=draft_id, format="raw")
            .execute()
        )

    def list_thread_messages(self, thread_id: str) -> list[dict[str, Any]]:
        service = self._build_service()
        response = (
            service.users()
            .threads()
            .get(userId="me", id=thread_id, format="full")
            .execute()
        )
        return list(response.get("messages", []))

    def watch_mailbox(
        self,
        topic_name: str,
        label_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Register a Push Notifications watch on the mailbox.
        Returns `{historyId, expiration}` from upstream."""
        service = self._build_service()
        body: dict[str, Any] = {
            "topicName": topic_name,
            "labelFilterAction": "include",
        }
        if label_ids:
            body["labelIds"] = label_ids
        return service.users().watch(userId="me", body=body).execute()

    def stop_watch(self) -> None:
        service = self._build_service()
        service.users().stop(userId="me").execute()

    def list_history(self, start_history_id: int) -> dict[str, Any]:
        """Wraps `users.history.list`. The caller is responsible for
        paginating with `nextPageToken` if needed — typical webhook
        loads stay on the first page."""
        service = self._build_service()
        return (
            service.users()
            .history()
            .list(
                userId="me",
                startHistoryId=str(start_history_id),
                historyTypes=["messageAdded"],
            )
            .execute()
        )


def _build_mime(
    *,
    from_alias: str,
    from_name: str | None,
    to: list[str],
    cc: list[str] | None,
    bcc: list[str] | None,
    subject: str,
    body_html: str | None,
    body_text: str | None,
    in_reply_to_message_id: str | None,
    references: list[str] | None,
    extra_headers: dict[str, str] | None = None,
) -> MIMEMultipart:
    """Construct an RFC 822 multipart/alternative MIME message with
    the reply headers Gmail needs to chain a thread on external
    clients."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject or ""
    sender = f"{from_name} <{from_alias}>" if from_name else from_alias
    msg["From"] = sender
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    if bcc:
        msg["Bcc"] = ", ".join(bcc)
    if in_reply_to_message_id:
        msg["In-Reply-To"] = in_reply_to_message_id
    if references:
        msg["References"] = " ".join(references)
    if extra_headers:
        for header, value in extra_headers.items():
            msg[header] = value
    if body_text:
        msg.attach(MIMEText(body_text, "plain", "utf-8"))
    if body_html:
        msg.attach(MIMEText(body_html, "html", "utf-8"))
    if not body_text and not body_html:
        msg.attach(MIMEText("", "plain", "utf-8"))
    return msg
