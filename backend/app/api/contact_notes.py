"""CRUD for contact notes.

Sprint Empresas — sub-PR 4/4. Backs the "Notas" section on the
contact ficha:

  GET     /api/contacts/{id}/notes
  POST    /api/contacts/{id}/notes
  PUT     /api/contacts/{id}/notes/{note_id}
  DELETE  /api/contacts/{id}/notes/{note_id}
  POST    /api/contacts/{id}/notes/{note_id}/pin
  POST    /api/contacts/{id}/notes/{note_id}/unpin

Pinned notes float to the top of the list; the dedicated `/pin`
and `/unpin` actions avoid having to PUT the whole row just to
flip a flag.

Imported rows (source `agile:Note{n}`) are editable by the
operator — once edited locally, `_apply_update` on the next Agile
sync skips them because the dedupe key matches the original
imported content; manual edits diverge and stay.
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import require_user
from app.core.errors import not_found
from app.db.session import get_session
from app.models.crm import Contact, ContactNote, User
from app.schemas.contact_notes import ContactNoteRead, ContactNoteWrite

router = APIRouter(prefix="/api/contacts", tags=["contact-notes"])


def _get_contact(
    session: Session, contact_id: str, user: User
) -> Contact:
    _ = user
    contact = session.get(Contact, contact_id)
    if contact is None:
        raise not_found("Contact")
    return contact


def _get_note(session: Session, contact_id: str, note_id: str) -> ContactNote:
    row = session.get(ContactNote, note_id)
    if row is None or row.contact_id != contact_id:
        raise not_found("ContactNote")
    return row


@router.get("/{contact_id}/notes", response_model=list[ContactNoteRead])
def list_notes(
    contact_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> list[ContactNoteRead]:
    _get_contact(session, contact_id, current_user)
    rows = list(
        session.scalars(
            select(ContactNote)
            .where(ContactNote.contact_id == contact_id)
            .order_by(
                ContactNote.pinned.desc(),
                ContactNote.created_at.desc(),
            )
        )
    )
    return [ContactNoteRead.model_validate(r) for r in rows]


@router.post(
    "/{contact_id}/notes",
    response_model=ContactNoteRead,
    status_code=201,
)
def create_note(
    contact_id: str,
    payload: ContactNoteWrite,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> ContactNoteRead:
    _get_contact(session, contact_id, current_user)
    content = payload.content.strip()
    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La nota no puede estar vacía.",
        )
    now = datetime.now(UTC)
    row = ContactNote(
        contact_id=contact_id,
        content=content,
        source="manual",
        pinned=payload.pinned,
        created_by_user_id=current_user.id,
    )
    row.created_at = now
    row.updated_at = now
    session.add(row)
    session.commit()
    session.refresh(row)
    return ContactNoteRead.model_validate(row)


@router.put(
    "/{contact_id}/notes/{note_id}", response_model=ContactNoteRead
)
def update_note(
    contact_id: str,
    note_id: str,
    payload: ContactNoteWrite,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> ContactNoteRead:
    _get_contact(session, contact_id, current_user)
    row = _get_note(session, contact_id, note_id)
    content = payload.content.strip()
    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La nota no puede estar vacía.",
        )
    row.content = content
    row.pinned = payload.pinned
    session.commit()
    session.refresh(row)
    return ContactNoteRead.model_validate(row)


@router.delete(
    "/{contact_id}/notes/{note_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_note(
    contact_id: str,
    note_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> Response:
    _get_contact(session, contact_id, current_user)
    row = _get_note(session, contact_id, note_id)
    session.delete(row)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{contact_id}/notes/{note_id}/pin", response_model=ContactNoteRead
)
def pin_note(
    contact_id: str,
    note_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> ContactNoteRead:
    _get_contact(session, contact_id, current_user)
    row = _get_note(session, contact_id, note_id)
    row.pinned = True
    session.commit()
    session.refresh(row)
    return ContactNoteRead.model_validate(row)


@router.post(
    "/{contact_id}/notes/{note_id}/unpin", response_model=ContactNoteRead
)
def unpin_note(
    contact_id: str,
    note_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> ContactNoteRead:
    _get_contact(session, contact_id, current_user)
    row = _get_note(session, contact_id, note_id)
    row.pinned = False
    session.commit()
    session.refresh(row)
    return ContactNoteRead.model_validate(row)
