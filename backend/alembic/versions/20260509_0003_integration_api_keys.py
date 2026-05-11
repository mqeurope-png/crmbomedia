"""add encrypted api keys to integration settings

Revision ID: 20260509_0003
Revises: 20260507_0002
Create Date: 2026-05-09 00:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260509_0003"
down_revision: str | None = "20260507_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "integration_settings",
        sa.Column("api_key_encrypted", sa.Text(), nullable=True),
    )
    op.add_column(
        "integration_settings",
        sa.Column("api_key_set_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "integration_settings",
        sa.Column("api_key_last_used_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("integration_settings", "api_key_last_used_at")
    op.drop_column("integration_settings", "api_key_set_at")
    op.drop_column("integration_settings", "api_key_encrypted")
