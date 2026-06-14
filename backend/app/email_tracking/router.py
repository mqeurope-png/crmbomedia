"""Public tracking endpoints — opens, clicks, unsubscribes.

Every route in this module is mounted with NO authentication. The
URLs are obfuscated by a 32-byte URL-safe token; an attacker who
guesses one only burns an event row, never reads CRM data.

Endpoints:

- `GET  /api/email-track/open/{token}`      — 1x1 GIF, records open.
- `GET  /api/email-track/click/{token}`     — 302 to `?d=<b64>` URL.
- `GET  /api/unsubscribe/{token}`           — confirm-then-POST page.
- `POST /api/unsubscribe/{token}`           — RFC 8058 One-Click.
"""
from __future__ import annotations

import html as html_lib
import logging
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.models.crm import (
    Contact,
    ContactTag,
    EmailEventType,
    EmailUnsubscribe,
    EmailUnsubscribeScope,
    Tag,
)

from .services import (
    TRANSPARENT_GIF,
    b64url_decode,
    contact_is_unsubscribed,
    dedupe_event,
    lookup_message_by_token,
    lookup_unsubscribe_by_token,
    record_event,
)

router = APIRouter(prefix="/api", tags=["email-tracking"])
log = logging.getLogger(__name__)


def _client_ip(request: Request) -> str | None:
    """Honour `X-Forwarded-For` (nginx in front of api) but only take
    the leftmost address — the only one the client controls under
    the original request."""
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip() or None
    return request.client.host if request.client else None


UNSUBSCRIBED_TAG_NAME = "unsubscribed"


def _ensure_unsubscribed_tag(session: Session, contact_id: str) -> None:
    """Auto-add the `unsubscribed` tag to the contact. The tag is
    created on first use (same case-normalised dedup rule the manual
    tag UI uses) so the operator can filter `tag:unsubscribed` in the
    contacts list."""
    from sqlalchemy import select  # noqa: PLC0415

    normalised = UNSUBSCRIBED_TAG_NAME.lower()
    tag = session.scalar(
        select(Tag).where(Tag.name_normalized == normalised)
    )
    if tag is None:
        tag = Tag(
            name=UNSUBSCRIBED_TAG_NAME,
            name_normalized=normalised,
            color="#94a3b8",
            description=(
                "Asignado automáticamente cuando el contacto pulsó "
                "el botón de anular suscripción."
            ),
        )
        session.add(tag)
        session.flush()
    already = session.get(ContactTag, (contact_id, tag.id))
    if already is None:
        session.add(
            ContactTag(
                contact_id=contact_id,
                tag_id=tag.id,
                source="email-unsubscribe",
            )
        )


def _pixel_response() -> Response:
    return Response(
        content=TRANSPARENT_GIF,
        media_type="image/gif",
        headers={
            # Every open must count; the recipient client must NOT
            # serve a cached pixel from a previous read.
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@router.get("/email-track/open/{token}")
def track_open(
    token: str,
    request: Request,
    session: Session = Depends(get_session),
) -> Response:
    message = lookup_message_by_token(session, token)
    # We always return the pixel — bad tokens shouldn't surface as
    # broken-image icons in the recipient's inbox.
    if message is None:
        log.info("email-track: unknown open token %s", token)
        return _pixel_response()

    ip = _client_ip(request)
    if not dedupe_event(
        session,
        message_id=message.id,
        event_type=EmailEventType.OPEN,
        ip=ip,
    ):
        record_event(
            session,
            message_id=message.id,
            event_type=EmailEventType.OPEN,
            ip=ip,
            user_agent=request.headers.get("user-agent"),
        )
        session.commit()
    return _pixel_response()


@router.get("/email-track/click/{token}")
def track_click(
    token: str,
    request: Request,
    d: str = Query(..., description="Base64-url destination URL"),
    session: Session = Depends(get_session),
) -> Response:
    message = lookup_message_by_token(session, token)
    try:
        destination = b64url_decode(d)
    except ValueError:
        return HTMLResponse(
            "Bad request", status_code=status.HTTP_400_BAD_REQUEST
        )
    if not destination.lower().startswith(("http://", "https://")):
        return HTMLResponse(
            "Bad request", status_code=status.HTTP_400_BAD_REQUEST
        )
    if message is not None:
        ip = _client_ip(request)
        if not dedupe_event(
            session,
            message_id=message.id,
            event_type=EmailEventType.CLICK,
            ip=ip,
        ):
            record_event(
                session,
                message_id=message.id,
                event_type=EmailEventType.CLICK,
                ip=ip,
                user_agent=request.headers.get("user-agent"),
                metadata={"url": destination},
            )
            session.commit()
    return RedirectResponse(
        url=destination, status_code=status.HTTP_302_FOUND
    )


_UNSUB_PAGE = """<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <title>Anular suscripción</title>
  <style>
    body {{ font-family: -apple-system, sans-serif; max-width: 480px;
           margin: 80px auto; padding: 24px; color: #1e293b; }}
    h1 {{ font-size: 20px; margin: 0 0 12px; }}
    p  {{ font-size: 14px; line-height: 1.6; color: #475569; }}
    button {{ font: inherit; font-size: 14px; padding: 10px 18px;
              border-radius: 8px; border: 0; background: #1e293b;
              color: #fff; cursor: pointer; }}
    .ok {{ color: #047857; }}
  </style>
</head>
<body>
  <h1>{title}</h1>
  <p>{body}</p>
  {action}
</body>
</html>
"""


def _render_page(*, title: str, body: str, action: str = "") -> HTMLResponse:
    return HTMLResponse(
        _UNSUB_PAGE.format(title=title, body=body, action=action)
    )


@router.get("/unsubscribe/{token}")
def unsubscribe_page(
    token: str, session: Session = Depends(get_session)
) -> Response:
    message = lookup_message_by_token(session, token)
    already = lookup_unsubscribe_by_token(session, token)
    if already is not None:
        return _render_page(
            title="Ya estabas dado de baja",
            body=(
                "No volverás a recibir correos comerciales de este "
                "remitente. Si fue un error, escríbenos."
            ),
        )
    if message is None:
        return _render_page(
            title="Enlace caducado",
            body=(
                "Este enlace ya no es válido. Si quieres dejar de "
                "recibir nuestros correos, responde con la palabra "
                "BAJA en el asunto."
            ),
        )
    action = (
        f'<form method="POST" action="/api/unsubscribe/{html_lib.escape(token)}">'
        '<button type="submit">Anular suscripción</button>'
        "</form>"
    )
    return _render_page(
        title="¿Quieres dejar de recibir nuestros correos?",
        body=(
            "Pulsa el botón para confirmar. Sólo afecta a correos "
            "comerciales; podríamos seguir contactándote por temas "
            "estrictamente operativos."
        ),
        action=action,
    )


@router.post("/unsubscribe/{token}")
def unsubscribe_submit(
    token: str,
    request: Request,
    session: Session = Depends(get_session),
) -> Response:
    """Same endpoint serves the confirm-page POST and the RFC 8058
    One-Click request (Gmail / Outlook send a `List-Unsubscribe=One-Click`
    body). Either path arrives here and produces the same row."""
    already = lookup_unsubscribe_by_token(session, token)
    if already is not None:
        return _render_page(
            title="✓ Ya estabas dado de baja",
            body="Tu solicitud anterior sigue activa.",
        )
    message = lookup_message_by_token(session, token)
    if message is None or message.thread is None:
        return _render_page(
            title="Enlace caducado",
            body=(
                "Este enlace ya no es válido. Responde el correo "
                "original con la palabra BAJA si quieres darte de baja."
            ),
        )
    # The contact we tie the baja to is the thread's contact_id; we
    # fall back to looking up by the recipient address so an unscoped
    # thread doesn't silently lose the unsubscribe.
    contact_id = message.thread.contact_id
    if contact_id is None:
        recipient = (message.to_emails_json or "").lower()
        contact = session.scalar(
            Contact.__table__.select().where(  # type: ignore[attr-defined]
                Contact.email.in_(
                    [recipient.strip('"[]') if recipient else ""]
                )
            )
        )
        if contact is not None:
            contact_id = contact.id
    if contact_id is None:
        log.warning(
            "unsubscribe token %s has no contact to mark; recording event only",
            token,
        )
        # Still record an event on the message so the dashboard
        # surfaces "1 unsubscribe attempt".
        record_event(
            session,
            message_id=message.id,
            event_type=EmailEventType.UNSUBSCRIBE,
        )
        session.commit()
        return _render_page(
            title="✓ Te hemos desuscrito",
            body=(
                "Hemos registrado tu solicitud. No volverás a "
                "recibir correos comerciales nuestros."
            ),
        )
    is_one_click = (
        request.headers.get("list-unsubscribe", "").lower() == "one-click"
        or "list-unsubscribe=one-click"
        in (request.headers.get("content-type") or "").lower()
    )
    row = EmailUnsubscribe(
        id=str(uuid4()),
        contact_id=contact_id,
        scope=EmailUnsubscribeScope.MARKETING,
        source="one-click" if is_one_click else "confirm-page",
        token=token,
        unsubscribed_at=datetime.now(UTC),
        message_id=message.id,
    )
    session.add(row)
    _ensure_unsubscribed_tag(session, contact_id)
    record_event(
        session,
        message_id=message.id,
        event_type=EmailEventType.UNSUBSCRIBE,
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        metadata={"contact_id": contact_id},
    )
    session.commit()
    if is_one_click:
        return Response(status_code=status.HTTP_200_OK)
    return _render_page(
        title="✓ Te hemos desuscrito",
        body=(
            "No volverás a recibir correos comerciales nuestros. "
            "Gracias por habérnoslo dicho."
        ),
    )


_ = contact_is_unsubscribed  # exported for the send wrapper
