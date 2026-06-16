"""CRUD endpoints para `contact_assignments` (multi-comercial).

Sprint Reglas-Assign — PR-B. La ficha y el bulk operan sobre estos
endpoints, que delegan los invariantes (1 primary, recompute del
caché owner_user_id) en `repositories.assignments`.

  GET     /api/contacts/{id}/assignments
  POST    /api/contacts/{id}/assignments           (require manager)
  POST    /api/contacts/{id}/assignments/{aid}/promote
  DELETE  /api/contacts/{id}/assignments/{aid}     (require manager)

Autorización (decisión §1 del spec):
- TODOS los endpoints: require_user — un comercial puede auto-
  asignarse o asignar a otro. El bulk legacy /api/contacts/bulk-action
  mantiene su política manager-only por compatibilidad histórica (es
  una operación masiva, no la asignación normal "1 contacto").

Cada mutación escribe una audit row con la actor/target/source para
trazar quién asignó a quién y por qué (manual vs rule:<id>).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.core.audit import Action, record_event
from app.core.auth import require_user
from app.core.errors import not_found
from app.db.session import get_session
from app.models.crm import Contact, User
from app.repositories import assignments as assignments_repo
from app.schemas.assignments import (
    AssignmentUserRef,
    ContactAssignmentRead,
    ContactAssignmentWrite,
)

router = APIRouter(prefix="/api/contacts", tags=["contact-assignments"])


def _require_contact(session: Session, contact_id: str) -> Contact:
    contact = session.get(Contact, contact_id)
    if contact is None:
        raise not_found("Contact")
    return contact


def _serialise(session: Session, row) -> ContactAssignmentRead:
    user = session.get(User, row.user_id)
    if user is None:
        # Defensive: FK CASCADE debería haber borrado la assignment,
        # pero si por algún motivo el user desaparece sin la cascada,
        # devolvemos un placeholder en lugar de 500.
        user_ref = AssignmentUserRef(
            id=row.user_id, email="(usuario borrado)", is_active=False
        )
    else:
        user_ref = AssignmentUserRef.model_validate(user)
    return ContactAssignmentRead(
        id=row.id,
        contact_id=row.contact_id,
        user_id=row.user_id,
        user=user_ref,
        is_primary=row.is_primary,
        source=row.source,
        rule_id=row.rule_id,
        notes=row.notes,
        assigned_by_user_id=row.assigned_by_user_id,
        assigned_at=row.assigned_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get(
    "/{contact_id}/assignments",
    response_model=list[ContactAssignmentRead],
)
def list_assignments(
    contact_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> list[ContactAssignmentRead]:
    _ = current_user
    _require_contact(session, contact_id)
    rows = assignments_repo.list_for_contact(session, contact_id)
    return [_serialise(session, r) for r in rows]


@router.post(
    "/{contact_id}/assignments",
    response_model=ContactAssignmentRead,
    status_code=201,
)
def create_assignment(
    contact_id: str,
    payload: ContactAssignmentWrite,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> ContactAssignmentRead:
    _require_contact(session, contact_id)
    user = session.get(User, payload.user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El usuario indicado no existe o está inactivo.",
        )
    previous_primary = _primary_user_id(session, contact_id)
    row = assignments_repo.add_assignment(
        session,
        contact_id=contact_id,
        user_id=payload.user_id,
        is_primary=payload.is_primary,
        assigned_by_user_id=current_user.id,
        source="manual",
        notes=payload.notes,
    )
    _audit_change(
        session,
        action=Action.CONTACT_ASSIGNMENT_ADDED,
        contact_id=contact_id,
        actor=current_user,
        request=request,
        metadata={
            "assignment_id": row.id,
            "user_id": payload.user_id,
            "is_primary": payload.is_primary,
            "source": "manual",
        },
    )
    if payload.is_primary and previous_primary != payload.user_id:
        _audit_change(
            session,
            action=Action.CONTACT_PRIMARY_CHANGED,
            contact_id=contact_id,
            actor=current_user,
            request=request,
            metadata={
                "from_user_id": previous_primary,
                "to_user_id": payload.user_id,
            },
        )
    session.commit()
    session.refresh(row)
    return _serialise(session, row)


@router.post(
    "/{contact_id}/assignments/{assignment_id}/promote",
    response_model=ContactAssignmentRead,
)
def promote_assignment(
    contact_id: str,
    assignment_id: str,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> ContactAssignmentRead:
    _require_contact(session, contact_id)
    previous_primary = _primary_user_id(session, contact_id)
    row = assignments_repo.set_primary(
        session, contact_id=contact_id, assignment_id=assignment_id
    )
    if row is None:
        raise not_found("ContactAssignment")
    if previous_primary != row.user_id:
        _audit_change(
            session,
            action=Action.CONTACT_PRIMARY_CHANGED,
            contact_id=contact_id,
            actor=current_user,
            request=request,
            metadata={
                "from_user_id": previous_primary,
                "to_user_id": row.user_id,
                "assignment_id": row.id,
            },
        )
    session.commit()
    session.refresh(row)
    return _serialise(session, row)


@router.delete(
    "/{contact_id}/assignments/{assignment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_assignment(
    contact_id: str,
    assignment_id: str,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> Response:
    _require_contact(session, contact_id)
    row = assignments_repo.get_assignment(session, assignment_id)
    if row is None or row.contact_id != contact_id:
        raise not_found("ContactAssignment")
    was_primary = row.is_primary
    removed_user_id = row.user_id
    assignments_repo.remove_assignment(session, row)
    _audit_change(
        session,
        action=Action.CONTACT_ASSIGNMENT_REMOVED,
        contact_id=contact_id,
        actor=current_user,
        request=request,
        metadata={
            "assignment_id": assignment_id,
            "user_id": removed_user_id,
            "was_primary": was_primary,
        },
    )
    if was_primary:
        # Tras borrar el primary el contacto queda sin responsable hasta
        # que el operador promueva otra fila o cree una nueva.
        _audit_change(
            session,
            action=Action.CONTACT_PRIMARY_CHANGED,
            contact_id=contact_id,
            actor=current_user,
            request=request,
            metadata={
                "from_user_id": removed_user_id,
                "to_user_id": None,
            },
        )
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _primary_user_id(session: Session, contact_id: str) -> str | None:
    contact = session.get(Contact, contact_id)
    return contact.owner_user_id if contact else None


def _audit_change(
    session: Session,
    *,
    action: str,
    contact_id: str,
    actor: User,
    request: Request,
    metadata: dict[str, Any],
) -> None:
    record_event(
        session,
        action=action,
        target_type="contact",
        target_id=contact_id,
        actor=actor,
        metadata=metadata,
        request=request,
    )
