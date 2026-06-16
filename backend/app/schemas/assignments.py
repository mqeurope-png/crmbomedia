"""Pydantic schemas para los endpoints de `contact_assignments`.

Sprint Reglas-Assign — PR-B. La sección "Comerciales asignados" de la
ficha lee/escribe vía `/api/contacts/{id}/assignments`. El backend
deriva `assigned_by_user_id` del contexto auth — nunca confía en el
cliente para esa columna.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AssignmentUserRef(BaseModel):
    """Mini-snapshot del usuario asignado, embebido en la respuesta
    para que la UI no tenga que cruzar con `/api/users` al pintar."""

    id: str
    email: str
    full_name: str | None = None
    is_active: bool

    model_config = ConfigDict(from_attributes=True)


class ContactAssignmentWrite(BaseModel):
    """POST `/api/contacts/{id}/assignments`."""

    user_id: str = Field(min_length=1)
    is_primary: bool = False
    notes: str | None = None


class ContactAssignmentRead(BaseModel):
    id: str
    contact_id: str
    user_id: str
    user: AssignmentUserRef
    is_primary: bool
    source: str
    rule_id: str | None
    notes: str | None
    assigned_by_user_id: str | None
    assigned_at: datetime
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
