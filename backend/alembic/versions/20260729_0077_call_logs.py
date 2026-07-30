"""Sprint Ficha 360 - tabla call_logs.

Revision ID: 20260729_0077
Revises: 20260729_0076
Create Date: 2026-07-29 18:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260729_0077"
down_revision: str | None = "20260729_0076"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "call_logs",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column(
            "contact_id", sa.String(36),
            sa.ForeignKey("contacts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id", sa.String(36), sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("result_code", sa.String(32), nullable=False),
        sa.Column("result_custom", sa.String(150), nullable=True),
        sa.Column("subject", sa.String(255), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("duration_bucket", sa.String(16), nullable=True),
        sa.Column("called_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "follow_up_task_id", sa.String(36),
            sa.ForeignKey("tasks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "idx_call_logs_contact", "call_logs", ["contact_id", "called_at"]
    )
    op.create_index(
        "idx_call_logs_user", "call_logs", ["user_id", "called_at"]
    )


def downgrade() -> None:
    op.drop_table("call_logs")
