"""OAuth helpers for the Google Calendar integration.

These wrap `google_auth_oauthlib.flow.Flow` so the rest of the app
stays unaware of the upstream protocol. Three call sites use them:

- `/api/integrations/google/authorize` → `get_authorize_url(state)`
- `/api/integrations/google/callback` → `exchange_code_for_tokens(code)`
- `service.connect_user` → idem

The scopes are minimal: `calendar.readonly` for listing the user's
calendars in the post-OAuth setup screen, and `calendar.events` for
creating/updating/deleting events in the calendar the user picked.
`userinfo.email` lets us show "Cuenta: bart@bomedia.net" without an
extra call.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException, status

from app.core.config import get_settings

# Imported lazily — google-auth-oauthlib pulls in cryptography which is
# heavy, and we don't want to crash at app boot if the package is
# missing (the tests use mocks). The functions below import what they
# need at call time.

GOOGLE_OAUTH_SCOPES: tuple[str, ...] = (
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
)


@dataclass(frozen=True)
class OAuthExchangeResult:
    google_email: str
    access_token: str
    refresh_token: str
    expires_at: datetime
    scopes: list[str]


def _ensure_configured() -> None:
    settings = get_settings()
    if not settings.google_calendar_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "La integración con Google Calendar no está configurada por "
                "el administrador. Pide al admin que defina "
                "GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET y "
                "GOOGLE_OAUTH_REDIRECT_URI."
            ),
        )


def _client_config() -> dict[str, Any]:
    settings = get_settings()
    return {
        "web": {
            "client_id": settings.google_oauth_client_id,
            "client_secret": settings.google_oauth_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [settings.google_oauth_redirect_uri],
        }
    }


def build_oauth_flow(state: str | None = None) -> Any:
    """Instantiate a `google_auth_oauthlib.flow.Flow`.

    Lazy-imports the upstream library so the application boots even
    when `google-auth-oauthlib` is unavailable (e.g. running a
    minimal test environment that mocks this module entirely).
    """
    _ensure_configured()
    from google_auth_oauthlib.flow import Flow  # noqa: PLC0415

    settings = get_settings()
    flow = Flow.from_client_config(
        _client_config(),
        scopes=list(GOOGLE_OAUTH_SCOPES),
        state=state,
    )
    flow.redirect_uri = settings.google_oauth_redirect_uri
    return flow


def get_authorize_url(state: str) -> str:
    """Build the consent URL the user is redirected to.

    `access_type=offline` is what makes Google issue a refresh token —
    without it we'd need the user to re-consent every hour, which is
    not acceptable for a calendar sync. `prompt=consent` forces the
    consent screen on every authorize call so a user who revoked
    access from the Google side gets fresh tokens instead of an
    invisible failure.
    """
    flow = build_oauth_flow(state=state)
    url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    return url


def exchange_code_for_tokens(code: str, state: str) -> OAuthExchangeResult:
    """Exchange the auth code Google handed us for access+refresh tokens.

    Also pulls the authenticated Google email from the ID token so the
    UI can display "Cuenta: bart@bomedia.net" without an extra
    userinfo round-trip.
    """
    flow = build_oauth_flow(state=state)
    flow.fetch_token(code=code)
    credentials = flow.credentials
    access_token = credentials.token
    refresh_token = credentials.refresh_token
    expires_at = (
        credentials.expiry.replace(tzinfo=UTC)
        if credentials.expiry is not None
        else datetime.now(UTC) + timedelta(minutes=55)
    )
    if not access_token or not refresh_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Google no devolvió un refresh_token. Revoca el acceso "
                "desde tu cuenta Google y vuelve a intentarlo."
            ),
        )
    google_email = _extract_email_from_id_token(credentials)
    return OAuthExchangeResult(
        google_email=google_email,
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=expires_at,
        scopes=list(credentials.scopes or GOOGLE_OAUTH_SCOPES),
    )


def _extract_email_from_id_token(credentials: Any) -> str:
    """Pull the verified email from the ID token returned alongside
    the access token. Falls back to an empty string + 400 to keep the
    UX explicit: the user *must* have a verifiable Google email."""
    id_token = getattr(credentials, "id_token", None)
    if id_token:
        try:
            from google.oauth2 import id_token as id_token_lib  # noqa: PLC0415
            from google.auth.transport import requests as g_requests  # noqa: PLC0415

            settings = get_settings()
            info = id_token_lib.verify_oauth2_token(
                id_token,
                g_requests.Request(),
                settings.google_oauth_client_id,
            )
            email = info.get("email")
            if email:
                return str(email)
        except Exception:  # noqa: BLE001
            # Fall through to a userinfo fetch below.
            pass
    # Userinfo fallback — for any reason the ID token wasn't usable,
    # ask Google directly.
    import httpx  # noqa: PLC0415

    response = httpx.get(
        "https://openidconnect.googleapis.com/v1/userinfo",
        headers={"Authorization": f"Bearer {credentials.token}"},
        timeout=10.0,
    )
    if response.status_code == 200:
        data = response.json()
        email = data.get("email")
        if email:
            return str(email)
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="No se pudo obtener el email de Google asociado a la cuenta.",
    )
