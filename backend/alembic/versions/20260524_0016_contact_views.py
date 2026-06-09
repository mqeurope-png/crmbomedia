"""contact_views (saved filters + columns + sort)

Revision ID: 20260524_0016
Revises: 20260523_0015
Create Date: 2026-05-24 00:00:00

Sprint P.1 ampliado PR-B. Owners can name their list configuration
(filters + visible columns + sort), share it read-only with the
team, and mark one as their default landing view.

No data migration — existing users start with zero views and the
front-end falls back to localStorage for the unsaved-config story.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260524_0016"
down_revision: str | None = "20260523_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "contact_views",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "owner_user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "is_shared", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "is_default", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("filters_json", sa.Text(), nullable=True),
        sa.Column("columns_json", sa.Text(), nullable=True),
        sa.Column("sort_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("contact_views")
