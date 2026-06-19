"""Revert PR #202: drop webhook_events table + integration_accounts.webhook_*.

Revision ID: 20260620_0058
Revises: 20260620_0057
Create Date: 2026-06-20 11:00:00

PR-Revert-Webhooks-Agile. AgileCRM requires the Enterprise plan to send
outbound webhooks; Bart opted not to upgrade and to keep polling instead
(now once per hour — see `AGILECRM_SYNC_INTERVAL_HOURS=1`). This
migration removes the schema added by #202 on databases that already
applied `20260620_0057`. New deployments still walk 0056 → 0057 → 0058
so the table is created and then dropped without leaving artefacts.

Nothing in the dropped objects was reused by other systems:
- `webhook_events` was AgileCRM-only (Brevo dedupes via its own
  `webhook_events_seen` table, which stays).
- `integration_accounts.webhook_secret` / `webhook_last_received_at`
  were only ever read by the deleted intake route.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260620_0058"
down_revision: str | None = "20260620_0057"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # NOTE: MySQL rejects `DROP INDEX` on a column carrying a foreign
    # key constraint (the FK itself depends on the index). Drop the
    # table outright instead — MySQL + SQLite both clear the
    # associated indexes / FK as part of `DROP TABLE`. This is the
    # subtle reason 0057's downgrade can't be reused literally.
    op.drop_table("webhook_events")
    op.drop_index(
        "ix_integration_accounts_webhook_secret",
        table_name="integration_accounts",
    )
    op.drop_column("integration_accounts", "webhook_last_received_at")
    op.drop_column("integration_accounts", "webhook_secret")


def downgrade() -> None:
    # Re-running 0057's upgrade puts the schema back if an operator
    # ever wants to roll forward into a webhook-enabled deployment
    # again. We delegate to that revision's upgrade so the two stay
    # in lock-step.
    import sqlalchemy as sa  # noqa: PLC0415

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
