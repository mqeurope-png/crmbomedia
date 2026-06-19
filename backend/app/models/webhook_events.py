"""Webhook intake audit trail.

`WebhookEvent` captures every inbound webhook so the operator can
replay, debug, or evidence a delivery — one row regardless of the
processing outcome. The `status` enum carries that outcome
(`received → processed | failed | skipped`).

Distinct from `webhook_events_seen` (Brevo dedupe ledger): that one
keys on `(system, event_key)` to swallow duplicate retries; this one
is an append-only audit trail.

The Sprint Webhooks Agile Real-Time PR introduces the table for the
AgileCRM receiver, but the shape is intentionally generic — every
future per-system intake (Freshdesk, FactuSOL, etc.) records here
through the same writer.
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.crm import Base, TimestampMixin, enum_values


class WebhookEventStatus(StrEnum):
    """Processing outcome of a single inbound webhook delivery."""

    RECEIVED = "received"
    PROCESSED = "processed"
    FAILED = "failed"
    SKIPPED = "skipped"


class WebhookEvent(TimestampMixin, Base):
    __tablename__ = "webhook_events"
    __table_args__ = (
        Index(
            "ix_webhook_events_system_account_received",
            "system",
            "account_id",
            "received_at",
        ),
        Index("ix_webhook_events_status", "status"),
        Index("ix_webhook_events_contact_id", "contact_id"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    system: Mapped[str] = mapped_column(String(32), nullable=False)
    account_id: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    # Raw payload — kept as TEXT so replay and debugging can rebuild
    # whatever shape the upstream system shipped. Capped before insert
    # so a misbehaving remote can't drown the table.
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[WebhookEventStatus] = mapped_column(
        Enum(
            WebhookEventStatus,
            native_enum=False,
            values_callable=enum_values,
            length=16,
        ),
        default=WebhookEventStatus.RECEIVED,
        nullable=False,
    )
    error_summary: Mapped[str | None] = mapped_column(Text)
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    # NULLABLE FK back to the contact the event resolved to (create /
    # update). NULL when the event was skipped or failed before
    # contact resolution.
    contact_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("contacts.id", ondelete="SET NULL"),
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # Leftmost X-Forwarded-For (nginx-proxied) or request.client.host.
    # Used by the audit UI + rate limiter; trimmed at 45 chars for
    # IPv6 compatibility.
    source_ip: Mapped[str | None] = mapped_column(String(45))
