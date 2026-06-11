"""user_email_alias_prefs — per-user Gmail alias preferences.

Revision ID: 20260612_0031
Revises: 20260612_0030
Create Date: 2026-06-12 12:00:00

Bomedia operators routinely have 50+ "Send mail as" aliases
configured in Gmail. This table lets each CRM user pick the
handful they actually use (`is_allowed=true`) and optionally
flag one as the default sender (`is_default=true`).

Only marked aliases are persisted. Unchecking an alias = delete
the row. The app enforces "at most one default per user" inside
the upsert endpoint (no DB-level partial unique because SQLite
doesn't support them portably).

Downgrade reversible: drop the table.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260612_0031"
down_revision: str | None = "20260612_0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_email_alias_prefs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("alias_email", sa.String(length=255), nullable=False),
        sa.Column(
            "is_allowed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column(
            "is_default",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "user_id", "alias_email", name="uq_user_email_alias_prefs_user_alias"
        ),
    )
    op.create_index(
        "ix_user_email_alias_prefs_user",
        "user_email_alias_prefs",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_user_email_alias_prefs_user", table_name="user_email_alias_prefs"
    )
    op.drop_table("user_email_alias_prefs")
