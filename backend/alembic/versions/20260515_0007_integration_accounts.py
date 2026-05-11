"""rename integration_settings to integration_accounts (multi-account)

Revision ID: 20260515_0007
Revises: 20260514_0006
Create Date: 2026-05-15 00:00:00

Converts the one-row-per-system table into a multi-account table keyed
by the composite `(system, account_id)`. Existing rows are preserved
with `account_id = 'default'` so existing API keys keep working without
operator intervention.

Uses `op.batch_alter_table` so the migration runs cleanly on both
SQLite (development / tests, via table recreation) and MySQL
(production, via plain ALTER).
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260515_0007"
down_revision: str | None = "20260514_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.rename_table("integration_settings", "integration_accounts")

    # Add the new columns. `account_id` lands as nullable so existing
    # rows can be backfilled with 'default' before we tighten the
    # constraint.
    with op.batch_alter_table("integration_accounts") as batch_op:
        batch_op.add_column(sa.Column("account_id", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("quota_max_contacts", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("quota_strategy", sa.String(length=32), nullable=True))
        batch_op.add_column(
            sa.Column(
                "sync_priority",
                sa.Integer(),
                nullable=False,
                server_default="100",
            )
        )

    # Preserve every existing row by stamping account_id='default'.
    op.execute(
        "UPDATE integration_accounts SET account_id = 'default' WHERE account_id IS NULL"
    )

    # Tighten the new columns and rewire the unique constraint to the
    # composite `(system, account_id)` so multiple accounts can coexist
    # per system. SQLite has no named unique constraint we can drop, so
    # we let batch mode rebuild the table without it.
    with op.batch_alter_table("integration_accounts") as batch_op:
        batch_op.alter_column(
            "account_id",
            existing_type=sa.String(length=64),
            nullable=False,
        )
        batch_op.create_unique_constraint(
            "uq_integration_accounts_system_account_id",
            ["system", "account_id"],
        )
        batch_op.create_index(
            "ix_integration_accounts_account_id",
            ["account_id"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("integration_accounts") as batch_op:
        batch_op.drop_index("ix_integration_accounts_account_id")
        batch_op.drop_constraint(
            "uq_integration_accounts_system_account_id",
            type_="unique",
        )
        batch_op.drop_column("sync_priority")
        batch_op.drop_column("quota_strategy")
        batch_op.drop_column("quota_max_contacts")
        batch_op.drop_column("account_id")
    op.rename_table("integration_accounts", "integration_settings")
