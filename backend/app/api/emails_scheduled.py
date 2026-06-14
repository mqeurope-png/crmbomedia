"""Scheduled-send endpoints — list, edit, cancel pending messages.

Sprint Email v2.4e. Mounted at `/api/emails/scheduled`. The actual
"schedule" verb piggy-backs on `/api/emails/send`: when a payload
ships `scheduled_for` in the future, the send route stops short of
the Gmail API and persists a `pending` row instead. This module
handles the operator-facing follow-ups (see in the list, edit,
cancel) and shares the same auth guards.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import require_user
from app.core.errors import not_found
from app.db.session import get_session
from app.models.crm import (
    EmailDirection,
    EmailMessage,
    EmailScheduledStatus,
    EmailThread,
    User,
)
from app.schemas.emails import EmailMessageRead, ScheduledMessageUpdate

router = APIRouter(prefix="/api/emails", tags=["emails-scheduled"])


def _pending_query(user_id: str | None):
    """Base SELECT for pending scheduled messages, optionally
    scoped to a user (None = admin view)."""
    stmt = select(EmailMessage).where(
        EmailMessage.scheduled_status == EmailScheduledStatus.PENDING.value,
    )
    if user_id is not None:
        stmt = stmt.where(EmailMessage.created_by_user_id == user_id)
    return stmt.order_by(EmailMessage.scheduled_for.asc())


def _ensure_pending_owned(
    session: Session, message_id: str, user: User
) -> EmailMessage:
    """Fetch a scheduled message the caller owns + is still pending.
    Anything else (sent, cancelled, foreign user) returns 404 so we
    don't leak existence of other operators' rows."""
    msg = session.get(EmailMessage, message_id)
    if msg is None or msg.scheduled_status != EmailScheduledStatus.PENDING.value:
        raise not_found("ScheduledMessage")
    if msg.created_by_user_id != user.id:
        # Treat foreign rows as "doesn't exist" — same shape as
        # `/api/emails/threads/{id}` for non-privileged callers.
        raise not_found("ScheduledMessage")
    return msg


@router.get("/scheduled", response_model=list[EmailMessageRead])
def list_scheduled(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> list[EmailMessageRead]:
    rows = list(session.scalars(_pending_query(current_user.id)))
    return [EmailMessageRead.model_validate(r) for r in rows]


@router.post("/scheduled/{message_id}/cancel", response_model=EmailMessageRead)
def cancel_scheduled(
    message_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> EmailMessageRead:
    msg = _ensure_pending_owned(session, message_id, current_user)
    msg.scheduled_status = EmailScheduledStatus.CANCELLED.value
    session.commit()
    session.refresh(msg)
    return EmailMessageRead.model_validate(msg)


@router.put("/scheduled/{message_id}", response_model=EmailMessageRead)
def update_scheduled(
    message_id: str,
    payload: ScheduledMessageUpdate,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> EmailMessageRead:
    msg = _ensure_pending_owned(session, message_id, current_user)
    if payload.scheduled_for is not None:
        target = (
            payload.scheduled_for
            if payload.scheduled_for.tzinfo
            else payload.scheduled_for.replace(tzinfo=UTC)
        )
        if target <= datetime.now(UTC):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="La fecha programada debe ser en el futuro.",
            )
        msg.scheduled_for = target
    if payload.subject is not None:
        msg.subject = payload.subject
    if payload.body_html is not None:
        msg.body_html = payload.body_html
    if payload.body_text is not None:
        msg.body_text = payload.body_text
    session.commit()
    session.refresh(msg)
    return EmailMessageRead.model_validate(msg)


def persist_scheduled_message(
    session: Session,
    *,
    sender_user_id: str,
    scheduled_for: datetime,
    from_alias: str,
    from_name: str | None,
    to: list[str],
    cc: list[str] | None,
    bcc: list[str] | None,
    subject: str,
    body_html: str | None,
    body_text: str | None,
    contact_id: str | None,
    in_reply_to_message_id: str | None,
) -> EmailMessage:
    """Create the pending `EmailMessage` row for a scheduled send.

    Shared by the `/send` route — exposed at module level so the
    endpoint stays a thin wrapper around payload validation.

    A fresh scheduled message uses a sentinel `gmail_thread_id` so
    the EmailThread row satisfies its NOT NULL constraint without
    pretending we have a real Gmail thread yet. The sweep replaces
    the sentinel with the real id once Gmail accepts the send. A
    reply (in_reply_to_message_id present) attaches to the parent's
    existing thread so the conversation is already linked.
    """
    annotated_scheduled = (
        scheduled_for
        if scheduled_for.tzinfo
        else scheduled_for.replace(tzinfo=UTC)
    )
    if annotated_scheduled <= datetime.now(UTC):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La fecha programada debe ser en el futuro.",
        )

    existing_thread: EmailThread | None = None
    if in_reply_to_message_id:
        parent = session.get(EmailMessage, in_reply_to_message_id)
        if parent is not None:
            existing_thread = session.get(EmailThread, parent.thread_id)

    if existing_thread is None:
        existing_thread = EmailThread(
            initiated_by_user_id=sender_user_id,
            gmail_account_user_id=sender_user_id,
            # Sentinel id — the sweep updates it with the real one
            # returned by Gmail. The unique constraint stays happy
            # because every sentinel embeds a fresh UUID.
            gmail_thread_id=f"pending:{uuid4()}",
            contact_id=contact_id,
            subject=subject,
            first_message_at=annotated_scheduled,
            last_message_at=annotated_scheduled,
            message_count=0,
        )
        session.add(existing_thread)
        session.flush()

    message = EmailMessage(
        thread_id=existing_thread.id,
        gmail_message_id=None,
        gmail_account_user_id=sender_user_id,
        direction=EmailDirection.OUTBOUND,
        from_email=from_alias,
        from_name=from_name,
        to_emails_json=json.dumps(to),
        cc_emails_json=json.dumps(cc) if cc else None,
        bcc_emails_json=json.dumps(bcc) if bcc else None,
        subject=subject,
        body_html=body_html,
        body_text=body_text,
        snippet=None,
        sent_at=None,
        contact_id=contact_id,
        created_by_user_id=sender_user_id,
        scheduled_for=annotated_scheduled,
        scheduled_status=EmailScheduledStatus.PENDING.value,
    )
    session.add(message)
    existing_thread.message_count = (existing_thread.message_count or 0) + 1
    existing_thread.last_message_at = annotated_scheduled
    session.flush()
    return message


__all__ = ["router", "persist_scheduled_message"]
