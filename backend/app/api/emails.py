"""Email endpoints — send, list threads, thread detail, admin list.

Sprint Email v1. Mounted at `/api/emails`.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

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
from app.models.crm import EmailMessage, EmailThread, User, UserRole
from app.schemas.emails import (
    EmailAlias,
    EmailMessageRead,
    EmailSendRequest,
    EmailThreadDetail,
    EmailThreadList,
    EmailThreadRead,
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
            external_id=f"email:{metadata.get('message_id') or metadata.get('thread_id')}:{event_type}",
            event_type=event_type,
            subject=(subject or "")[:200],
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
    """Verified "Send mail as" aliases the user can pick in the
    composer. Empty list when Gmail isn't connected."""
    try:
        items = gmail_service.list_aliases(session, current_user.id)
    except GmailNotConnectedError:
        return []
    except GmailScopeMissingError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        ) from exc
    return [EmailAlias(**item) for item in items]


@router.post("/send", response_model=EmailMessageRead, status_code=201)
def send_email(
    payload: EmailSendRequest,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> EmailMessageRead:
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
        metadata={
            "message_id": message.id,
            "thread_id": message.thread_id,
            "to": ", ".join(payload.to)[:200],
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
    return EmailThreadList(
        items=[EmailThreadRead.model_validate(t) for t in items],
        total=total,
    )


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
