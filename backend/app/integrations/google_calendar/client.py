"""Thin wrapper around `googleapiclient.discovery.build`.

The client carries the per-user `UserGoogleIntegration` row so it can
decrypt+refresh tokens transparently. Calls return raw dicts (no
schema layer here — schemas live next to the API).

Refresh semantics:
- Before any call, if `token_expires_at < now + 60s`, do a refresh
  round-trip with the stored refresh_token. The fresh access token +
  new expiry are persisted to the row.
- If Google replies 401 mid-call, try one refresh + retry. If the
  refresh itself fails with `invalid_grant`, mark the row as
  disconnected (the caller deletes it).

The client is a per-request object; never cache it across users.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.crypto import decrypt, encrypt
from app.models.crm import UserGoogleIntegration

logger = logging.getLogger(__name__)


class GoogleAuthExpiredError(RuntimeError):
    """Raised when the stored refresh token is no longer valid.

    The caller is expected to wipe the row and ask the user to
    reconnect from `/account`.
    """


class GoogleCalendarClient:
    """Per-user Google Calendar API facade.

    Instantiated for the lifetime of one request. The session is held
    so token refreshes can be persisted in the same transaction the
    caller is using.
    """

    def __init__(
        self,
        session: Session,
        integration: UserGoogleIntegration,
    ) -> None:
        self._session = session
        self._integration = integration
        self._service: Any | None = None

    # ------------------------------------------------------------------
    # Service builder + refresh

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
            "calendar",
            "v3",
            credentials=credentials,
            cache_discovery=False,
        )
        return self._service

    def _ensure_fresh_token(self) -> None:
        """Refresh if the stored token is within 60 s of expiry."""
        token_expires_at = self._integration.token_expires_at
        if token_expires_at.tzinfo is None:
            token_expires_at = token_expires_at.replace(tzinfo=UTC)
        if token_expires_at - datetime.now(UTC) > timedelta(seconds=60):
            return
        self._refresh_token()

    def _refresh_token(self) -> None:
        """Force a token refresh and persist the result."""
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
                "google_calendar.refresh_failed user_id=%s",
                self._integration.user_id,
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
        # Drop the cached service so the next call rebuilds with the
        # fresh credentials.
        self._service = None

    # ------------------------------------------------------------------
    # Public API

    def list_calendars(self) -> list[dict[str, Any]]:
        """Return the calendar list trimmed to `(id, summary, primary,
        accessRole)` — everything the setup UI needs."""

        def _call() -> dict[str, Any]:
            service = self._build_service()
            return service.calendarList().list().execute()

        items = self._call_with_retry(_call).get("items", [])
        return [
            {
                "id": item["id"],
                "summary": item.get("summaryOverride") or item.get("summary", ""),
                "primary": bool(item.get("primary")),
                "access_role": item.get("accessRole"),
                "background_color": item.get("backgroundColor"),
            }
            for item in items
        ]

    def create_event(
        self, calendar_id: str, event_data: dict[str, Any]
    ) -> dict[str, Any]:
        def _call() -> dict[str, Any]:
            service = self._build_service()
            return (
                service.events()
                .insert(calendarId=calendar_id, body=event_data)
                .execute()
            )

        return self._call_with_retry(_call)

    def update_event(
        self,
        calendar_id: str,
        event_id: str,
        event_data: dict[str, Any],
    ) -> dict[str, Any]:
        def _call() -> dict[str, Any]:
            service = self._build_service()
            return (
                service.events()
                .patch(calendarId=calendar_id, eventId=event_id, body=event_data)
                .execute()
            )

        return self._call_with_retry(_call)

    def delete_event(self, calendar_id: str, event_id: str) -> None:
        def _call() -> None:
            service = self._build_service()
            service.events().delete(
                calendarId=calendar_id, eventId=event_id
            ).execute()

        self._call_with_retry(_call)

    # ------------------------------------------------------------------
    # Helpers

    def _call_with_retry(self, fn: Any) -> Any:
        """Execute `fn`; on 401 refresh once and retry."""
        from googleapiclient.errors import HttpError  # noqa: PLC0415

        try:
            return fn()
        except HttpError as exc:
            status_code = getattr(exc, "status_code", None) or getattr(
                exc.resp, "status", None
            )
            if status_code in (401, 403):
                try:
                    self._refresh_token()
                except GoogleAuthExpiredError:
                    raise
                return fn()
            raise
