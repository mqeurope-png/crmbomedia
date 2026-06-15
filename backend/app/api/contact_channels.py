"""Multi-channel CRUD for contact phones + emails.

Sprint Empresas — sub-PR 3/4. Two parallel resource collections
mounted under each contact:

  GET     /api/contacts/{id}/phones
  POST    /api/contacts/{id}/phones
  PUT     /api/contacts/{id}/phones/{phone_id}
  DELETE  /api/contacts/{id}/phones/{phone_id}
  POST    /api/contacts/{id}/phones/{phone_id}/primary

  GET     /api/contacts/{id}/emails
  POST    /api/contacts/{id}/emails
  PUT     /api/contacts/{id}/emails/{email_id}
  DELETE  /api/contacts/{id}/emails/{email_id}
  POST    /api/contacts/{id}/emails/{email_id}/primary

A dedicated `/primary` route avoids the operator having to PUT
the whole row just to flip the canonical flag — backend
guarantees only one primary per contact by clearing every other
row in a single statement before the flag goes on.
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.auth import require_user
from app.core.errors import not_found
from app.db.session import get_session
from app.models.crm import Contact, ContactEmail, ContactPhone, User
from app.schemas.contact_channels import (
    ContactEmailRead,
    ContactEmailWrite,
    ContactPhoneRead,
    ContactPhoneWrite,
)

router = APIRouter(prefix="/api/contacts", tags=["contact-channels"])


def _normalise_phone(raw: str) -> str:
    """Strip whitespace + low-noise punctuation so dedupe matches
    `+34 600 12 34 56` to `+34600123456`. Doesn't enforce a
    region — Brevo / Agile imports carry international numbers
    that wouldn't parse against ES rules."""
    return "".join(c for c in raw if c.isdigit() or c == "+")


def _normalise_email(raw: str) -> str:
    return raw.strip().lower()


def _get_contact(
    session: Session, contact_id: str, user: User
) -> Contact:
    _ = user
    contact = session.get(Contact, contact_id)
    if contact is None:
        raise not_found("Contact")
    return contact


def _set_primary(
    session: Session,
    table: type[ContactPhone] | type[ContactEmail],
    *,
    contact_id: str,
    row_id: str,
) -> None:
    """Clear is_primary across the contact's siblings, then set it
    on the targeted row. A single UPDATE per branch keeps the
    invariant atomic from the operator's perspective."""
    session.execute(
        update(table)
        .where(table.contact_id == contact_id, table.id != row_id)
        .values(is_primary=False)
    )
    session.execute(
        update(table).where(table.id == row_id).values(is_primary=True)
    )


# -- phones --------------------------------------------------------


@router.get("/{contact_id}/phones", response_model=list[ContactPhoneRead])
def list_phones(
    contact_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> list[ContactPhoneRead]:
    _get_contact(session, contact_id, current_user)
    rows = list(
        session.scalars(
            select(ContactPhone)
            .where(ContactPhone.contact_id == contact_id)
            .order_by(
                ContactPhone.is_primary.desc(),
                ContactPhone.created_at.asc(),
            )
        )
    )
    return [ContactPhoneRead.model_validate(r) for r in rows]


@router.post(
    "/{contact_id}/phones", response_model=ContactPhoneRead, status_code=201
)
def create_phone(
    contact_id: str,
    payload: ContactPhoneWrite,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> ContactPhoneRead:
    _get_contact(session, contact_id, current_user)
    normalised = _normalise_phone(payload.number)
    if not normalised:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El teléfono no contiene dígitos válidos.",
        )
    # Dedupe within the same contact: compare normalised digits.
    for existing in session.scalars(
        select(ContactPhone).where(ContactPhone.contact_id == contact_id)
    ):
        if _normalise_phone(existing.number) == normalised:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Este contacto ya tiene ese número.",
            )
    now = datetime.now(UTC)
    row = ContactPhone(
        contact_id=contact_id,
        label=payload.label,
        number=payload.number.strip(),
        is_primary=False,
        source=payload.source,
    )
    row.created_at = now
    row.updated_at = now
    session.add(row)
    session.flush()
    if payload.is_primary:
        _set_primary(session, ContactPhone, contact_id=contact_id, row_id=row.id)
    session.commit()
    session.refresh(row)
    return ContactPhoneRead.model_validate(row)


@router.put(
    "/{contact_id}/phones/{phone_id}", response_model=ContactPhoneRead
)
def update_phone(
    contact_id: str,
    phone_id: str,
    payload: ContactPhoneWrite,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> ContactPhoneRead:
    _get_contact(session, contact_id, current_user)
    row = session.get(ContactPhone, phone_id)
    if row is None or row.contact_id != contact_id:
        raise not_found("ContactPhone")
    row.label = payload.label
    row.number = payload.number.strip()
    row.source = payload.source
    if payload.is_primary:
        _set_primary(session, ContactPhone, contact_id=contact_id, row_id=row.id)
    session.commit()
    session.refresh(row)
    return ContactPhoneRead.model_validate(row)


@router.delete(
    "/{contact_id}/phones/{phone_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_phone(
    contact_id: str,
    phone_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> Response:
    _get_contact(session, contact_id, current_user)
    row = session.get(ContactPhone, phone_id)
    if row is None or row.contact_id != contact_id:
        raise not_found("ContactPhone")
    session.delete(row)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{contact_id}/phones/{phone_id}/primary", response_model=ContactPhoneRead
)
def set_primary_phone(
    contact_id: str,
    phone_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> ContactPhoneRead:
    _get_contact(session, contact_id, current_user)
    row = session.get(ContactPhone, phone_id)
    if row is None or row.contact_id != contact_id:
        raise not_found("ContactPhone")
    _set_primary(session, ContactPhone, contact_id=contact_id, row_id=phone_id)
    session.commit()
    session.refresh(row)
    return ContactPhoneRead.model_validate(row)


# -- emails --------------------------------------------------------


@router.get("/{contact_id}/emails", response_model=list[ContactEmailRead])
def list_emails(
    contact_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> list[ContactEmailRead]:
    _get_contact(session, contact_id, current_user)
    rows = list(
        session.scalars(
            select(ContactEmail)
            .where(ContactEmail.contact_id == contact_id)
            .order_by(
                ContactEmail.is_primary.desc(),
                ContactEmail.created_at.asc(),
            )
        )
    )
    return [ContactEmailRead.model_validate(r) for r in rows]


@router.post(
    "/{contact_id}/emails", response_model=ContactEmailRead, status_code=201
)
def create_email(
    contact_id: str,
    payload: ContactEmailWrite,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> ContactEmailRead:
    _get_contact(session, contact_id, current_user)
    normalised = _normalise_email(payload.email)
    if "@" not in normalised:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El email no tiene el formato esperado.",
        )
    for existing in session.scalars(
        select(ContactEmail).where(ContactEmail.contact_id == contact_id)
    ):
        if _normalise_email(existing.email) == normalised:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Este contacto ya tiene esa dirección.",
            )
    now = datetime.now(UTC)
    row = ContactEmail(
        contact_id=contact_id,
        label=payload.label,
        email=normalised,
        is_primary=False,
        is_verified=payload.is_verified,
        source=payload.source,
    )
    row.created_at = now
    row.updated_at = now
    session.add(row)
    session.flush()
    if payload.is_primary:
        _set_primary(session, ContactEmail, contact_id=contact_id, row_id=row.id)
    session.commit()
    session.refresh(row)
    return ContactEmailRead.model_validate(row)


@router.put(
    "/{contact_id}/emails/{email_id}", response_model=ContactEmailRead
)
def update_email(
    contact_id: str,
    email_id: str,
    payload: ContactEmailWrite,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> ContactEmailRead:
    _get_contact(session, contact_id, current_user)
    row = session.get(ContactEmail, email_id)
    if row is None or row.contact_id != contact_id:
        raise not_found("ContactEmail")
    row.label = payload.label
    row.email = _normalise_email(payload.email)
    row.is_verified = payload.is_verified
    row.source = payload.source
    if payload.is_primary:
        _set_primary(session, ContactEmail, contact_id=contact_id, row_id=row.id)
    session.commit()
    session.refresh(row)
    return ContactEmailRead.model_validate(row)


@router.delete(
    "/{contact_id}/emails/{email_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_email(
    contact_id: str,
    email_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> Response:
    _get_contact(session, contact_id, current_user)
    row = session.get(ContactEmail, email_id)
    if row is None or row.contact_id != contact_id:
        raise not_found("ContactEmail")
    session.delete(row)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{contact_id}/emails/{email_id}/primary",
    response_model=ContactEmailRead,
)
def set_primary_email(
    contact_id: str,
    email_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> ContactEmailRead:
    _get_contact(session, contact_id, current_user)
    row = session.get(ContactEmail, email_id)
    if row is None or row.contact_id != contact_id:
        raise not_found("ContactEmail")
    _set_primary(session, ContactEmail, contact_id=contact_id, row_id=email_id)
    session.commit()
    session.refresh(row)
    return ContactEmailRead.model_validate(row)
