"""add integration settings

Revision ID: 20260507_0002
Revises: 20260507_0001
Create Date: 2026-05-07 00:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260507_0002"
down_revision: str | None = "20260507_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "integration_settings",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("system", sa.String(length=32), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("api_base_url", sa.String(length=255), nullable=True),
        sa.Column("account_label", sa.String(length=255), nullable=True),
        sa.Column("credential_status", sa.String(length=80), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("system"),
    )
    op.create_index(
        op.f("ix_integration_settings_system"),
        "integration_settings",
        ["system"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_integration_settings_system"), table_name="integration_settings")
    op.drop_table("integration_settings")
