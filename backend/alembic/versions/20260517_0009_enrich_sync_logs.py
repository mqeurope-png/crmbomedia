"""enrich sync_logs with multi-account + lifecycle columns

Revision ID: 20260517_0009
Revises: 20260516_0008
Create Date: 2026-05-17 00:00:00

Adds the columns the Sprint A integration infrastructure needs:
account_id, operation, started_at, finished_at, counts, error_summary,
triggered_by, triggered_by_user_id, job_id and the JSON metadata
column. Backwards compatible: the legacy `direction` and `message`
columns stay in place. Uses `op.batch_alter_table` so it runs on both
MySQL (production) and SQLite (tests).
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260517_0009"
down_revision: str | None = "20260516_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("sync_logs") as batch_op:
        batch_op.add_column(sa.Column("account_id", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("operation", sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(
            sa.Column(
                "records_processed",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch_op.add_column(
            sa.Column(
                "records_skipped",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch_op.add_column(
            sa.Column(
                "records_failed",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch_op.add_column(sa.Column("error_summary", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("triggered_by", sa.String(length=32), nullable=True))
        batch_op.add_column(
            sa.Column(
                "triggered_by_user_id",
                sa.String(length=36),
                sa.ForeignKey("users.id"),
                nullable=True,
            )
        )
        batch_op.add_column(sa.Column("job_id", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("metadata", sa.Text(), nullable=True))
        # The original `status` is VARCHAR(80) in the legacy column; we
        # leave it as-is and just standardise on the new shorter
        # lifecycle strings going forward.
        # `direction` is loosened to nullable so new rows (cron / worker /
        # webhook) don't need to invent one — the new `operation` column
        # is the canonical name now.
        batch_op.alter_column(
            "direction",
            existing_type=sa.String(length=80),
            nullable=True,
        )

    op.create_index("ix_sync_logs_system", "sync_logs", ["system"], unique=False)
    op.create_index("ix_sync_logs_account_id", "sync_logs", ["account_id"], unique=False)
    op.create_index("ix_sync_logs_operation", "sync_logs", ["operation"], unique=False)
    op.create_index("ix_sync_logs_job_id", "sync_logs", ["job_id"], unique=False)


def downgrade() -> None:
    for ix in (
        "ix_sync_logs_job_id",
        "ix_sync_logs_operation",
        "ix_sync_logs_account_id",
        "ix_sync_logs_system",
    ):
        try:
            op.drop_index(ix, table_name="sync_logs")
        except Exception:  # noqa: BLE001
            pass

    with op.batch_alter_table("sync_logs") as batch_op:
        batch_op.alter_column(
            "direction",
            existing_type=sa.String(length=80),
            nullable=False,
        )
        batch_op.drop_column("metadata")
        batch_op.drop_column("job_id")
        batch_op.drop_column("triggered_by_user_id")
        batch_op.drop_column("triggered_by")
        batch_op.drop_column("error_summary")
        batch_op.drop_column("records_failed")
        batch_op.drop_column("records_skipped")
        batch_op.drop_column("records_processed")
        batch_op.drop_column("finished_at")
        batch_op.drop_column("started_at")
        batch_op.drop_column("operation")
        batch_op.drop_column("account_id")
