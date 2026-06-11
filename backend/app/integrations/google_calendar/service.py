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
    refresh) the row, return it."""
    result = exchange_code_for_tokens(code=code, state=state)
    integration = get_integration(session, user_id)
    now = datetime.now(UTC)
    if integration is None:
        integration = UserGoogleIntegration(
            user_id=user_id,
            google_email=result.google_email,
            access_token_encrypted=encrypt(result.access_token),
            refresh_token_encrypted=encrypt(result.refresh_token),
            token_expires_at=result.expires_at,
            scopes=" ".join(result.scopes),
            connected_at=now,
        )
        session.add(integration)
    else:
        integration.google_email = result.google_email
        integration.access_token_encrypted = encrypt(result.access_token)
        integration.refresh_token_encrypted = encrypt(result.refresh_token)
        integration.token_expires_at = result.expires_at
        integration.scopes = " ".join(result.scopes)
        integration.connected_at = now
        # Reset calendar selection so the user picks one for the new
        # connection — the old id may belong to a different account.
        integration.selected_calendar_id = None
        integration.selected_calendar_summary = None
    session.flush()
    return integration


def disconnect_user(session: Session, *, user_id: str) -> bool:
    """Revoke the OAuth grant on Google's side (best effort) and drop
    the row. Returns True when a row was actually removed."""
    integration = get_integration(session, user_id)
    if integration is None:
        return False
    _revoke_tokens(integration)
    session.delete(integration)
    session.flush()
    return True


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


def sync_task_to_calendar(session: Session, task: Task) -> Task:
    """Mirror a task as a Google Calendar event in the assignee's
    selected calendar. No-op (logged) when the assignee isn't
    connected, has no calendar picked, or the API call fails."""
    integration = get_integration(session, task.assigned_user_id)
    if integration is None or integration.selected_calendar_id is None:
        logger.info(
            "google_calendar.sync_skip task_id=%s reason=not_configured",
            task.id,
        )
        return task
    body = _event_body_for_task(task)
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
        session.delete(integration)
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
        session.delete(integration)
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
        session.delete(integration)
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


def _event_body_for_task(task: Task) -> dict[str, object]:
    """Build the Google Calendar event payload from a Task.

    Layout: title in `summary`, deep link back to the CRM appended to
    the description, start/end based on `due_at` (defaults to a
    30-minute slot — tasks have no end time today). When the task is
    done the title is prefixed with `✓ ` so completion is visible in
    the calendar without extra columns.
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
            "end": {"date": today.isoformat()},
        }
    start = task.due_at
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
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
