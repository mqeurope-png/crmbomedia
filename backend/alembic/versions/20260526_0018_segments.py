"""segments table

Revision ID: 20260526_0018
Revises: 20260525_0017
Create Date: 2026-05-26 00:00:00

Sprint P.3 — single table that holds the rule tree (`rules_json`),
the optional frozen contact id list (`static_contact_ids`), and the
cached evaluation count + timestamp so the segments list page
renders fast.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260526_0018"
down_revision: str | None = "20260525_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "segments",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("rules_json", sa.Text(), nullable=True),
        sa.Column(
            "is_dynamic", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column("static_contact_ids", sa.Text(), nullable=True),
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
        sa.Column("color", sa.String(length=7), nullable=True),
        sa.Column("cached_count", sa.Integer(), nullable=True),
        sa.Column("last_evaluated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("segments")
