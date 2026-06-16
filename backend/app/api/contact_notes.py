"""CRUD for contact notes (post-unification migration 0049).

  GET     /api/contacts/{id}/notes
  POST    /api/contacts/{id}/notes
  PUT     /api/contacts/{id}/notes/{note_id}
  DELETE  /api/contacts/{id}/notes/{note_id}
  POST    /api/contacts/{id}/notes/{note_id}/pin
  POST    /api/contacts/{id}/notes/{note_id}/unpin

Pinned notes float to the top of the list. Reads from the unified
`notes` table (previously had two tables: `notes` for Agile timeline
imports and `contact_notes` for Note1..Note10 + manual; merged in
2026-06-16 — `contact_notes` borrada).

The list endpoint joins external_author_* for imported rows so the UI
can show "Scott Dörflein, marzo 2026" on rows brought in from the
AgileCRM timeline.
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import require_user
from app.core.errors import not_found
from app.db.session import get_session
from app.models.crm import Contact, Note, User
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


def _get_note(session: Session, contact_id: str, note_id: str) -> Note:
    row = session.get(Note, note_id)
    if row is None or row.contact_id != contact_id:
        raise not_found("ContactNote")
    return row


def _serialise(row: Note) -> ContactNoteRead:
    return ContactNoteRead(
        id=row.id,
        contact_id=row.contact_id,
        content=row.body,
        source=row.source,
        pinned=row.pinned,
        created_by_user_id=row.created_by_user_id,
        external_author_name=row.external_author_name,
        external_author_email=row.external_author_email,
        external_created_at=row.external_created_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("/{contact_id}/notes", response_model=list[ContactNoteRead])
def list_notes(
    contact_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> list[ContactNoteRead]:
    _get_contact(session, contact_id, current_user)
    rows = list(
        session.scalars(
            select(Note)
            .where(Note.contact_id == contact_id)
            .order_by(
                Note.pinned.desc(),
                # Display by the remote date when present (Agile
                # timeline notes have a real `external_created_at`),
                # falling back to our import timestamp otherwise.
                Note.external_created_at.desc().nullslast(),
                Note.created_at.desc(),
            )
        )
    )
    return [_serialise(r) for r in rows]


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
    row = Note(
        contact_id=contact_id,
        body=content,
        source="manual",
        pinned=payload.pinned,
        created_by_user_id=current_user.id,
        author_user_id=current_user.id,
    )
    row.created_at = now
    row.updated_at = now
    session.add(row)
    session.commit()
    session.refresh(row)
    return _serialise(row)


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
    row.body = content
    row.pinned = payload.pinned
    session.commit()
    session.refresh(row)
    return _serialise(row)


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
    return _serialise(row)


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
    return _serialise(row)
