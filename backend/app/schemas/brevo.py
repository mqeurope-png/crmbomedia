"""Pydantic schemas for the Brevo-specific API surface
(`/api/brevo/*`): sync targets, templates, campaigns, senders."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ---------------------------------------------------------------------------
# Sync targets
# ---------------------------------------------------------------------------


class BrevoSyncTargetCreate(BaseModel):
    brevo_account_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    segment_id: str
    brevo_list_id: str | None = None
    sync_direction: str = Field(default="push_only")
    auto_sync_enabled: bool = True
    sync_interval_minutes: int = Field(default=60, ge=5, le=1440)

    @field_validator("sync_direction")
    @classmethod
    def validate_direction(cls, value: str) -> str:
        # `pull_only` is reserved in the enum but unsupported by the
        # push engine for now — reject it early with a clear message.
        if value not in {"push_only", "bidirectional"}:
            raise ValueError(
                "sync_direction must be 'push_only' or 'bidirectional'"
            )
        return value


class BrevoSyncTargetUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    segment_id: str | None = None
    brevo_list_id: str | None = None
    sync_direction: str | None = None
    is_active: bool | None = None
    auto_sync_enabled: bool | None = None
    sync_interval_minutes: int | None = Field(default=None, ge=5, le=1440)

    @field_validator("sync_direction")
    @classmethod
    def validate_direction(cls, value: str | None) -> str | None:
        if value is not None and value not in {"push_only", "bidirectional"}:
            raise ValueError(
                "sync_direction must be 'push_only' or 'bidirectional'"
            )
        return value


class BrevoSyncTargetRead(BaseModel):
    id: str
    brevo_account_id: str
    name: str
    description: str | None
    segment_id: str
    segment_name: str | None = None
    brevo_list_id: str | None
    sync_direction: str
    is_active: bool
    last_run_at: datetime | None
    last_run_status: str
    last_run_stats: dict[str, Any] | None = None
    auto_sync_enabled: bool
    sync_interval_minutes: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @field_validator("last_run_stats", mode="before")
    @classmethod
    def decode_stats(cls, value: Any) -> dict[str, Any] | None:
        if isinstance(value, str):
            try:
                decoded = json.loads(value)
                return decoded if isinstance(decoded, dict) else None
            except (ValueError, TypeError):
                return None
        return value


class BrevoTargetRunResponse(BaseModel):
    """POST /sync-targets/{id}/run — async run returns the sync_log id;
    dry runs return the stats inline."""

    sync_log_id: str | None = None
    job_id: str | None = None
    dry_run: bool = False
    stats: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Lists / senders (read proxies)
# ---------------------------------------------------------------------------


class BrevoListRead(BaseModel):
    id: int
    name: str
    total_subscribers: int = 0
    folder_id: int | None = None


class BrevoSenderRead(BaseModel):
    id: int
    name: str
    email: str
    active: bool = False


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


class BrevoTemplateCreate(BaseModel):
    brevo_account_id: str
    name: str = Field(min_length=1, max_length=200)
    subject: str = Field(min_length=1, max_length=500)
    html_content: str = Field(min_length=1)
    sender_name: str = Field(min_length=1, max_length=200)
    sender_email: str = Field(min_length=3, max_length=255)
    tag: str | None = Field(default=None, max_length=100)
    is_active: bool = True


class BrevoTemplateUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    subject: str | None = Field(default=None, min_length=1, max_length=500)
    html_content: str | None = None
    sender_name: str | None = Field(default=None, max_length=200)
    sender_email: str | None = Field(default=None, max_length=255)
    tag: str | None = Field(default=None, max_length=100)
    is_active: bool | None = None


class BrevoTemplateRead(BaseModel):
    id: str
    brevo_account_id: str
    brevo_template_id: int
    name: str
    subject: str | None
    is_active: bool
    tag: str | None
    sender_name: str | None
    sender_email: str | None
    created_at_brevo: datetime | None
    modified_at_brevo: datetime | None
    cached_at: datetime
    html_content: str | None = None

    model_config = ConfigDict(from_attributes=True)


class BrevoSendTestRequest(BaseModel):
    emails: list[str] = Field(min_length=1, max_length=3)


# ---------------------------------------------------------------------------
# Campaigns
# ---------------------------------------------------------------------------


class BrevoCampaignCreate(BaseModel):
    brevo_account_id: str
    name: str = Field(min_length=1, max_length=255)
    subject: str = Field(min_length=1, max_length=500)
    sender_name: str
    sender_email: str
    reply_to: str | None = None
    # Either inline HTML or a cached template id.
    html_content: str | None = None
    template_id: int | None = None
    # Either an existing Brevo list or a CRM segment to materialise.
    list_ids: list[int] | None = None
    segment_id: str | None = None
    scheduled_at: datetime | None = None


class BrevoCampaignUpdate(BaseModel):
    name: str | None = None
    subject: str | None = None
    sender_name: str | None = None
    sender_email: str | None = None
    reply_to: str | None = None
    html_content: str | None = None


class BrevoCampaignRead(BaseModel):
    id: str
    brevo_account_id: str
    brevo_campaign_id: int
    name: str
    subject: str | None
    status: str
    type: str
    sender_name: str | None
    sender_email: str | None
    reply_to: str | None
    created_at_brevo: datetime | None
    modified_at_brevo: datetime | None
    scheduled_at: datetime | None
    sent_at: datetime | None
    stats: dict[str, Any] | None = None
    recipient_list_ids: list[int] | None = None
    template_id_used: int | None
    cached_at: datetime
    # Lazy-loaded by the detail endpoint. Lists never carry it; the
    # editor iframe consumes it the first time the operator opens the
    # campaign.
    html_content: str | None = None

    model_config = ConfigDict(from_attributes=True)

    @field_validator("stats", mode="before")
    @classmethod
    def decode_stats(cls, value: Any) -> dict[str, Any] | None:
        if isinstance(value, str):
            try:
                decoded = json.loads(value)
                return decoded if isinstance(decoded, dict) else None
            except (ValueError, TypeError):
                return None
        return value

    @field_validator("recipient_list_ids", mode="before")
    @classmethod
    def decode_lists(cls, value: Any) -> list[int] | None:
        if isinstance(value, str):
            try:
                decoded = json.loads(value)
                return decoded if isinstance(decoded, list) else None
            except (ValueError, TypeError):
                return None
        return value


class BrevoCampaignScheduleRequest(BaseModel):
    scheduled_at: datetime


class BrevoWebhookStatsRead(BaseModel):
    """24h event counters for the integrations panel."""

    total: int
    by_type: dict[str, int]
