"""email signatures per user

Revision ID: 20260614_0036
Revises: 20260612_0035
Create Date: 2026-06-14 06:30:00

Sprint Email v2.2 follow-up. Adds the `email_signatures` table so
commerciales can store one or more reusable signatures (HTML body
authored in the TinyMCE editor), mark one as the default and have
it auto-appended at the bottom of a fresh compose.

One signature row per user per name; the application layer
enforces "at most one is_default" at write time. Cascade delete
on the user FK so a deactivated user takes their signatures with
them.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260614_0036"
down_revision: str | None = "20260612_0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "email_signatures",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("html_content", sa.Text(), nullable=False),
        sa.Column(
            "is_default",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "sort_order",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_email_signatures_user",
        "email_signatures",
        ["user_id"],
    )
    op.create_index(
        "ix_email_signatures_user_default",
        "email_signatures",
        ["user_id", "is_default"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_email_signatures_user_default", table_name="email_signatures"
    )
    op.drop_index(
        "ix_email_signatures_user", table_name="email_signatures"
    )
    op.drop_table("email_signatures")
