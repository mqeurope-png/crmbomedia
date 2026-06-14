"""email message scheduled send — scheduled_for + scheduled_status

Revision ID: 20260615_0039
Revises: 20260615_0038
Create Date: 2026-06-15 11:00:00

Sprint Email v2.4e — replaces the never-used "snooze a thread"
flow with the canonically-correct "schedule the send of an email".
Lives on `email_messages` because the unit of scheduling is the
outbound message, not the conversation.

Two new columns:
- `scheduled_for` — when the message should leave the building.
  NULL for every message ever sent prior to this migration and for
  any future send-now call.
- `scheduled_status` — life-cycle state machine: pending → sent /
  cancelled / failed. NULL when there's no scheduled send (i.e.
  the message was sent immediately).

A composite index on `(scheduled_for, scheduled_status)` keeps the
worker's sweep query — "every pending row past its target time"
— at index-scan cost.

Two columns are also relaxed to nullable so a pending message can
be persisted without a real Gmail id / send timestamp yet:
- `gmail_message_id` — assigned by the sweep once Gmail accepts
  the send. The uniqueness invariant is still enforced for
  non-NULL values; pending rows simply sit outside it.
- `sent_at` — the sweep stamps this when it actually hands the
  payload to Gmail.

The legacy `email_threads.snooze_until` column is intentionally
left alone (harmless) — drop it in a follow-up once nothing reads
it anymore.

MySQL-8 safe: VARCHAR(16) instead of native ENUM, mirroring the
existing email_direction / email_event_type pattern.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260615_0039"
down_revision: str | None = "20260615_0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "email_messages",
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "email_messages",
        sa.Column("scheduled_status", sa.String(length=16), nullable=True),
    )
    op.create_index(
        "ix_email_messages_scheduled",
        "email_messages",
        ["scheduled_for", "scheduled_status"],
    )
    # Pending rows have no real Gmail id / send timestamp yet — the
    # sweep stamps both when Gmail accepts the send.
    op.alter_column(
        "email_messages",
        "gmail_message_id",
        existing_type=sa.String(length=255),
        nullable=True,
    )
    op.alter_column(
        "email_messages",
        "sent_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "email_messages",
        "sent_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
    )
    op.alter_column(
        "email_messages",
        "gmail_message_id",
        existing_type=sa.String(length=255),
        nullable=False,
    )
    op.drop_index(
        "ix_email_messages_scheduled", table_name="email_messages"
    )
    op.drop_column("email_messages", "scheduled_status")
    op.drop_column("email_messages", "scheduled_for")
