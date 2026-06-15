"""Pydantic schemas for the /api/companies endpoints.

Sprint Empresas. JSON columns (`external_references_json`,
`custom_fields_json`) ship as decoded dicts via a `before`
validator so the API surface looks like a normal nested object.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)


class CompanyWrite(BaseModel):
    """Create / update payload. Every column is optional except
    `name`; the backfill + manual-create both go through this
    shape."""

    name: str = Field(min_length=1, max_length=300)
    website: str | None = Field(default=None, max_length=500)
    domain: str | None = Field(default=None, max_length=255)
    tax_id: str | None = Field(default=None, max_length=64)
    vat: str | None = Field(default=None, max_length=40)
    country: str | None = Field(default=None, max_length=120)
    region: str | None = Field(default=None, max_length=120)
    state: str | None = Field(default=None, max_length=200)
    city: str | None = Field(default=None, max_length=200)
    address_line: str | None = Field(default=None, max_length=500)
    postal_code: str | None = Field(default=None, max_length=20)
    sector: str | None = Field(default=None, max_length=120)
    size_category: str | None = Field(default=None, max_length=40)
    notes: str | None = None
    source: str = Field(default="manual", max_length=40)
    external_references: dict[str, Any] = Field(default_factory=dict)
    custom_fields: dict[str, Any] = Field(default_factory=dict)


class CompanyRead(BaseModel):
    id: str
    name: str
    website: str | None
    domain: str | None
    tax_id: str | None
    vat: str | None
    country: str | None
    region: str | None
    state: str | None
    city: str | None
    address_line: str | None
    postal_code: str | None
    sector: str | None
    size_category: str | None
    notes: str | None
    source: str
    is_active: bool
    external_references: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices(
            "external_references", "external_references_json"
        ),
    )
    custom_fields: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices(
            "custom_fields", "custom_fields_json"
        ),
    )
    contacts_count: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    @field_validator(
        "external_references", "custom_fields", mode="before"
    )
    @classmethod
    def _decode_json(cls, value: Any) -> Any:
        if value is None:
            return {}
        if isinstance(value, str):
            try:
                return json.loads(value)
            except (TypeError, ValueError):
                return {}
        return value


class CompanyList(BaseModel):
    items: list[CompanyRead]
    total: int


class CompanyAssignPayload(BaseModel):
    """Body for `POST /api/contacts/{id}/assign-company`. Pass
    `company_id=null` to clear the assignment."""

    company_id: str | None = None
