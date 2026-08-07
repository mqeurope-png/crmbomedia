"""Pydantic schemas for the inbound alias registry (CRM-GMAIL Parte A)."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr


class UserEmailAliasRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    alias_email: str
    active: bool
    created_at: datetime
    updated_at: datetime


class UserEmailAliasCreate(BaseModel):
    alias_email: EmailStr


class UserEmailAliasUpdate(BaseModel):
    active: bool
