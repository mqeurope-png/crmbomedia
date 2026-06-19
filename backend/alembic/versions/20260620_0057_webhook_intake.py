"""integration_accounts.webhook_* + new webhook_events table.

Revision ID: 20260620_0057
Revises: 20260619_0056
Create Date: 2026-06-20 10:00:00

Sprint Webhooks Agile Real-Time. Two pieces:

1. `integration_accounts`: per-account `webhook_secret` (random URL-safe
   32-byte token, plaintext — it's a query-string shared secret, not a
   user credential) + `webhook_last_received_at` so the admin card can
   render "last received X minutes ago" without scanning the audit log.

2. `webhook_events`: append-only audit trail of every inbound webhook.
   One row per delivery, regardless of whether it produced a contact —
   the `status` column carries the outcome. Old rows are pruned in a
   follow-up retention job (out of scope for this sprint).

The Brevo dedupe ledger (`webhook_events_seen`) is unrelated and stays
where it is.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260620_0057"
down_revision: str | None = "20260619_0056"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "integration_accounts",
        sa.Column("webhook_secret", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "integration_accounts",
        sa.Column(
            "webhook_last_received_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_integration_accounts_webhook_secret",
        "integration_accounts",
        ["webhook_secret"],
        unique=False,
    )

    op.create_table(
        "webhook_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("system", sa.String(length=32), nullable=False),
        sa.Column("account_id", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="received",
        ),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "contact_id",
            sa.String(length=36),
            sa.ForeignKey("contacts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "source_ip",
            sa.String(length=45),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_webhook_events_system_account_received",
        "webhook_events",
        ["system", "account_id", "received_at"],
    )
    op.create_index(
        "ix_webhook_events_status",
        "webhook_events",
        ["status"],
    )
    op.create_index(
        "ix_webhook_events_contact_id",
        "webhook_events",
        ["contact_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_webhook_events_contact_id", table_name="webhook_events"
    )
    op.drop_index("ix_webhook_events_status", table_name="webhook_events")
    op.drop_index(
        "ix_webhook_events_system_account_received",
        table_name="webhook_events",
    )
    op.drop_table("webhook_events")
    op.drop_index(
        "ix_integration_accounts_webhook_secret",
        table_name="integration_accounts",
    )
    op.drop_column("integration_accounts", "webhook_last_received_at")
    op.drop_column("integration_accounts", "webhook_secret")
