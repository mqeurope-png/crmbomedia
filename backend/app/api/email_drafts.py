"""Operator-draft CRUD + send shortcut.

Sprint Email v2.4d. Mounted at `/api/email-drafts`. The composer
modal auto-saves on a timer; this module owns the persistence
side. Cross-user access is blocked at the route layer — owner
mismatch always returns 404 so probing by id doesn't leak
existence.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import require_user
from app.core.errors import not_found
from app.db.session import get_session
from app.models.crm import EmailDraft, User
from app.schemas.emails import (
    EmailDraftRead,
    EmailDraftWrite,
    EmailMessageRead,
    EmailSendRequest,
)

router = APIRouter(prefix="/api/email-drafts", tags=["emails-drafts"])


def _own(session: Session, draft_id: str, user: User) -> EmailDraft:
    draft = session.get(EmailDraft, draft_id)
    if draft is None or draft.user_id != user.id:
        raise not_found("EmailDraft")
    return draft


def _apply(draft: EmailDraft, payload: EmailDraftWrite, *, now: datetime) -> None:
    """Copy a write payload onto the draft. We intentionally
    overwrite every field (including back to NULL) so the
    auto-save behaves like a snapshot rather than a patch — the
    operator clears a recipient and we shouldn't keep the
    previous one stuck in the row."""
    draft.thread_id = payload.thread_id
    draft.contact_id = payload.contact_id
    draft.from_alias = payload.from_alias
    draft.from_name = payload.from_name
    draft.subject = payload.subject
    draft.body_html = payload.body_html
    draft.body_text = payload.body_text
    draft.to_emails_json = (
        json.dumps(payload.to_emails) if payload.to_emails else None
    )
    draft.cc_emails_json = (
        json.dumps(payload.cc_emails) if payload.cc_emails else None
    )
    draft.bcc_emails_json = (
        json.dumps(payload.bcc_emails) if payload.bcc_emails else None
    )
    draft.in_reply_to_message_id = payload.in_reply_to_message_id
    draft.signature_id = payload.signature_id
    draft.include_unsubscribe = payload.include_unsubscribe
    draft.scheduled_for = payload.scheduled_for
    draft.updated_at = now


@router.get("", response_model=list[EmailDraftRead])
def list_drafts(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> list[EmailDraftRead]:
    rows = list(
        session.scalars(
            select(EmailDraft)
            .where(EmailDraft.user_id == current_user.id)
            .order_by(EmailDraft.updated_at.desc())
        )
    )
    return [EmailDraftRead.model_validate(r) for r in rows]


@router.post("", response_model=EmailDraftRead, status_code=201)
def create_draft(
    payload: EmailDraftWrite,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> EmailDraftRead:
    now = datetime.now(UTC)
    draft = EmailDraft(user_id=current_user.id, created_at=now, updated_at=now)
    _apply(draft, payload, now=now)
    session.add(draft)
    session.commit()
    session.refresh(draft)
    return EmailDraftRead.model_validate(draft)


@router.get("/{draft_id}", response_model=EmailDraftRead)
def get_draft(
    draft_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> EmailDraftRead:
    return EmailDraftRead.model_validate(_own(session, draft_id, current_user))


@router.put("/{draft_id}", response_model=EmailDraftRead)
def update_draft(
    draft_id: str,
    payload: EmailDraftWrite,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> EmailDraftRead:
    draft = _own(session, draft_id, current_user)
    _apply(draft, payload, now=datetime.now(UTC))
    session.commit()
    session.refresh(draft)
    return EmailDraftRead.model_validate(draft)


@router.delete("/{draft_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_draft(
    draft_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> Response:
    draft = _own(session, draft_id, current_user)
    session.delete(draft)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{draft_id}/send", response_model=EmailMessageRead, status_code=201)
def send_draft(
    draft_id: str,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> EmailMessageRead:
    """Convert a draft into a real send. Delegates to the
    existing `/api/emails/send` handler so the alias-pref +
    unsubscribe + Gmail-scope guards stay in one place; the draft
    row is deleted only on success. A failed send leaves the
    draft intact so the operator can fix the issue and retry."""
    from app.api.emails import send_email as send_email_handler  # noqa: PLC0415

    draft = _own(session, draft_id, current_user)
    to_list = json.loads(draft.to_emails_json or "[]") or []
    cc_list = json.loads(draft.cc_emails_json or "[]") or None
    bcc_list = json.loads(draft.bcc_emails_json or "[]") or None
    if not draft.from_alias or not to_list:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "El borrador necesita un remitente y al menos un destinatario "
                "antes de enviarse."
            ),
        )

    payload = EmailSendRequest.model_validate(
        {
            "from_alias": draft.from_alias,
            "from_name": draft.from_name,
            "to": to_list,
            "cc": cc_list,
            "bcc": bcc_list,
            "subject": draft.subject or "",
            "body_html": draft.body_html,
            "body_text": draft.body_text,
            "contact_id": draft.contact_id,
            "in_reply_to_message_id": draft.in_reply_to_message_id,
            "include_unsubscribe": draft.include_unsubscribe,
            "scheduled_for": (
                draft.scheduled_for.isoformat() if draft.scheduled_for else None
            ),
        }
    )
    result = send_email_handler(
        payload, request, session=session, current_user=current_user
    )
    session.delete(draft)
    session.commit()
    return result
