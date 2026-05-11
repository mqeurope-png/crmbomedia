"""Pydantic schemas for the multi-account integration module.

The old `IntegrationSettingRead` is kept as an alias of
`IntegrationAccountRead` so existing imports do not break while the
codebase is migrated; new code should reference the *Account* names.
"""
import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from app.models.crm import ExternalSystem
from app.models.integration_settings import (
    IntegrationMode,
    IntegrationStatus,
    QuotaStrategy,
)

# `account_id` is meant to appear in URLs and audit metadata, so the
# allowed alphabet is intentionally narrow: lowercase letters, digits,
# underscore and hyphen. A leading or trailing separator is rejected.
ACCOUNT_ID_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9_-]*[a-z0-9])?$")


def _normalize_account_id(value: str) -> str:
    """Validate strictly: the on-the-wire value must already match the
    documented format. We don't lowercase/strip silently — a hidden
    rewrite would surprise the operator next time they refer to the
    account by name."""
    if value != value.strip() or not ACCOUNT_ID_PATTERN.match(value):
        raise ValueError(
            "account_id must match [a-z0-9_-]+ (lowercase, no leading or trailing separator)"
        )
    if len(value) > 64:
        raise ValueError("account_id must be at most 64 characters")
    return value


class IntegrationAccountCreate(BaseModel):
    account_id: str = Field(..., min_length=1, max_length=64)
    display_name: str = Field(..., min_length=1, max_length=255)
    enabled: bool = False
    mode: IntegrationMode = IntegrationMode.SANDBOX
    api_base_url: str | None = Field(default=None, max_length=255)
    account_label: str | None = Field(default=None, max_length=255)
    notes: str | None = None
    quota_max_contacts: int | None = Field(default=None, ge=1)
    quota_strategy: QuotaStrategy | None = None
    sync_priority: int = Field(default=100, ge=0, le=10_000)

    @field_validator("account_id")
    @classmethod
    def validate_account_id(cls, value: str) -> str:
        return _normalize_account_id(value)

    @field_validator("display_name")
    @classmethod
    def strip_display_name(cls, value: str) -> str:
        return value.strip()


class IntegrationAccountUpdate(BaseModel):
    """Editable fields for an existing account. `system` and `account_id`
    are immutable; renaming requires deleting and recreating."""

    display_name: str | None = Field(default=None, min_length=1, max_length=255)
    enabled: bool | None = None
    mode: IntegrationMode | None = None
    status: IntegrationStatus | None = None
    api_base_url: str | None = Field(default=None, max_length=255)
    account_label: str | None = Field(default=None, max_length=255)
    credential_status: str | None = Field(default=None, max_length=80)
    notes: str | None = None
    quota_max_contacts: int | None = Field(default=None, ge=1)
    quota_strategy: QuotaStrategy | None = None
    sync_priority: int | None = Field(default=None, ge=0, le=10_000)

    @field_validator("display_name")
    @classmethod
    def strip_optional_display_name(cls, value: str | None) -> str | None:
        return value.strip() if value else value


class IntegrationApiKeyUpdate(BaseModel):
    api_key: str = Field(..., min_length=1, max_length=4096)

    @field_validator("api_key")
    @classmethod
    def strip_api_key(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("api_key must not be blank")
        return stripped


class IntegrationAccountRead(BaseModel):
    id: str
    system: ExternalSystem
    account_id: str
    display_name: str
    enabled: bool
    mode: IntegrationMode
    status: IntegrationStatus
    api_base_url: str | None
    account_label: str | None
    credential_status: str
    notes: str | None
    quota_max_contacts: int | None
    quota_strategy: QuotaStrategy | None
    sync_priority: int
    api_key_set_at: datetime | None = None
    api_key_last_used_at: datetime | None = None
    api_key_encrypted: str | None = Field(default=None, exclude=True, repr=False)
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def has_api_key(self) -> bool:
        return self.api_key_encrypted is not None and self.api_key_encrypted != ""


# Backwards-compatible aliases. New code should use the *Account* names.
IntegrationSettingUpdate = IntegrationAccountUpdate
IntegrationSettingRead = IntegrationAccountRead
