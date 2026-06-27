"""Google Calendar integration endpoints.

Five surfaces, all mounted under `/api/integrations/google`:

- `GET /authorize` — generate state, persist it in Redis, redirect
  the user to Google's consent screen.
- `GET /callback` — Google bounces back with `?code=&state=`. We
  consume the state, exchange the code for tokens, persist the row,
  and redirect to `/account/google-setup` so the user picks a
  calendar.
- `GET /status` — UI polls this on every render of `/account` to
  decide which CTA to show.
- `GET /calendars` — fed into the calendar picker.
- `PATCH /calendar` — persist the calendar id the user picked.
- `DELETE /disconnect` — revoke + drop the row.

Authorisation: every endpoint except `/callback` requires a viewer+
session — `/callback` happens in the user's browser before our
session cookies are necessarily refreshed, so we rely on the OAuth
`state` to bind the redemption back to the user that started the
flow.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.core.audit import Action, record_event
from app.core.auth import require_admin, require_viewer
from app.core.config import get_settings
from app.db.session import get_session
from app.integrations.google_calendar import service as google_service
from app.integrations.google_calendar.client import (
    GoogleAuthExpiredError,
    GoogleCalendarClient,
)
from app.integrations.google_calendar.oauth import (
    SCOPE_CALENDAR_EVENTS,
    SCOPE_CALENDAR_READONLY,
    SCOPE_GMAIL_MODIFY,
    SCOPE_GMAIL_SEND,
    SCOPE_GMAIL_SETTINGS,
    get_authorize_url,
)
from app.models.crm import User
from app.schemas.crm import (
    GoogleCalendarItem,
    GoogleCalendarSelection,
    GoogleCalendarSelectPayload,
    GoogleCalendarStatus,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/integrations/google", tags=["integrations"])


def _require_configured() -> None:
    """Refuse with 503 (not 500) when admin hasn't set the OAuth keys.

    The UI surfaces the message verbatim — that's why it's in Spanish.
    """
    if not get_settings().google_calendar_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "La integración con Google Calendar no está configurada "
                "por el administrador."
            ),
        )


@router.get("/scopes-status")
def scopes_status(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> dict[str, bool]:
    """Granular per-scope check — drives the reauth banner when an
    operator already authorised Calendar but not Gmail.

    Returns booleans for every scope the integration cares about.
    The UI compares against the union to decide what's missing.
    """
    integration = google_service.get_integration(session, current_user.id)
    granted: set[str] = set()
    if integration is not None and integration.scopes:
        granted = set(integration.scopes.split())
    return {
        "calendar_events": SCOPE_CALENDAR_EVENTS in granted,
        "calendar_readonly": SCOPE_CALENDAR_READONLY in granted,
        "gmail_send": SCOPE_GMAIL_SEND in granted,
        "gmail_modify": SCOPE_GMAIL_MODIFY in granted,
        "gmail_settings": SCOPE_GMAIL_SETTINGS in granted,
    }


@router.post("/refresh-watch")
def refresh_gmail_watch(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> dict[str, str]:
    """Manual fallback for the auto-register on OAuth. Useful when
    the OAuth callback's watch registration failed (transient
    quota, API outage, etc.) and the user wants to retry from
    `/account` without going through the full reauth flow again."""
    from app.integrations.gmail.service import (  # noqa: PLC0415
        GmailNotConnectedError,
        GmailScopeMissingError,
        register_watch,
    )

    try:
        register_watch(session, user_id=current_user.id)
    except GmailNotConnectedError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    except GmailScopeMissingError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        ) from exc
    except Exception as exc:
        logger.warning(
            "gmail.watch.refresh_failed user_id=%s", current_user.id, exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Gmail rejected the watch request: {exc}",
        ) from exc
    session.commit()
    return {"status": "registered"}


@router.get("/status", response_model=GoogleCalendarStatus)
def get_status(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> GoogleCalendarStatus:
    """Lightweight probe used by the UI to pick the right CTA.

    PR-OAuth-Google-Unificado. La conexión es org-wide; el calendario
    seleccionado es per-user (`user_calendar_prefs`)."""
    settings = get_settings()
    integration = google_service.get_org_integration(session)
    if integration is None:
        return GoogleCalendarStatus(
            configured=settings.google_calendar_configured,
            connected=False,
        )
    pref = google_service.get_calendar_pref(session, current_user.id)
    selected: GoogleCalendarSelection | None = None
    if pref is not None and pref.selected_calendar_id:
        selected = GoogleCalendarSelection(
            id=pref.selected_calendar_id,
            summary=pref.selected_calendar_summary,
        )
    # PR-OAuth-Permisos-Admin Items 9 + 12. Estado + caducidad para el
    # banner. token_expiring_soon solo aplica a integraciones activas.
    from datetime import UTC, datetime, timedelta  # noqa: PLC0415

    integ_status = getattr(integration, "status", "active")
    now = datetime.now(UTC)
    expiring_soon = False
    if integ_status == "active" and integration.token_expires_at is not None:
        exp = integration.token_expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=UTC)
        expiring_soon = now <= exp <= now + timedelta(hours=48)
    # PR-Hotfix-OAuth-Banner Bug 14. El banner amarillo SOLO debe activarse
    # por la caducidad del refresh_token (7 días), no por el access_token
    # (1h, se refresca solo). NULL = sin caducidad (app verificada) → nunca
    # expiring_soon.
    refresh_expiring_soon = False
    refresh_exp = getattr(integration, "refresh_token_expires_at", None)
    if integ_status == "active" and refresh_exp is not None:
        if refresh_exp.tzinfo is None:
            refresh_exp = refresh_exp.replace(tzinfo=UTC)
        refresh_expiring_soon = now <= refresh_exp <= now + timedelta(hours=48)
    # Si la integración no está activa, la UI debe ofrecer reconectar —
    # `connected=False` para reutilizar el CTA de conexión, pero
    # exponemos `status` para el banner específico.
    return GoogleCalendarStatus(
        configured=settings.google_calendar_configured,
        connected=integ_status == "active",
        google_email=integration.google_email,
        selected_calendar=selected,
        requires_calendar_selection=(
            integ_status == "active" and selected is None
        ),
        connected_at=integration.connected_at,
        last_sync_at=integration.last_sync_at,
        status=integ_status,
        token_expires_at=integration.token_expires_at,
        token_expiring_soon=expiring_soon,
        refresh_token_expires_at=refresh_exp,
        refresh_token_expiring_soon=refresh_expiring_soon,
    )


@router.get("/authorize")
def authorize(
    current_user: User = Depends(require_admin),
) -> dict[str, str]:
    """PR-OAuth-Google-Unificado. Solo el admin conecta la cuenta Google
    org-wide. Devuelve la URL de consentimiento; el SPA navega a ella."""
    _require_configured()
    state = google_service.issue_oauth_state(current_user.id)
    url = get_authorize_url(state)
    return {"url": url}


@router.get("/callback")
def callback(
    request: Request,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> RedirectResponse:
    """OAuth bounce target. Validates `state`, exchanges `code` for
    tokens, persists the row, redirects to the setup screen.

    `state` is also our user-id binding: the consent screen happens
    outside our auth session, so we trust the cached mapping rather
    than the request's Bearer token (which may not be present).
    """
    _require_configured()
    frontend_base = (
        get_settings().frontend_base_url.rstrip("/")
        if get_settings().frontend_base_url
        else ""
    )
    if error:
        return RedirectResponse(
            url=f"{frontend_base}/account?google_error={error}",
            status_code=status.HTTP_302_FOUND,
        )
    if not code or not state:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Faltan parámetros code/state en la respuesta de Google.",
        )
    user_id = google_service.consume_oauth_state(state)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "El estado de OAuth ha caducado o no es válido. "
                "Inicia la conexión otra vez desde /account."
            ),
        )
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Usuario no encontrado.",
        )
    integration = google_service.connect_org(
        session, connected_by_user_id=user_id, code=code, state=state
    )
    record_event(
        session,
        action=Action.GOOGLE_CALENDAR_CONNECTED,
        target_type="org_google_integration",
        target_id=integration.id,
        actor=user,
        metadata={"google_email": integration.google_email},
        request=request,
    )
    session.commit()
    # PR-OAuth-Google-Unificado. Tras conectar la cuenta org, el admin
    # vuelve a /admin/integrations. Cada user elige su calendario aparte.
    return RedirectResponse(
        url=f"{frontend_base}/admin/integrations?google_connected=1",
        status_code=status.HTTP_302_FOUND,
    )


@router.get("/calendars", response_model=list[GoogleCalendarItem])
def list_calendars(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> list[GoogleCalendarItem]:
    """PR-OAuth-Google-Unificado. Lista los calendarios de la cuenta org
    para que cada user elija el suyo."""
    _require_configured()
    _ = current_user
    integration = google_service.get_org_integration(session)
    if integration is None or integration.status != "active":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Google no está conectado.",
        )
    try:
        calendars = GoogleCalendarClient(session, integration).list_calendars()
    except GoogleAuthExpiredError as exc:
        google_service.mark_needs_reconnect(session, error="invalid_grant")
        session.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="La cuenta Google ha revocado el acceso. Avisa al admin.",
        ) from exc
    session.commit()
    return [GoogleCalendarItem(**item) for item in calendars]


@router.patch("/calendar", response_model=GoogleCalendarStatus)
def select_calendar(
    payload: GoogleCalendarSelectPayload,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> GoogleCalendarStatus:
    """PR-OAuth-Google-Unificado. Persiste el calendario que el user
    eligió (per-user) validándolo contra la cuenta org."""
    _require_configured()
    try:
        pref = google_service.set_calendar(
            session,
            user_id=current_user.id,
            calendar_id=payload.calendar_id,
        )
    except google_service.GoogleNotConnectedError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    except google_service.InvalidCalendarError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    except GoogleAuthExpiredError as exc:
        google_service.mark_needs_reconnect(session, error="invalid_grant")
        session.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="La cuenta Google ha revocado el acceso. Avisa al admin.",
        ) from exc
    integration = google_service.get_org_integration(session)
    record_event(
        session,
        action=Action.GOOGLE_CALENDAR_SELECTED,
        target_type="user_calendar_pref",
        target_id=current_user.id,
        actor=current_user,
        metadata={
            "calendar_id": pref.selected_calendar_id,
            "calendar_summary": pref.selected_calendar_summary,
        },
        request=request,
    )
    session.commit()
    return GoogleCalendarStatus(
        configured=True,
        connected=True,
        google_email=integration.google_email if integration else None,
        selected_calendar=GoogleCalendarSelection(
            id=pref.selected_calendar_id or "",
            summary=pref.selected_calendar_summary,
        ),
        requires_calendar_selection=False,
        connected_at=integration.connected_at if integration else None,
        last_sync_at=integration.last_sync_at if integration else None,
        status=integration.status if integration else None,
    )


@router.delete("/disconnect")
def disconnect(
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> dict[str, str]:
    """PR-OAuth-Google-Unificado. Solo el admin desconecta la cuenta
    Google org. Marca la fila disconnected_by_user (no borra)."""
    removed = google_service.disconnect_org(
        session, actor_user_id=current_user.id
    )
    if removed:
        record_event(
            session,
            action=Action.GOOGLE_CALENDAR_DISCONNECTED,
            target_type="org_google_integration",
            actor=current_user,
            request=request,
        )
    session.commit()
    return {"message": "disconnected" if removed else "not_connected"}
