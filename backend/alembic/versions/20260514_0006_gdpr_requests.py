"""gdpr_requests table for subject-rights tracking

Revision ID: 20260514_0006
Revises: 20260513_0005
Create Date: 2026-05-14 00:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260514_0006"
down_revision: str | None = "20260513_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "gdpr_requests",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("subject_email", sa.String(length=255), nullable=False, index=True),
        sa.Column("subject_contact_id", sa.String(length=36), nullable=True),
        sa.Column("request_type", sa.String(length=32), nullable=False, index=True),
        sa.Column("status", sa.String(length=32), nullable=False, index=True),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "requester_user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("evidence_path", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("gdpr_requests")
