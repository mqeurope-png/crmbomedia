"""Pydantic schemas for the contact-notes endpoints.

Sprint Empresas — sub-PR 4/4. The "Notas" section on the ficha
reads/writes through `/api/contacts/{id}/notes`; the schemas
mirror the storage model except for `created_by_user_id`, which
the backend sets from the auth context (never trusted from the
client).
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ContactNoteWrite(BaseModel):
    content: str = Field(min_length=1)
    pinned: bool = False


class ContactNoteRead(BaseModel):
    id: str
    contact_id: str
    content: str
    source: str
    pinned: bool
    created_by_user_id: str | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
