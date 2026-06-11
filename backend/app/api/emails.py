"""Email endpoints — send, list threads, thread detail, admin list.

Sprint Email v1. Mounted at `/api/emails`.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.audit import Action, record_event
from app.core.auth import require_admin, require_user
from app.core.errors import not_found
from app.db.session import get_session
from app.integrations.gmail import service as gmail_service
from app.integrations.gmail.service import (
    GmailNotConnectedError,
    GmailScopeMissingError,
)
from app.models.crm import (
    EmailMessage,
    EmailThread,
    User,
    UserEmailAliasPref,
    UserRole,
)
from app.schemas.emails import (
    AliasPreferencesPayload,
    EmailAlias,
    EmailMessageRead,
    EmailSendRequest,
    EmailThreadDetail,
    EmailThreadList,
    EmailThreadRead,
    MyAlias,
)

router = APIRouter(prefix="/api/emails", tags=["emails"])
logger = logging.getLogger(__name__)


def _emit_activity(
    session: Session,
    *,
    contact_id: str | None,
    event_type: str,
    subject: str | None,
    metadata: dict[str, str | int | None],
    occurred_at: datetime,
    body: str | None = None,
) -> None:
    """Mirror an email mutation into the contact's activity timeline
    when we know which contact it belongs to."""
    if not contact_id:
        return
    from app.models.crm import ActivityEvent  # noqa: PLC0415

    session.add(
        ActivityEvent(
            contact_id=contact_id,
            system="crm",
            account_id="emails",
            external_id=(
                f"email:{metadata.get('message_id') or metadata.get('thread_id')}"
                f":{event_type}"
            ),
            event_type=event_type,
            subject=(subject or "")[:200],
            body=(body or "")[:200] or None,
            metadata_json=json.dumps(metadata, default=str),
            occurred_at=occurred_at,
            synced_at=datetime.now(UTC),
        )
    )


@router.get("/aliases", response_model=list[EmailAlias])
def list_aliases(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> list[EmailAlias]:
    """All verified "Send mail as" aliases from Gmail, enriched
    with the current user's CRM preferences."""
    try:
        items = gmail_service.list_aliases(session, current_user.id)
    except GmailNotConnectedError:
        return []
    except GmailScopeMissingError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        ) from exc
    prefs = _prefs_index(session, current_user.id)
    return [
        EmailAlias(
            send_as_email=item["send_as_email"],
            display_name=item["display_name"],
            is_primary=item["is_primary"],
            is_default=item["is_default"],
            verification_status=item.get("verification_status"),
            user_pref_allowed=prefs.get(item["send_as_email"], (False, False))[0],
            user_pref_default=prefs.get(item["send_as_email"], (False, False))[1],
        )
        for item in items
    ]


@router.get("/my-aliases", response_model=list[MyAlias])
def my_aliases(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> list[MyAlias]:
    """Only the aliases the user marked as allowed, used by the
    composer dropdown. Cross-checked against the live Gmail list
    so an alias removed from Gmail doesn't keep showing up."""
    try:
        items = gmail_service.list_aliases(session, current_user.id)
    except (GmailNotConnectedError, GmailScopeMissingError):
        return []
    available = {it["send_as_email"]: it for it in items}
    prefs = _prefs_index(session, current_user.id)
    out: list[MyAlias] = []
    for alias_email, (allowed, is_default) in prefs.items():
        if not allowed:
            continue
        if alias_email not in available:
            continue
        out.append(
            MyAlias(
                send_as_email=alias_email,
                display_name=available[alias_email]["display_name"],
                is_default=is_default,
            )
        )
    out.sort(key=lambda a: (not a.is_default, a.send_as_email))
    return out


@router.put("/aliases/preferences", response_model=list[EmailAlias])
def upsert_alias_preferences(
    payload: AliasPreferencesPayload,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> list[EmailAlias]:
    """Upsert the user's alias preferences in one shot.

    Semantics:
    - `is_allowed=true` upserts the row.
    - `is_allowed=false` deletes the row (keep the table clean).
    - Setting `is_default=true` on one row demotes the other
      defaults to false inside the same transaction.
    """
    existing = {
        p.alias_email: p
        for p in session.scalars(
            select(UserEmailAliasPref).where(
                UserEmailAliasPref.user_id == current_user.id
            )
        )
    }
    incoming_default: str | None = None
    for item in payload.preferences:
        row = existing.get(item.alias_email)
        if not item.is_allowed:
            if row is not None:
                session.delete(row)
            continue
        if item.is_default:
            incoming_default = item.alias_email
        if row is None:
            session.add(
                UserEmailAliasPref(
                    user_id=current_user.id,
                    alias_email=item.alias_email,
                    is_allowed=True,
                    is_default=item.is_default,
                )
            )
        else:
            row.is_allowed = True
            row.is_default = item.is_default
    session.flush()
    if incoming_default is not None:
        session.execute(
            UserEmailAliasPref.__table__.update()  # type: ignore[attr-defined]
            .where(
                UserEmailAliasPref.user_id == current_user.id,
                UserEmailAliasPref.alias_email != incoming_default,
            )
            .values(is_default=False)
        )
    _ = request
    session.commit()
    # Re-render the enriched list so the UI doesn't need a second
    # round-trip.
    return list_aliases(session=session, current_user=current_user)


def _prefs_index(
    session: Session, user_id: str
) -> dict[str, tuple[bool, bool]]:
    rows = session.scalars(
        select(UserEmailAliasPref).where(
            UserEmailAliasPref.user_id == user_id
        )
    )
    return {r.alias_email: (r.is_allowed, r.is_default) for r in rows}


@router.post("/send", response_model=EmailMessageRead, status_code=201)
def send_email(
    payload: EmailSendRequest,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> EmailMessageRead:
    # The user must have Gmail connected with the send scope before
    # we even bother to check preferences — `_client_for` inside
    # gmail_service.send_email raises GmailScopeMissingError, but
    # we'd rather catch the scope problem here than return the
    # less-helpful pref error.
    from app.integrations.gmail.service import _client_for  # noqa: PLC0415

    try:
        _client_for(session, current_user.id)
    except GmailNotConnectedError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    except GmailScopeMissingError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        ) from exc

    # Validate that the alias is in the user's allowed preferences.
    # Blocks an operator from spoofing an alias their colleague
    # configured but they didn't opt into.
    pref = session.scalar(
        select(UserEmailAliasPref).where(
            UserEmailAliasPref.user_id == current_user.id,
            UserEmailAliasPref.alias_email == payload.from_alias,
            UserEmailAliasPref.is_allowed.is_(True),
        )
    )
    if pref is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "El alias seleccionado no está en tus preferencias. "
                "Márcalo desde /account."
            ),
        )
    try:
        message = gmail_service.send_email(
            session,
            sender_user_id=current_user.id,
            from_alias=payload.from_alias,
            from_name=payload.from_name,
            to=list(payload.to),
            cc=list(payload.cc) if payload.cc else None,
            bcc=list(payload.bcc) if payload.bcc else None,
            subject=payload.subject,
            body_html=payload.body_html,
            body_text=payload.body_text,
            contact_id=payload.contact_id,
            in_reply_to_message_id=payload.in_reply_to_message_id,
        )
    except GmailNotConnectedError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    except GmailScopeMissingError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        ) from exc

    _emit_activity(
        session,
        contact_id=payload.contact_id,
        event_type="email.sent_from_crm",
        subject=payload.subject,
        body=message.snippet or (payload.body_text or "")[:200] or None,
        metadata={
            "message_id": message.id,
            "thread_id": message.thread_id,
            "direction": "outbound",
            "from_email": message.from_email,
            "to": ", ".join(payload.to)[:200],
            "snippet": message.snippet or "",
        },
        occurred_at=message.sent_at,
    )
    record_event(
        session,
        action=Action.EMAIL_SENT_FROM_CRM,
        target_type="email_message",
        target_id=message.id,
        actor=current_user,
        metadata={
            "to": list(payload.to),
            "thread_id": message.thread_id,
            "contact_id": payload.contact_id,
        },
        request=request,
    )
    session.commit()
    session.refresh(message)
    return _message_read(message)


@router.get("/threads", response_model=EmailThreadList)
def list_threads(
    contact_id: str | None = Query(default=None),
    q: str | None = Query(default=None, description="LIKE on subject / from / snippet"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> EmailThreadList:
    stmt = select(EmailThread)
    if contact_id:
        stmt = stmt.where(EmailThread.contact_id == contact_id)
    if current_user.role not in (UserRole.ADMIN, UserRole.MANAGER):
        stmt = stmt.where(EmailThread.initiated_by_user_id == current_user.id)
    if q:
        # LIKE-match the term against the thread subject OR any
        # message's from_email / subject / snippet / body_text.
        # MySQL collates case-insensitively by default; SQLite (CI)
        # needs the `ilike` analog.
        from sqlalchemy import or_ as _or  # noqa: PLC0415

        like = f"%{q}%"
        msg_match = select(EmailMessage.thread_id).where(
            _or(
                EmailMessage.from_email.ilike(like),
                EmailMessage.subject.ilike(like),
                EmailMessage.snippet.ilike(like),
                EmailMessage.body_text.ilike(like),
            )
        )
        stmt = stmt.where(
            _or(
                EmailThread.subject.ilike(like),
                EmailThread.id.in_(msg_match),
            )
        )
    total = int(
        session.scalar(
            select(func.count()).select_from(stmt.subquery())
        )
        or 0
    )
    items = list(
        session.scalars(
            stmt.order_by(EmailThread.last_message_at.desc())
            .offset(offset)
            .limit(limit)
        )
    )
    # Last message per thread — driven the "Remitente último" +
    # "Vista previa" columns the v2.1 list view exposes. One small
    # extra query per page, batched via `id IN (...)`.
    last_by_thread = _latest_messages(session, [t.id for t in items])
    out: list[EmailThreadRead] = []
    for thread in items:
        last = last_by_thread.get(thread.id)
        read = EmailThreadRead.model_validate(thread)
        if last is not None:
            read.last_message_direction = (
                last.direction.value
                if hasattr(last.direction, "value")
                else str(last.direction)
            )
            read.last_message_from = last.from_email
            read.last_message_snippet = last.snippet or _snippet_from_body(
                last.body_text, last.body_html
            )
        out.append(read)
    return EmailThreadList(items=out, total=total)


@router.get("/threads/{thread_id}", response_model=EmailThreadDetail)
def thread_detail(
    thread_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> EmailThreadDetail:
    thread = session.scalar(
        select(EmailThread)
        .where(EmailThread.id == thread_id)
        .options(selectinload(EmailThread.messages))
    )
    if thread is None:
        raise not_found("EmailThread")
    if (
        current_user.role not in (UserRole.ADMIN, UserRole.MANAGER)
        and thread.initiated_by_user_id != current_user.id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tienes permiso para ver este hilo.",
        )
    return EmailThreadDetail(
        **EmailThreadRead.model_validate(thread).model_dump(),
        messages=[_message_read(m) for m in thread.messages],
    )


@router.post("/threads/{thread_id}/mark-read")
def mark_read(
    thread_id: str,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> dict[str, str]:
    thread = session.get(EmailThread, thread_id)
    if thread is None:
        raise not_found("EmailThread")
    now = datetime.now(UTC)
    session.execute(
        EmailMessage.__table__.update()  # type: ignore[attr-defined]
        .where(
            EmailMessage.thread_id == thread_id,
            EmailMessage.direction == "inbound",
            EmailMessage.read_at.is_(None),
        )
        .values(read_at=now)
    )
    thread.has_unread_replies = False
    record_event(
        session,
        action=Action.EMAIL_THREAD_MARKED_READ,
        target_type="email_thread",
        target_id=thread.id,
        actor=current_user,
        request=request,
    )
    session.commit()
    return {"message": "marked_read"}


@router.get("/activity")
def email_activity(
    scope: str = Query(default="mine", pattern="^(mine|all)$"),
    limit: int = Query(default=5, ge=1, le=50),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> list[dict[str, Any]]:
    """Recent CRM email activity for the dashboard widget.

    Returns flat items `{type, thread_id, subject, contact_id,
    contact_name, occurred_at, snippet, direction}` sorted by
    `occurred_at` desc. `scope=mine` filters to threads the
    current user initiated OR whose Gmail account belongs to
    them. `scope=all` is admin-only — other roles get their own
    activity regardless of what they passed.
    """
    from app.models.crm import Contact  # noqa: PLC0415

    stmt = select(EmailMessage, EmailThread, Contact).join(
        EmailThread, EmailMessage.thread_id == EmailThread.id
    ).outerjoin(Contact, Contact.id == EmailMessage.contact_id)
    if scope == "mine" or current_user.role not in (
        UserRole.ADMIN,
        UserRole.MANAGER,
    ):
        from sqlalchemy import or_ as _or  # noqa: PLC0415

        stmt = stmt.where(
            _or(
                EmailThread.initiated_by_user_id == current_user.id,
                EmailThread.gmail_account_user_id == current_user.id,
            )
        )
    stmt = stmt.order_by(EmailMessage.sent_at.desc()).limit(limit)
    rows = list(session.execute(stmt).all())
    out: list[dict[str, Any]] = []
    for msg, thread, contact in rows:
        snippet = msg.snippet or _snippet_from_body(msg.body_text, msg.body_html)
        contact_name = None
        if contact is not None:
            contact_name = (
                " ".join(
                    p for p in (contact.first_name, contact.last_name) if p
                )
                or contact.email
            )
        out.append(
            {
                "type": (
                    "email.sent_from_crm"
                    if msg.direction.value == "outbound"
                    else "email.reply_received"
                ),
                "direction": msg.direction.value,
                "thread_id": thread.id,
                "message_id": msg.id,
                "subject": thread.subject,
                "contact_id": contact.id if contact else None,
                "contact_name": contact_name,
                "from_email": msg.from_email,
                "occurred_at": msg.sent_at,
                "snippet": snippet,
            }
        )
    return out


@router.get("/admin/all-threads", response_model=EmailThreadList)
def admin_all_threads(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> EmailThreadList:
    _ = current_user
    stmt = select(EmailThread).order_by(EmailThread.last_message_at.desc())
    total = int(
        session.scalar(select(func.count()).select_from(EmailThread))
        or 0
    )
    items = list(session.scalars(stmt.offset(offset).limit(limit)))
    return EmailThreadList(
        items=[EmailThreadRead.model_validate(t) for t in items],
        total=total,
    )


def _message_read(m: EmailMessage) -> EmailMessageRead:
    return EmailMessageRead.model_validate(m)


def _latest_messages(
    session: Session, thread_ids: list[str]
) -> dict[str, EmailMessage]:
    """Return the most recent EmailMessage per thread id. Avoids
    the per-row N+1 the list view used to have."""
    if not thread_ids:
        return {}
    rows = session.scalars(
        select(EmailMessage)
        .where(EmailMessage.thread_id.in_(thread_ids))
        .order_by(EmailMessage.sent_at.desc())
    ).all()
    out: dict[str, EmailMessage] = {}
    for row in rows:
        if row.thread_id not in out:
            out[row.thread_id] = row
    return out


def _snippet_from_body(
    body_text: str | None, body_html: str | None
) -> str | None:
    """Derive a ~200-char snippet from the message body when the
    message didn't already carry a Gmail snippet. Strips HTML tags
    naively (no DOMPurify needed for a preview line)."""
    import re  # noqa: PLC0415

    if body_text:
        text = body_text.strip()
    elif body_html:
        text = re.sub(r"<[^>]+>", " ", body_html)
    else:
        return None
    flat = " ".join(text.split()).strip()
    return flat[:200] or None
