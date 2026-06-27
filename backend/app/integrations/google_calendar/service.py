"""High-level Google Calendar operations.

These functions are the only entry point the API layer should use —
the client is an implementation detail. Each function owns its own
transaction boundary (caller commits) and returns the freshly
updated row.

`sync_task_to_calendar` is intentionally tolerant: if the assignee
hasn't connected Google, hasn't picked a calendar, or the API call
fails, the task is left intact and the caller logs a warning. The
goal of the integration is to mirror tasks to a calendar, not to
gate task creation on Google being up.
"""
from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.crypto import encrypt
from app.integrations.google_calendar.client import (
    GoogleAuthExpiredError,
    GoogleCalendarClient,
)
from app.integrations.google_calendar.oauth import (
    GOOGLE_OAUTH_SCOPES,
    exchange_code_for_tokens,
)
from app.models.crm import Task, UserGoogleIntegration

logger = logging.getLogger(__name__)

# Redis key + TTL for OAuth state ↔ user_id mapping. Short TTL because
# the user is expected to bounce through Google in seconds.
OAUTH_STATE_KEY_PREFIX = "google_oauth:state:"
OAUTH_STATE_TTL_SECONDS = 600


def issue_oauth_state(user_id: str) -> str:
    """Create a CSRF-safe random state and bind it to `user_id` in
    Redis with a 10-minute TTL. Returns the state string."""
    from app.workers.queues import redis_connection  # noqa: PLC0415

    state = secrets.token_urlsafe(32)
    redis_connection().setex(
        f"{OAUTH_STATE_KEY_PREFIX}{state}",
        OAUTH_STATE_TTL_SECONDS,
        user_id,
    )
    return state


def consume_oauth_state(state: str) -> str | None:
    """Look up the user_id bound to `state` and atomically drop the
    key so the state can't be replayed."""
    from app.workers.queues import redis_connection  # noqa: PLC0415

    conn = redis_connection()
    key = f"{OAUTH_STATE_KEY_PREFIX}{state}"
    user_id = conn.get(key)
    if user_id is None:
        return None
    conn.delete(key)
    return user_id.decode() if isinstance(user_id, bytes) else str(user_id)


def get_integration(session: Session, user_id: str) -> UserGoogleIntegration | None:
    return session.scalar(
        select(UserGoogleIntegration).where(
            UserGoogleIntegration.user_id == user_id
        )
    )


def connect_user(
    session: Session, *, user_id: str, code: str, state: str
) -> UserGoogleIntegration:
    """Complete the OAuth flow: exchange the code, persist (or
    refresh) the row, return it.

    Scope-expansion flow: when an existing user re-authorises (e.g.
    to add Gmail to a Calendar-only integration), we merge the
    scopes and KEEP the previously-selected calendar — the old
    `selected_calendar_id` belongs to the same Google account so
    resetting it would force the operator to re-pick on every
    incremental authorisation.

    The calendar selection IS dropped when the connected Google
    email changes (the user authorised with a different account),
    since calendar ids aren't portable across accounts.
    """
    result = exchange_code_for_tokens(code=code, state=state)
    integration = get_integration(session, user_id)
    now = datetime.now(UTC)
    previous_scopes: set[str] = set()
    # PR-OAuth-Permisos-Admin Item 12. ¿Es una reconexión (la fila ya
    # existía en needs_reconnect / disconnected_by_user)? Lo usamos para
    # el audit log `gmail.reconnected` vs `gmail.connected`.
    was_reconnect = (
        integration is not None
        and getattr(integration, "status", "active") != "active"
    )
    if integration is None:
        integration = UserGoogleIntegration(
            user_id=user_id,
            google_email=result.google_email,
            access_token_encrypted=encrypt(result.access_token),
            refresh_token_encrypted=encrypt(result.refresh_token),
            token_expires_at=result.expires_at,
            scopes=" ".join(result.scopes),
            connected_at=now,
            status="active",
        )
        session.add(integration)
    else:
        account_changed = integration.google_email != result.google_email
        previous_scopes = set((integration.scopes or "").split())
        merged_scopes = sorted(previous_scopes | set(result.scopes))
        integration.google_email = result.google_email
        integration.access_token_encrypted = encrypt(result.access_token)
        integration.refresh_token_encrypted = encrypt(result.refresh_token)
        integration.token_expires_at = result.expires_at
        integration.scopes = " ".join(merged_scopes)
        integration.connected_at = now
        # PR-OAuth-Permisos-Admin Item 12. Reconexión → vuelve a activo,
        # limpia el último error de refresh.
        integration.status = "active"
        integration.last_refresh_error = None
        integration.last_refresh_error_at = None
        if account_changed:
            # New Google account → old calendar id is meaningless.
            integration.selected_calendar_id = None
            integration.selected_calendar_summary = None
    session.flush()

    # PR-OAuth-Permisos-Admin Item 12. Audit log de la (re)conexión.
    from app.core.audit import Action, record_event  # noqa: PLC0415

    record_event(
        session,
        action=Action.GMAIL_RECONNECTED if was_reconnect else Action.GMAIL_CONNECTED,
        target_type="user_google_integration",
        target_id=integration.id,
        actor_email=integration.google_email,
        metadata={
            "user_id": user_id,
            "google_email": integration.google_email,
            "reconnect": was_reconnect,
        },
    )

    # PR-OAuth-Permisos-Admin Item 13. Sincronizar los Send-As aliases
    # desde Gmail al (re)conectar — refleja el ★ default real para que
    # el handler del backfill no skipee al user. Best-effort: un fallo
    # NO debe abortar el OAuth.
    if any("gmail" in s for s in result.scopes):
        try:
            from app.integrations.gmail.aliases import (  # noqa: PLC0415
                sync_send_as_aliases,
            )

            synced = sync_send_as_aliases(session, user_id=user_id)
            record_event(
                session,
                action=Action.GMAIL_ALIASES_SYNCED,
                target_type="user_google_integration",
                target_id=integration.id,
                actor_email=integration.google_email,
                metadata={"user_id": user_id, "synced_count": synced},
            )
            logger.info(
                "gmail.aliases_synced user_id=%s count=%d", user_id, synced
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "gmail.aliases_sync_failed user_id=%s", user_id, exc_info=True
            )

    # Auto-register the Gmail Push Notifications watch the first
    # time the user grants gmail.modify. Before this fix, replies
    # never reached the CRM because the watch was only created via
    # a manual `register_watch` call.
    gmail_modify = "https://www.googleapis.com/auth/gmail.modify"
    new_scopes = set(result.scopes)
    if gmail_modify in new_scopes and gmail_modify not in previous_scopes:
        try:
            from app.integrations.gmail.service import (  # noqa: PLC0415
                register_watch,
            )

            register_watch(session, user_id=user_id)
            logger.info(
                "gmail.watch.auto_registered user_id=%s", user_id
            )
        except Exception:  # noqa: BLE001
            # Watch failure must NOT abort the OAuth flow — the
            # user can retry from /account if needed. Log + carry on.
            logger.warning(
                "gmail.watch.auto_register_failed user_id=%s",
                user_id,
                exc_info=True,
            )

    return integration


def disconnect_user(session: Session, *, user_id: str) -> bool:
    """PR-OAuth-Permisos-Admin Item 12. El user pulsa "Desconectar
    Google". Antes BORRABA la fila (perdiendo histórico + config). Ahora
    revoca el grant en Google (best effort), pone a NULL los tokens por
    privacidad, y MARCA `status='disconnected_by_user'` conservando la
    fila para histórico + audit. Returns True si había integración."""
    integration = get_integration(session, user_id)
    if integration is None:
        return False
    _revoke_tokens(integration)

    from app.core.audit import Action, record_event  # noqa: PLC0415

    audit = record_event(
        session,
        action=Action.GMAIL_DISCONNECTED_BY_USER,
        target_type="user_google_integration",
        target_id=integration.id,
        actor_email=integration.google_email,
        metadata={"user_id": user_id, "google_email": integration.google_email},
    )
    session.flush()  # para tener audit.id

    integration.status = "disconnected_by_user"
    # Tokens a NULL por privacidad — la fila sigue para histórico, pero
    # ya no guardamos credenciales de una integración desconectada.
    integration.access_token_encrypted = ""
    integration.refresh_token_encrypted = ""
    integration.disconnect_audit_id = audit.id
    session.flush()
    return True


def mark_needs_reconnect(
    session: Session, *, user_id: str, error: str
) -> UserGoogleIntegration | None:
    """PR-OAuth-Permisos-Admin Item 12. Llamado cuando un refresh falla
    de forma permanente (invalid_grant). En lugar de borrar la fila la
    marca `needs_reconnect` + registra el error + audit log. Devuelve la
    fila (o None si no existía). Idempotente: si ya está en
    needs_reconnect no duplica el audit."""
    integration = get_integration(session, user_id)
    if integration is None:
        return None
    if integration.status == "needs_reconnect":
        # Ya marcada — solo refrescamos el timestamp del último error.
        integration.last_refresh_error = error[:255]
        integration.last_refresh_error_at = datetime.now(UTC)
        session.flush()
        return integration

    from app.core.audit import Action, record_event  # noqa: PLC0415

    audit = record_event(
        session,
        action=Action.GMAIL_REFRESH_FAILED_PERMANENT,
        target_type="user_google_integration",
        target_id=integration.id,
        actor_email=integration.google_email,
        metadata={
            "user_id": user_id,
            "google_email": integration.google_email,
            "error": error,
        },
    )
    session.flush()
    integration.status = "needs_reconnect"
    integration.last_refresh_error = error[:255]
    integration.last_refresh_error_at = datetime.now(UTC)
    integration.disconnect_audit_id = audit.id
    session.flush()
    logger.warning(
        "gmail.refresh_failed_permanent user_id=%s error=%s — marcado "
        "needs_reconnect (NO borrado)",
        user_id, error,
    )
    return integration


def _revoke_tokens(integration: UserGoogleIntegration) -> None:
    """Best-effort POST to Google's revoke endpoint. Failures are
    logged but never propagated — the local row goes away regardless."""
    import httpx  # noqa: PLC0415

    from app.core.crypto import decrypt as _decrypt  # noqa: PLC0415

    try:
        refresh_token = _decrypt(integration.refresh_token_encrypted)
    except Exception:  # noqa: BLE001
        return
    try:
        httpx.post(
            "https://oauth2.googleapis.com/revoke",
            data={"token": refresh_token},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=10.0,
        )
    except Exception:  # noqa: BLE001
        logger.info(
            "google_calendar.revoke_failed user_id=%s", integration.user_id
        )


def set_calendar(
    session: Session,
    *,
    user_id: str,
    calendar_id: str,
) -> UserGoogleIntegration:
    """Persist the calendar the user picked in the setup screen.

    Validates that the id is one of the user's actual calendars (so
    arbitrary strings can't end up in the DB) and stores the summary
    so we can show it without an extra API call on every page load.
    """
    integration = get_integration(session, user_id)
    if integration is None:
        raise GoogleNotConnectedError("Google Calendar no está conectado.")
    client = GoogleCalendarClient(session, integration)
    calendars = client.list_calendars()
    match = next((c for c in calendars if c["id"] == calendar_id), None)
    if match is None:
        raise InvalidCalendarError(
            f"El calendario {calendar_id!r} no pertenece a la cuenta conectada."
        )
    integration.selected_calendar_id = match["id"]
    integration.selected_calendar_summary = match["summary"]
    session.flush()
    return integration


# ---------------------------------------------------------------------------
# Task → event sync
# ---------------------------------------------------------------------------


def sync_task_to_calendar(
    session: Session, task: Task, *, all_day: bool = False
) -> Task:
    """Mirror a task as a Google Calendar event in the assignee's
    selected calendar. No-op (logged) when the assignee isn't
    connected, has no calendar picked, or the API call fails.

    `all_day=True` produces an all-day event using the date of
    `task.due_at` in the calendar's timezone — used by the workflow
    `action_create_task` step when no specific time is chosen.
    """
    integration = get_integration(session, task.assigned_user_id)
    if integration is None or integration.selected_calendar_id is None:
        logger.info(
            "google_calendar.sync_skip task_id=%s reason=not_configured",
            task.id,
        )
        return task
    body = _event_body_for_task(task, all_day=all_day)
    try:
        event = GoogleCalendarClient(session, integration).create_event(
            integration.selected_calendar_id, body
        )
    except GoogleAuthExpiredError:
        logger.warning(
            "google_calendar.sync_auth_expired task_id=%s user_id=%s",
            task.id,
            integration.user_id,
        )
        # PR-OAuth-Permisos-Admin Item 12. Marcar, no borrar.
        mark_needs_reconnect(
            session, user_id=integration.user_id, error="invalid_grant"
        )
        return task
    except Exception:  # noqa: BLE001
        logger.warning(
            "google_calendar.sync_failed task_id=%s",
            task.id,
            exc_info=True,
        )
        return task
    task.google_event_id = event.get("id")
    task.google_calendar_id = integration.selected_calendar_id
    integration.last_sync_at = datetime.now(UTC)
    session.flush()
    return task


def update_task_event(session: Session, task: Task) -> Task:
    """Patch the existing event with the task's current fields. If
    the task was never synced (no `google_event_id`), this is a
    no-op."""
    if not task.google_event_id or not task.google_calendar_id:
        return task
    integration = get_integration(session, task.assigned_user_id)
    if integration is None:
        return task
    try:
        GoogleCalendarClient(session, integration).update_event(
            task.google_calendar_id,
            task.google_event_id,
            _event_body_for_task(task),
        )
    except GoogleAuthExpiredError:
        logger.warning(
            "google_calendar.update_auth_expired task_id=%s", task.id
        )
        mark_needs_reconnect(
            session, user_id=integration.user_id, error="invalid_grant"
        )
        return task
    except Exception:  # noqa: BLE001
        logger.warning(
            "google_calendar.update_failed task_id=%s",
            task.id,
            exc_info=True,
        )
        return task
    integration.last_sync_at = datetime.now(UTC)
    session.flush()
    return task


def delete_task_event(session: Session, task: Task) -> None:
    """Delete the event from Google Calendar. Errors are swallowed:
    the local delete must succeed regardless."""
    if not task.google_event_id or not task.google_calendar_id:
        return
    integration = get_integration(session, task.assigned_user_id)
    if integration is None:
        return
    try:
        GoogleCalendarClient(session, integration).delete_event(
            task.google_calendar_id, task.google_event_id
        )
    except GoogleAuthExpiredError:
        logger.warning(
            "google_calendar.delete_auth_expired task_id=%s", task.id
        )
        mark_needs_reconnect(
            session, user_id=integration.user_id, error="invalid_grant"
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "google_calendar.delete_failed task_id=%s",
            task.id,
            exc_info=True,
        )


_DONE_PREFIX = "✓ "


def _event_summary(task: Task) -> str:
    """Compute the event title. When the task is done we prefix `✓ `
    so the operator sees completion state from the calendar. Strip a
    pre-existing prefix from `task.title` first so a manual rename
    after completion doesn't double-up the mark."""
    from app.models.crm import TaskStatus  # noqa: PLC0415

    base = task.title or ""
    if base.startswith(_DONE_PREFIX):
        base = base[len(_DONE_PREFIX):]
    if task.status == TaskStatus.DONE:
        return f"{_DONE_PREFIX}{base}"
    return base


def _event_body_for_task(
    task: Task, *, all_day: bool = False
) -> dict[str, object]:
    """Build the Google Calendar event payload from a Task.

    Layout: title in `summary`, deep link back to the CRM appended to
    the description, start/end based on `due_at` (defaults to a
    30-minute slot — tasks have no end time today). When the task is
    done the title is prefixed with `✓ ` so completion is visible in
    the calendar without extra columns.

    `all_day=True` ignores the time-of-day in `due_at` and emits a
    `date` range — used by the workflow create-task step when no
    explicit hour is given.
    """
    settings = get_settings()
    tz = settings.google_calendar_timezone
    summary = _event_summary(task)
    if task.due_at is None:
        # An all-day event makes the most sense for a "no date"
        # task — Google requires a date range though, so pick "today".
        today = datetime.now(UTC).date()
        return {
            "summary": summary,
            "description": _build_description(task),
            "start": {"date": today.isoformat()},
            "end": {"date": (today + timedelta(days=1)).isoformat()},
        }
    start = task.due_at
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    if all_day:
        # Google's all-day events use a half-open [start_date, end_date)
        # range. Resolve to the calendar's configured TZ so the date
        # the operator picked maps to the date shown in Google.
        try:
            from zoneinfo import ZoneInfo  # noqa: PLC0415

            local_date = start.astimezone(ZoneInfo(tz)).date()
        except Exception:  # noqa: BLE001 — fallback to UTC date
            local_date = start.date()
        return {
            "summary": summary,
            "description": _build_description(task),
            "start": {"date": local_date.isoformat()},
            "end": {"date": (local_date + timedelta(days=1)).isoformat()},
        }
    duration = timedelta(minutes=settings.google_calendar_default_event_minutes)
    end = start + duration
    body: dict[str, object] = {
        "summary": summary,
        "description": _build_description(task),
        "start": {"dateTime": start.isoformat(), "timeZone": tz},
        "end": {"dateTime": end.isoformat(), "timeZone": tz},
    }
    if task.reminder_minutes_before is not None:
        body["reminders"] = {
            "useDefault": False,
            "overrides": [
                {"method": "popup", "minutes": task.reminder_minutes_before}
            ],
        }
    return body


def _build_description(task: Task) -> str:
    base = (task.description or "").rstrip()
    settings = get_settings()
    frontend = (
        getattr(settings, "frontend_base_url", "http://localhost:3000") or ""
    ).rstrip("/")
    link = f"{frontend}/tasks#task-{task.id}" if frontend else f"task-{task.id}"
    footer = f"\n\n— Tarea CRM: {link}"
    return f"{base}{footer}".lstrip()


# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------


class GoogleNotConnectedError(RuntimeError):
    """Raised when an op requires a connected Google account but the
    row is missing."""


class InvalidCalendarError(ValueError):
    """Raised when the user tries to pick a calendar that doesn't
    belong to the connected Google account."""


__all__ = [
    "GOOGLE_OAUTH_SCOPES",
    "GoogleNotConnectedError",
    "InvalidCalendarError",
    "connect_user",
    "consume_oauth_state",
    "delete_task_event",
    "disconnect_user",
    "get_integration",
    "issue_oauth_state",
    "set_calendar",
    "sync_task_to_calendar",
    "update_task_event",
]
