"""PR-OAuth-Permisos-Admin Items 9 + 13 — crons del ciclo de vida OAuth.

Mientras la app OAuth no esté verificada por Google, los refresh tokens
caducan a 7 días. Estos crons avisan proactivamente para que el ciclo
sea sostenible:

- `gmail:token_expiry_check` (cada hora): avisa por email a cada user
  con token a <48h de caducar (1 aviso / 12h máx). Audit
  `gmail.token_expiry_warning_sent`.
- `gmail:admin_digest` (cada día): email al admin con el resumen de
  users problemáticos (próximos a caducar, needs_reconnect,
  refresh_failed reciente).
- `gmail:sync_aliases` (cada día): refleja los Send-As de Gmail en
  `user_email_alias_prefs` (Item 13).

Todos siguen el patrón self-rescheduling SETNX-guarded del resto del
CRM (backups.scheduler, brevo.scheduler).
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from rq import Queue
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import Action, record_event
from app.core.config import get_settings
from app.models.crm import AuditLog, User, UserGoogleIntegration, UserRole
from app.workers.queues import queue_name, redis_connection

logger = logging.getLogger(__name__)

EXPIRY_WARNING_WINDOW_HOURS = 48
WARNING_DEDUP_HOURS = 12

_EXPIRY_LOCK = "gmail:token_expiry_check:lock"
_DIGEST_LOCK = "gmail:admin_digest:lock"
_ALIASES_LOCK = "gmail:sync_aliases:lock"


# ---------------------------------------------------------------------------
# Token expiry check (Item 9)
# ---------------------------------------------------------------------------


def _recent_warning_exists(
    session: Session, integration_id: str, since: datetime
) -> bool:
    row = session.scalar(
        select(AuditLog.id).where(
            AuditLog.action == Action.GMAIL_TOKEN_EXPIRY_WARNING_SENT,
            AuditLog.target_id == integration_id,
            AuditLog.created_at >= since,
        )
    )
    return row is not None


def token_expiry_check(session: Session) -> int:
    """Avisa a users con token a <48h. Devuelve cuántos avisos se
    enviaron. Si `GMAIL_APP_VERIFIED=true` sale early (0)."""
    settings = get_settings()
    if settings.gmail_app_verified:
        logger.info(
            "gmail.token_expiry_check skip — GMAIL_APP_VERIFIED=true"
        )
        return 0

    now = datetime.now(UTC)
    horizon = now + timedelta(hours=EXPIRY_WARNING_WINDOW_HOURS)
    dedup_since = now - timedelta(hours=WARNING_DEDUP_HOURS)

    integrations = list(
        session.scalars(
            select(UserGoogleIntegration).where(
                UserGoogleIntegration.status == "active",
                UserGoogleIntegration.token_expires_at >= now,
                UserGoogleIntegration.token_expires_at <= horizon,
            )
        )
    )
    sent = 0
    from app.services.email import get_email_service  # noqa: PLC0415

    for integ in integrations:
        if _recent_warning_exists(session, integ.id, dedup_since):
            continue
        user = session.get(User, integ.user_id)
        if user is None or not user.email:
            continue
        expires_local = integ.token_expires_at.strftime("%d/%m/%Y %H:%M")
        try:
            get_email_service().send_notification(
                to_email=user.email,
                to_name=user.full_name or "",
                subject="Tu conexión Gmail en BoHub CRM caduca pronto",
                text_body=(
                    f"Hola {user.full_name or ''},\n\n"
                    f"Tu conexión de Gmail en BoHub CRM caduca el "
                    f"{expires_local}. Reconecta para no perder el sync de "
                    f"emails.\n\n"
                    f"Entra en {settings.frontend_base_url.rstrip('/')}/account "
                    f"y pulsa \"Reconectar Google\".\n"
                ),
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "gmail.token_expiry_check email failed user_id=%s",
                integ.user_id, exc_info=True,
            )
            continue
        record_event(
            session,
            action=Action.GMAIL_TOKEN_EXPIRY_WARNING_SENT,
            target_type="user_google_integration",
            target_id=integ.id,
            actor_email=integ.google_email,
            metadata={
                "user_id": integ.user_id,
                "token_expires_at": integ.token_expires_at.isoformat(),
            },
        )
        session.commit()
        sent += 1
    logger.info(
        "gmail.token_expiry_check done candidates=%d warnings_sent=%d",
        len(integrations), sent,
    )
    return sent


# ---------------------------------------------------------------------------
# Admin daily digest (Item 9)
# ---------------------------------------------------------------------------


def admin_daily_digest(session: Session) -> int:
    """Email a cada admin con el resumen de integraciones problemáticas.
    Devuelve cuántos emails se enviaron (uno por admin)."""
    settings = get_settings()
    now = datetime.now(UTC)
    horizon = now + timedelta(hours=EXPIRY_WARNING_WINDOW_HOURS)
    recent = now - timedelta(hours=24)

    expiring = list(
        session.scalars(
            select(UserGoogleIntegration).where(
                UserGoogleIntegration.status == "active",
                UserGoogleIntegration.token_expires_at >= now,
                UserGoogleIntegration.token_expires_at <= horizon,
            )
        )
    )
    disconnected = list(
        session.scalars(
            select(UserGoogleIntegration).where(
                UserGoogleIntegration.status == "needs_reconnect",
            )
        )
    )
    recent_fail = [
        i for i in disconnected
        if i.last_refresh_error_at is not None
        and i.last_refresh_error_at >= recent
    ]
    if not expiring and not disconnected:
        logger.info("gmail.admin_digest skip — nada que reportar")
        return 0

    def _line(i: UserGoogleIntegration) -> str:
        u = session.get(User, i.user_id)
        who = (u.full_name or u.email) if u else i.user_id
        return f"  - {who} ({i.google_email})"

    body = ["Resumen diario de conexiones Gmail — BoHub CRM\n"]
    if expiring:
        body.append(f"Tokens próximos a caducar (<48h): {len(expiring)}")
        body.extend(_line(i) for i in expiring)
        body.append("")
    if disconnected:
        body.append(f"Users desconectados (needs_reconnect): {len(disconnected)}")
        body.extend(_line(i) for i in disconnected)
        body.append("")
    if recent_fail:
        body.append(f"Refresh fallido en las últimas 24h: {len(recent_fail)}")
    body.append(
        f"\nGestiona los users en "
        f"{settings.frontend_base_url.rstrip('/')}/admin/users"
    )
    text_body = "\n".join(body)

    admins = list(
        session.scalars(
            select(User).where(
                User.role == UserRole.ADMIN, User.is_active.is_(True)
            )
        )
    )
    from app.services.email import get_email_service  # noqa: PLC0415

    sent = 0
    for admin in admins:
        if not admin.email:
            continue
        try:
            get_email_service().send_notification(
                to_email=admin.email,
                to_name=admin.full_name or "",
                subject="BoHub CRM — resumen diario de conexiones Gmail",
                text_body=text_body,
            )
            sent += 1
        except Exception:  # noqa: BLE001
            logger.warning(
                "gmail.admin_digest email failed admin=%s", admin.id,
                exc_info=True,
            )
    if sent:
        record_event(
            session,
            action=Action.GMAIL_ADMIN_DIGEST_SENT,
            target_type="system",
            metadata={
                "admins_notified": sent,
                "expiring": len(expiring),
                "needs_reconnect": len(disconnected),
            },
        )
        session.commit()
    logger.info("gmail.admin_digest done admins_notified=%d", sent)
    return sent


# ---------------------------------------------------------------------------
# RQ runners + self-rescheduling schedulers
# ---------------------------------------------------------------------------


def _open_session() -> Session:
    from app.db.session import get_engine  # noqa: PLC0415

    return Session(get_engine())


def token_expiry_check_runner() -> None:
    try:
        with _open_session() as session:
            token_expiry_check(session)
    finally:
        schedule_token_expiry_check()


def admin_digest_runner() -> None:
    try:
        with _open_session() as session:
            admin_daily_digest(session)
    finally:
        schedule_admin_digest()


def sync_aliases_runner() -> None:
    try:
        with _open_session() as session:
            from app.integrations.gmail.aliases import (  # noqa: PLC0415
                sync_all_active_users,
            )

            sync_all_active_users(session)
    finally:
        schedule_sync_aliases()


def _arm(lock_key: str, queue_op: str, runner, interval: timedelta) -> None:
    try:
        conn = redis_connection()
        ttl = max(60, int(interval.total_seconds()) - 30)
        if not conn.set(lock_key, "1", nx=True, ex=ttl):
            return
        try:
            Queue(queue_name("gmail", queue_op), connection=conn).enqueue_in(
                interval, runner, job_timeout=600
            )
            logger.info(
                "gmail.%s armed next_run_in=%.0fs",
                queue_op, interval.total_seconds(),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("gmail.%s enqueue failed: %s", queue_op, exc)
            conn.delete(lock_key)
    except Exception as exc:  # noqa: BLE001
        logger.warning("gmail.%s redis unreachable: %s", queue_op, exc)


def schedule_token_expiry_check() -> None:
    _arm(
        _EXPIRY_LOCK,
        "token_expiry_check",
        token_expiry_check_runner,
        timedelta(hours=1),
    )


def schedule_admin_digest() -> None:
    _arm(_DIGEST_LOCK, "admin_digest", admin_digest_runner, timedelta(hours=24))


def schedule_sync_aliases() -> None:
    _arm(_ALIASES_LOCK, "sync_aliases", sync_aliases_runner, timedelta(hours=24))


def arm_all() -> None:
    """Llamado al arranque de la API. Arma los 3 crons."""
    schedule_token_expiry_check()
    schedule_admin_digest()
    schedule_sync_aliases()
