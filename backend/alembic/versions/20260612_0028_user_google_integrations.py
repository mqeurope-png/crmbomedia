"""user_google_integrations — per-user Google Calendar connection

Revision ID: 20260612_0028
Revises: 20260612_0027
Create Date: 2026-06-12 10:00:00

Mini-PR C Fase 2. Adds the row that holds a user's OAuth tokens (both
encrypted with the Fernet key already in play for integration
credentials) plus the calendar they picked for task sync. One row per
user, identified by `user_id` (UNIQUE).

The `tasks.google_event_id` + `tasks.google_calendar_id` columns are
already in place (migration 20260612_0027), so this migration only
introduces the connection table.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260612_0028"
down_revision: str | None = "20260612_0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_google_integrations",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("google_email", sa.String(length=255), nullable=False),
        sa.Column("access_token_encrypted", sa.Text(), nullable=False),
        sa.Column("refresh_token_encrypted", sa.Text(), nullable=False),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("selected_calendar_id", sa.String(length=255), nullable=True),
        sa.Column("selected_calendar_summary", sa.String(length=255), nullable=True),
        sa.Column("scopes", sa.Text(), nullable=False),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint("user_id", name="uq_user_google_integrations_user"),
    )
    op.create_index(
        "ix_user_google_integrations_user_id",
        "user_google_integrations",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_user_google_integrations_user_id", table_name="user_google_integrations"
    )
    op.drop_table("user_google_integrations")
