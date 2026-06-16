"""Bulk-action endpoint for the contacts list.

Mini-PR C Fase 3. Surfaces a single POST /api/contacts/bulk-action
that handles every contact-list bulk operation the UI exposes:
reassign owner, add/remove tag, change commercial status, deactivate.

Limited to 1000 contacts per call — anything larger is paginated by
the client. Every action writes an audit row with the affected
contact ids in the metadata.

Brevo list push and segment creation deliberately stay out of this
endpoint because they need a real saved view to identify the cohort;
the UI sends those flows to the existing
`/api/contact-views/{id}/push-to-brevo` and
`/api/segments` endpoints respectively.
"""
from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import Action, record_event
from app.core.auth import require_admin, require_manager, require_user
from app.db.session import get_session
from app.models.crm import (
    Contact,
    ContactTag,
    Tag,
    User,
    UserRole,
)
from app.repositories import assignments as assignments_repo

router = APIRouter(prefix="/api/contacts", tags=["contacts"])
logger = logging.getLogger(__name__)

BulkAction = Literal[
    "assign_owner",
    "add_tag",
    "remove_tag",
    "change_status",
    "deactivate",
]

MAX_BULK_CONTACTS = 1000


class BulkActionPayload(BaseModel):
    contact_ids: list[str] = Field(min_length=1, max_length=MAX_BULK_CONTACTS)
    action: BulkAction
    payload: dict[str, Any] = Field(default_factory=dict)


@router.post("/bulk-action")
def bulk_action(
    body: BulkActionPayload,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> dict[str, Any]:
    """Run a single bulk action across the contact ids the caller
    sent. Returns `{action, affected_count, contact_ids}`.

    Authorisation:
    - `assign_owner` requires admin or manager.
    - `deactivate` requires admin.
    - The rest are open to any signed-in user (the `require_user`
      dep already excludes viewers).
    """
    _check_role_for(body.action, current_user)
    contacts = list(
        session.scalars(
            select(Contact).where(Contact.id.in_(body.contact_ids))
        )
    )
    if not contacts:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ningún contacto válido en la selección.",
        )
    affected = _dispatch(session, body, contacts)
    record_event(
        session,
        action=Action.CONTACT_TAGS_BULK_ACTION
        if body.action in ("add_tag", "remove_tag")
        else Action.CONTACT_UPDATED,
        target_type="contact",
        actor=current_user,
        metadata={
            "bulk_action": body.action,
            "affected_count": affected,
            "contact_ids": [c.id for c in contacts][:50],
            "payload_keys": sorted(body.payload.keys()),
        },
        request=request,
    )
    session.commit()
    return {
        "action": body.action,
        "affected_count": affected,
        "contact_ids": [c.id for c in contacts],
    }


def _check_role_for(action: BulkAction, user: User) -> None:
    if action == "assign_owner" and user.role not in (
        UserRole.ADMIN,
        UserRole.MANAGER,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo admin/manager puede reasignar contactos.",
        )
    if action == "deactivate" and user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo admin puede desactivar contactos en bulk.",
        )


def _dispatch(
    session: Session, body: BulkActionPayload, contacts: list[Contact]
) -> int:
    """Apply the action; return the number of rows actually touched."""
    if body.action == "assign_owner":
        owner_id = body.payload.get("owner_user_id")
        if not owner_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Falta `owner_user_id` en payload.",
            )
        owner = session.get(User, owner_id)
        if owner is None or not owner.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="El owner indicado no existe o está inactivo.",
            )
        # Sprint Reglas-Assign PR-B: el bulk legacy "assign_owner" ahora
        # mantiene el invariante multi-comercial — pasa por add_assignment
        # con is_primary=True, que demota la primary previa si la había y
        # recalcula el caché owner_user_id. La acción semántica sigue
        # siendo "fijar al responsable", no "borrar secundarios".
        n = 0
        for c in contacts:
            if c.owner_user_id == owner_id:
                # Ya era primary — nada que tocar.
                continue
            assignments_repo.add_assignment(
                session,
                contact_id=c.id,
                user_id=owner_id,
                is_primary=True,
                source="manual",
            )
            n += 1
        return n
    if body.action == "add_tag":
        tag = _require_tag(session, body.payload.get("tag_id"))
        n = 0
        existing = {
            (a.contact_id, a.tag_id)
            for a in session.scalars(
                select(ContactTag).where(
                    ContactTag.contact_id.in_([c.id for c in contacts]),
                    ContactTag.tag_id == tag.id,
                )
            )
        }
        for c in contacts:
            if (c.id, tag.id) in existing:
                continue
            session.add(ContactTag(contact_id=c.id, tag_id=tag.id))
            n += 1
        return n
    if body.action == "remove_tag":
        tag = _require_tag(session, body.payload.get("tag_id"))
        assignments = list(
            session.scalars(
                select(ContactTag).where(
                    ContactTag.contact_id.in_([c.id for c in contacts]),
                    ContactTag.tag_id == tag.id,
                )
            )
        )
        for a in assignments:
            session.delete(a)
        return len(assignments)
    if body.action == "change_status":
        new_status = body.payload.get("new_status")
        if not new_status:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Falta `new_status` en payload.",
            )
        n = 0
        for c in contacts:
            if c.commercial_status != new_status:
                c.commercial_status = new_status
                n += 1
        return n
    if body.action == "deactivate":
        n = 0
        for c in contacts:
            if c.is_active:
                c.is_active = False
                n += 1
        return n
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Acción bulk desconocida: {body.action!r}",
    )


def _require_tag(session: Session, tag_id: str | None) -> Tag:
    if not tag_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Falta `tag_id` en payload.",
        )
    tag = session.get(Tag, tag_id)
    if tag is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El tag indicado no existe.",
        )
    return tag


# Imports kept for explicit role-gate references upstream.
_ = require_admin
_ = require_manager
