"""Pydantic schemas for the per-user email signatures surface."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SignatureRead(BaseModel):
    id: str
    name: str
    html_content: str
    is_default: bool
    sort_order: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SignatureWrite(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    html_content: str = Field(min_length=1)
    is_default: bool = False
    sort_order: int = 0
