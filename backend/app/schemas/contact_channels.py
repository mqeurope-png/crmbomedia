"""Pydantic schemas for the contact-phones endpoints.

Sprint Empresas — sub-PR 3/4 (post-revert). The parallel
`/api/contacts/{id}/emails` collection was dropped — contacts
only carry one email (the UNIQUE `contacts.email` column) in
practice. Brevo's `EMAIL_SECUNDARIO` / `EMAIL2` land in
`custom_fields` JSON via the v2.4 whitelist and surface in the
ficha's "Datos adicionales" section as informational only.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ContactPhoneWrite(BaseModel):
    label: str | None = Field(default=None, max_length=80)
    number: str = Field(min_length=1, max_length=80)
    is_primary: bool = False
    source: str = Field(default="manual", max_length=40)


class ContactPhoneRead(BaseModel):
    id: str
    contact_id: str
    label: str | None
    number: str
    is_primary: bool
    source: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
