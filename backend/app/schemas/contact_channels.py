"""Pydantic schemas + helpers for the multi-channel endpoints.

Sprint Empresas — sub-PR 3/4. `/api/contacts/{id}/phones` and
`/api/contacts/{id}/emails` carry these shapes.
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


class ContactEmailWrite(BaseModel):
    label: str | None = Field(default=None, max_length=80)
    email: str = Field(min_length=3, max_length=255)
    is_primary: bool = False
    is_verified: bool = False
    source: str = Field(default="manual", max_length=40)


class ContactEmailRead(BaseModel):
    id: str
    contact_id: str
    label: str | None
    email: str
    is_primary: bool
    is_verified: bool
    source: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
