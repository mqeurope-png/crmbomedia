"""Pydantic schemas for the contact-notes endpoints.

Post-unification (migration 0049): la fuente backend es la tabla
`notes`, pero el shape del endpoint mantiene `content` (no `body`)
por backwards-compat con el frontend pre-unification. Añade los
`external_*` para que la UI pinte el autor remoto en las notas
importadas de Agile.
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
    external_author_name: str | None = None
    external_author_email: str | None = None
    external_created_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
