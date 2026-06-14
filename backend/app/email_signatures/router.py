"""CRUD + default-toggle for per-user email signatures."""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import require_user
from app.core.errors import not_found
from app.db.session import get_session
from app.models.crm import EmailSignature, User

from .schemas import SignatureRead, SignatureWrite

router = APIRouter(prefix="/api", tags=["email-signatures"])


def _now() -> datetime:
    return datetime.now(UTC)


def _clear_default(session: Session, user_id: str, exclude_id: str | None) -> None:
    """Unset is_default on every other signature this user owns.

    Run inside the same transaction as the write that sets a new
    default so a power user clicking "marcar como default" twice
    in a row can't leave the table with two defaults set.
    """
    others = session.scalars(
        select(EmailSignature).where(
            EmailSignature.user_id == user_id,
            EmailSignature.is_default.is_(True),
        )
    )
    for row in others:
        if exclude_id and row.id == exclude_id:
            continue
        row.is_default = False
        row.updated_at = _now()


def _load_owned(
    session: Session, signature_id: str, user: User
) -> EmailSignature:
    row = session.get(EmailSignature, signature_id)
    if row is None or row.user_id != user.id:
        raise not_found("EmailSignature")
    return row


@router.get("/email-signatures", response_model=list[SignatureRead])
def list_signatures(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> list[SignatureRead]:
    rows = list(
        session.scalars(
            select(EmailSignature)
            .where(EmailSignature.user_id == current_user.id)
            .order_by(
                EmailSignature.is_default.desc(),
                EmailSignature.sort_order,
                EmailSignature.name,
            )
        )
    )
    return [SignatureRead.model_validate(r) for r in rows]


@router.get(
    "/email-signatures/default",
    response_model=SignatureRead | None,
)
def get_default_signature(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> SignatureRead | None:
    """Returned by the send-modal at open time so the editor can
    auto-insert the operator's default. Null when none is set."""
    row = session.scalar(
        select(EmailSignature)
        .where(EmailSignature.user_id == current_user.id)
        .where(EmailSignature.is_default.is_(True))
    )
    return SignatureRead.model_validate(row) if row is not None else None


@router.post(
    "/email-signatures",
    response_model=SignatureRead,
    status_code=status.HTTP_201_CREATED,
)
def create_signature(
    payload: SignatureWrite,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> SignatureRead:
    now = _now()
    row = EmailSignature(
        user_id=current_user.id,
        name=payload.name.strip(),
        html_content=payload.html_content,
        is_default=payload.is_default,
        sort_order=payload.sort_order,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.flush()
    if payload.is_default:
        _clear_default(session, current_user.id, exclude_id=row.id)
    session.commit()
    session.refresh(row)
    return SignatureRead.model_validate(row)


@router.put(
    "/email-signatures/{signature_id}", response_model=SignatureRead
)
def update_signature(
    signature_id: str,
    payload: SignatureWrite,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> SignatureRead:
    row = _load_owned(session, signature_id, current_user)
    row.name = payload.name.strip()
    row.html_content = payload.html_content
    row.is_default = payload.is_default
    row.sort_order = payload.sort_order
    row.updated_at = _now()
    if payload.is_default:
        _clear_default(session, current_user.id, exclude_id=row.id)
    session.commit()
    session.refresh(row)
    return SignatureRead.model_validate(row)


@router.delete("/email-signatures/{signature_id}")
def delete_signature(
    signature_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> dict[str, str]:
    row = _load_owned(session, signature_id, current_user)
    session.delete(row)
    session.commit()
    return {"message": "deleted"}


@router.post(
    "/email-signatures/{signature_id}/default",
    response_model=SignatureRead,
)
def set_default_signature(
    signature_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> SignatureRead:
    """Mark this signature as the user's default, unsetting any other."""
    row = _load_owned(session, signature_id, current_user)
    row.is_default = True
    row.updated_at = _now()
    _clear_default(session, current_user.id, exclude_id=row.id)
    session.commit()
    session.refresh(row)
    return SignatureRead.model_validate(row)
