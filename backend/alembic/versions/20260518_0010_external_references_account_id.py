"""external_references: account_id + external_status

Revision ID: 20260518_0010
Revises: 20260517_0009
Create Date: 2026-05-18 00:00:00

Multi-account integration accounts can hold colliding external IDs:
AgileCRM ES and AgileCRM UK each have a contact #42 that refer to
different people. The previous UNIQUE `(system, external_id)` rejected
that as a duplicate. This migration:

- Adds `account_id` (NOT NULL after backfill with 'default') so the
  natural key matches `integration_accounts(system, account_id)`.
- Adds `external_status` (nullable) so the quota-purge job can mark a
  reference as `deleted_in_origin` without dropping the historical row.
- Replaces the single-column UNIQUE with the composite
  `(system, account_id, external_id)`.

Uses `op.batch_alter_table` so SQLite (tests) and MySQL (production)
both apply cleanly.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260518_0010"
down_revision: str | None = "20260517_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("external_references") as batch_op:
        batch_op.add_column(sa.Column("account_id", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("external_status", sa.String(length=40), nullable=True))

    # Backfill pre-existing rows with the implicit 'default' account so
    # the legacy single-account installs keep working without operator
    # intervention.
    op.execute(
        "UPDATE external_references SET account_id = 'default' WHERE account_id IS NULL"
    )

    with op.batch_alter_table("external_references") as batch_op:
        batch_op.alter_column(
            "account_id",
            existing_type=sa.String(length=64),
            nullable=False,
        )
        # Drop the legacy single-column UNIQUE; the composite replaces it.
        try:
            batch_op.drop_constraint("uq_external_reference", type_="unique")
        except Exception:  # noqa: BLE001
            pass
        batch_op.create_unique_constraint(
            "uq_external_reference_system_account_external_id",
            ["system", "account_id", "external_id"],
        )
    op.create_index(
        "ix_external_references_account_id",
        "external_references",
        ["account_id"],
        unique=False,
    )


def downgrade() -> None:
    try:
        op.drop_index("ix_external_references_account_id", table_name="external_references")
    except Exception:  # noqa: BLE001
        pass
    with op.batch_alter_table("external_references") as batch_op:
        try:
            batch_op.drop_constraint(
                "uq_external_reference_system_account_external_id",
                type_="unique",
            )
        except Exception:  # noqa: BLE001
            pass
        batch_op.create_unique_constraint(
            "uq_external_reference",
            ["system", "external_id"],
        )
        batch_op.drop_column("external_status")
        batch_op.drop_column("account_id")
