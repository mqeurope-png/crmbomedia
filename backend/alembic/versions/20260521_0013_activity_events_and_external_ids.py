"""activity_events table + external_* columns on notes/tasks

Revision ID: 20260521_0013
Revises: 20260520_0012
Create Date: 2026-05-21 00:00:00

Sprint A PR-5 imports notes / tasks / activities from AgileCRM and
persists them locally so the contact detail screen shows the full
remote context. The notes + tasks tables get an `external_*` provenance
trio (system, account_id, external_id) plus per-side timestamps so the
sync job can dedup re-imports without colliding with manually-created
rows. The new `activity_events` table holds the AgileCRM timeline,
deduped by a (system, account_id, external_id) unique constraint.

All new columns are NULLABLE so the migration is a non-event for
existing data — manual notes/tasks keep working unchanged.

Uses `op.batch_alter_table` so SQLite (tests) and MySQL (production)
both apply cleanly.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260521_0013"
down_revision: str | None = "20260520_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("notes") as batch_op:
        batch_op.add_column(sa.Column("external_system", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("external_account_id", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("external_id", sa.String(length=255), nullable=True))
        batch_op.add_column(
            sa.Column("external_author_email", sa.String(length=255), nullable=True)
        )
        batch_op.add_column(
            sa.Column("external_author_name", sa.String(length=255), nullable=True)
        )
        batch_op.add_column(
            sa.Column("external_created_at", sa.DateTime(timezone=True), nullable=True)
        )

    with op.batch_alter_table("tasks") as batch_op:
        batch_op.add_column(sa.Column("external_system", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("external_account_id", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("external_id", sa.String(length=255), nullable=True))
        batch_op.add_column(
            sa.Column("external_created_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("external_updated_at", sa.DateTime(timezone=True), nullable=True)
        )

    op.create_table(
        "activity_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "contact_id",
            sa.String(length=36),
            sa.ForeignKey("contacts.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("system", sa.String(length=32), nullable=False),
        sa.Column("account_id", sa.String(length=64), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False, index=True),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("metadata", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "system",
            "account_id",
            "external_id",
            name="uq_activity_event_system_account_external_id",
        ),
    )


def downgrade() -> None:
    op.drop_table("activity_events")

    with op.batch_alter_table("tasks") as batch_op:
        batch_op.drop_column("external_updated_at")
        batch_op.drop_column("external_created_at")
        batch_op.drop_column("external_id")
        batch_op.drop_column("external_account_id")
        batch_op.drop_column("external_system")

    with op.batch_alter_table("notes") as batch_op:
        batch_op.drop_column("external_created_at")
        batch_op.drop_column("external_author_name")
        batch_op.drop_column("external_author_email")
        batch_op.drop_column("external_id")
        batch_op.drop_column("external_account_id")
        batch_op.drop_column("external_system")
