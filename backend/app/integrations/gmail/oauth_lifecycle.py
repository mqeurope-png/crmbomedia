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
from app.integrations.google_calendar.service import get_org_integration
from app.models.crm import AuditLog, User, UserRole
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


def _admin_recipients(session: Session) -> list[User]:
    return list(
        session.scalars(
            select(User).where(
                User.role == UserRole.ADMIN, User.is_active.is_(True)
            )
        )
    )


def token_expiry_check(session: Session) -> int:
    """PR-OAuth-Google-Unificado. La conexión Google es org-wide y la
    gestiona el admin, así que el aviso de caducidad (<48h) va a los
    ADMINS. Devuelve cuántos emails se enviaron. `GMAIL_APP_VERIFIED=true`
    → sale early (0)."""
    settings = get_settings()
    if settings.gmail_app_verified:
        logger.info(
            "gmail.token_expiry_check skip — GMAIL_APP_VERIFIED=true"
        )
        return 0

    now = datetime.now(UTC)
    horizon = now + timedelta(hours=EXPIRY_WARNING_WINDOW_HOURS)
    dedup_since = now - timedelta(hours=WARNING_DEDUP_HOURS)

    org = get_org_integration(session)
    # PR-Hotfix-OAuth-Banner Bug 14. El aviso se dispara por la caducidad
    # del REFRESH token (7 días), NO del access token (1h, se refresca
    # solo). NULL = app verificada → sin caducidad → no avisar.
    refresh_exp = getattr(org, "refresh_token_expires_at", None) if org else None
    if refresh_exp is not None and refresh_exp.tzinfo is None:
        refresh_exp = refresh_exp.replace(tzinfo=UTC)
    if (
        org is None
        or org.status != "active"
        or refresh_exp is None
        or refresh_exp < now
        or refresh_exp > horizon
    ):
        return 0
    if _recent_warning_exists(session, org.id, dedup_since):
        return 0

    expires_local = refresh_exp.strftime("%d/%m/%Y %H:%M")
    from app.services.email import get_email_service  # noqa: PLC0415

    sent = 0
    for admin in _admin_recipients(session):
        if not admin.email:
            continue
        try:
            get_email_service().send_notification(
                to_email=admin.email,
                to_name=admin.full_name or "",
                subject="La conexión Google de BoHub CRM caduca pronto",
                text_body=(
                    f"Hola {admin.full_name or ''},\n\n"
                    f"La conexión Google de la organización ({org.google_email}) "
                    f"caduca el {expires_local}. Reconecta para no perder el "
                    f"sync de emails de todo el equipo.\n\n"
                    f"Entra en "
                    f"{settings.frontend_base_url.rstrip('/')}/admin/integrations"
                    f" y pulsa \"Reconectar Google\".\n"
                ),
            )
            sent += 1
        except Exception:  # noqa: BLE001
            logger.warning(
                "gmail.token_expiry_check email failed admin=%s",
                admin.id, exc_info=True,
            )
    if sent:
        record_event(
            session,
            action=Action.GMAIL_TOKEN_EXPIRY_WARNING_SENT,
            target_type="org_google_integration",
            target_id=org.id,
            actor_email=org.google_email,
            metadata={
                "token_expires_at": org.token_expires_at.isoformat(),
                "admins_notified": sent,
            },
        )
        session.commit()
    logger.info("gmail.token_expiry_check done warnings_sent=%d", sent)
    return sent


# ---------------------------------------------------------------------------
# Admin daily digest (Item 9)
# ---------------------------------------------------------------------------


def admin_daily_digest(session: Session) -> int:
    """PR-OAuth-Google-Unificado. Email diario a cada admin con el
    estado de la ÚNICA conexión Google org. Devuelve cuántos emails se
    enviaron. Si la conexión está sana (active, no caduca <48h) no
    envía nada."""
    settings = get_settings()
    now = datetime.now(UTC)
    horizon = now + timedelta(hours=EXPIRY_WARNING_WINDOW_HOURS)

    org = get_org_integration(session)
    if org is None:
        logger.info("gmail.admin_digest skip — sin conexión org")
        return 0

    # PR-Hotfix-OAuth-Banner Bug 14. El digest avisa por la caducidad del
    # REFRESH token (7 días), no del access token (1h). NULL = sin
    # caducidad (app verificada) → no avisar por caducidad.
    refresh_exp = getattr(org, "refresh_token_expires_at", None)
    if refresh_exp is not None and refresh_exp.tzinfo is None:
        refresh_exp = refresh_exp.replace(tzinfo=UTC)
    # Con la app verificada el refresh no caduca → no avisamos por
    # caducidad (el needs_reconnect de abajo sí sigue avisando).
    expiring_soon = (
        not settings.gmail_app_verified
        and org.status == "active"
        and refresh_exp is not None
        and now <= refresh_exp <= horizon
    )
    needs_reconnect = org.status == "needs_reconnect"
    if not expiring_soon and not needs_reconnect:
        logger.info("gmail.admin_digest skip — conexión org sana")
        return 0

    body = ["Estado de la conexión Google de la organización — BoHub CRM\n"]
    body.append(f"Cuenta: {org.google_email}")
    if needs_reconnect:
        body.append("Estado: ⚠ DESCONECTADA (needs_reconnect).")
        if org.last_refresh_error:
            body.append(f"Último error: {org.last_refresh_error}")
        body.append("El sync de emails de TODO el equipo está detenido.")
    elif expiring_soon:
        expires_local = refresh_exp.strftime("%d/%m/%Y %H:%M")
        body.append(
            f"Estado: activa, pero la reconexión Google es necesaria antes "
            f"del {expires_local}."
        )
    body.append(
        f"\nReconecta en "
        f"{settings.frontend_base_url.rstrip('/')}/admin/integrations"
    )
    text_body = "\n".join(body)

    from app.services.email import get_email_service  # noqa: PLC0415

    sent = 0
    for admin in _admin_recipients(session):
        if not admin.email:
            continue
        try:
            get_email_service().send_notification(
                to_email=admin.email,
                to_name=admin.full_name or "",
                subject="BoHub CRM — estado de la conexión Google",
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
            target_type="org_google_integration",
            target_id=org.id,
            metadata={
                "admins_notified": sent,
                "expiring_soon": expiring_soon,
                "needs_reconnect": needs_reconnect,
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
