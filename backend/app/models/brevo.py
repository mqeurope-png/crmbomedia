"""Brevo-specific persistence: sync targets, membership tracking,
webhook dedupe, and the templates/campaigns caches.

These live apart from `crm.py` because they're connector-private —
nothing outside `app.integrations.brevo` and the `/api/brevo/*`
routes should touch them.
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.models.crm import Base, TimestampMixin, enum_values

#: HTML payloads from Brevo regularly cross the 64KB TEXT limit on
#: MySQL — one real template weighed 124KB and crashed
#: `?refresh=true` with 500. `Text` on SQLite is unbounded already;
#: the MySQL-only variant uses LONGTEXT (4GB).
_LongText = Text().with_variant(LONGTEXT(), "mysql")


class SyncDirection(StrEnum):
    PUSH_ONLY = "push_only"
    PULL_ONLY = "pull_only"
    BIDIRECTIONAL = "bidirectional"


class TargetRunStatus(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL_ERROR = "partial_error"
    ERROR = "error"


class BrevoSyncTarget(TimestampMixin, Base):
    """One push rule: "the contacts matching segment X go to Brevo
    list Y of account Z". The CRM holds contacts from every AgileCRM
    account; a target picks the slice that belongs in a given Brevo
    audience."""

    __tablename__ = "brevo_sync_targets"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    # Informal FK to integration_accounts.account_id (system='brevo'),
    # same convention as external_references: removing the account
    # doesn't cascade so the run history survives for audits.
    brevo_account_id: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    segment_id: Mapped[str] = mapped_column(
        ForeignKey("segments.id"), nullable=False
    )
    # Brevo list id as string (Brevo uses ints; string keeps us
    # agnostic to future UUID-shaped ids). NULL → contacts are pushed
    # without list assignment.
    brevo_list_id: Mapped[str | None] = mapped_column(String(64))
    sync_direction: Mapped[SyncDirection] = mapped_column(
        Enum(SyncDirection, native_enum=False, values_callable=enum_values, length=32),
        default=SyncDirection.PUSH_ONLY,
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_run_status: Mapped[TargetRunStatus] = mapped_column(
        Enum(
            TargetRunStatus,
            native_enum=False,
            values_callable=enum_values,
            length=32,
        ),
        default=TargetRunStatus.IDLE,
        nullable=False,
    )
    last_run_stats_json: Mapped[str | None] = mapped_column(Text)
    auto_sync_enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    sync_interval_minutes: Mapped[int] = mapped_column(
        Integer, default=60, nullable=False
    )

    @property
    def last_run_stats(self) -> str | None:
        """Raw JSON string for the Pydantic read schema — its
        before-validator decodes. Same `from_attributes` trick as
        `Contact.tag_objects`."""
        return self.last_run_stats_json


class BrevoTargetMembership(Base):
    """Tracks which contacts a target pushed on its last run so the
    next run can compute the delta: contacts that left the segment
    get removed from the Brevo list (NOT deleted in Brevo)."""

    __tablename__ = "brevo_target_memberships"
    __table_args__ = (
        UniqueConstraint(
            "target_id", "contact_id", name="uq_brevo_membership_target_contact"
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    target_id: Mapped[str] = mapped_column(
        ForeignKey("brevo_sync_targets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    contact_id: Mapped[str] = mapped_column(
        ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WebhookEventSeen(Base):
    """Dedupe ledger for incoming webhooks. Brevo delivers best-effort
    (same event can arrive twice); we record each event's id and skip
    re-processing. Rows older than 30 days are pruned opportunistically
    by the webhook worker."""

    __tablename__ = "webhook_events_seen"
    __table_args__ = (
        UniqueConstraint("system", "event_key", name="uq_webhook_event_seen"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    system: Mapped[str] = mapped_column(String(32), nullable=False)
    event_key: Mapped[str] = mapped_column(String(255), nullable=False)
    seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )


class BrevoTemplateCache(TimestampMixin, Base):
    """Local mirror of Brevo email templates so the /marketing UI
    renders instantly. `html_content` is lazy-loaded on first detail
    open (the list endpoint never pays for megabytes of HTML)."""

    __tablename__ = "brevo_templates_cache"
    __table_args__ = (
        UniqueConstraint(
            "brevo_account_id",
            "brevo_template_id",
            name="uq_brevo_template_account_template",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    brevo_account_id: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True
    )
    brevo_template_id: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    subject: Mapped[str | None] = mapped_column(String(500))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    tag: Mapped[str | None] = mapped_column(String(100))
    sender_name: Mapped[str | None] = mapped_column(String(200))
    sender_email: Mapped[str | None] = mapped_column(String(255))
    created_at_brevo: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    modified_at_brevo: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cached_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    html_content: Mapped[str | None] = mapped_column(_LongText)


class BrevoCampaignCache(TimestampMixin, Base):
    """Local mirror of Brevo email campaigns (status + aggregated
    stats). Refreshed by the 15-min cron job and on-demand when the
    operator opens a detail whose cache is older than 5 minutes."""

    __tablename__ = "brevo_campaigns_cache"
    __table_args__ = (
        UniqueConstraint(
            "brevo_account_id",
            "brevo_campaign_id",
            name="uq_brevo_campaign_account_campaign",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    brevo_account_id: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True
    )
    brevo_campaign_id: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="draft")
    type: Mapped[str] = mapped_column(String(40), nullable=False, default="classic")
    sender_name: Mapped[str | None] = mapped_column(String(200))
    sender_email: Mapped[str | None] = mapped_column(String(255))
    reply_to: Mapped[str | None] = mapped_column(String(255))
    created_at_brevo: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    modified_at_brevo: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stats_json: Mapped[str | None] = mapped_column(Text)
    recipient_list_ids_json: Mapped[str | None] = mapped_column(Text)
    template_id_used: Mapped[int | None] = mapped_column(Integer)
    cached_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Lazy-loaded on first detail open. Brevo's list endpoint doesn't
    # return htmlContent, so this column is `None` until the
    # operator opens the campaign detail; from then on it serves the
    # iframe preview without round-tripping Brevo.
    html_content_cached: Mapped[str | None] = mapped_column(_LongText)

    @property
    def stats(self) -> str | None:
        return self.stats_json

    @property
    def recipient_list_ids(self) -> str | None:
        return self.recipient_list_ids_json
