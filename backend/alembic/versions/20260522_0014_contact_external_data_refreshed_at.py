"""contact.external_data_refreshed_at

Revision ID: 20260522_0014
Revises: 20260521_0013
Create Date: 2026-05-22 00:00:00

Sprint A PR-8 moves the AgileCRM notes/tasks/events fetch from the
bulk `sync_contacts` job to an on-demand refresh triggered from the
contact detail screen. We persist the last refresh timestamp on the
contact itself (not MAX over the child tables) so the freshness
indicator reflects the operator's last click even when the remote
returned zero rows.

Nullable so existing contacts have a clean "never refreshed" state
without a backfill.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260522_0014"
down_revision: str | None = "20260521_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("contacts") as batch_op:
        batch_op.add_column(
            sa.Column(
                "external_data_refreshed_at",
                sa.DateTime(timezone=True),
                nullable=True,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("contacts") as batch_op:
        batch_op.drop_column("external_data_refreshed_at")
