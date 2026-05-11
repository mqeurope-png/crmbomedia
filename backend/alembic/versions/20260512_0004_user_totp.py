"""add totp 2fa fields to users

Revision ID: 20260512_0004
Revises: 20260509_0003
Create Date: 2026-05-12 00:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260512_0004"
down_revision: str | None = "20260509_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("totp_secret_encrypted", sa.Text(), nullable=True))
    op.add_column(
        "users",
        sa.Column(
            "totp_enabled",
            sa.Boolean(),
            nullable=False,
            # Server default lets MySQL backfill existing rows with False; it
            # is dropped immediately so the column behaves as expected
            # afterwards (the SQLAlchemy mapper uses its own Python default).
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "users", sa.Column("totp_confirmed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("users", sa.Column("backup_codes_hash", sa.Text(), nullable=True))
    op.alter_column("users", "totp_enabled", server_default=None)


def downgrade() -> None:
    op.drop_column("users", "backup_codes_hash")
    op.drop_column("users", "totp_confirmed_at")
    op.drop_column("users", "totp_enabled")
    op.drop_column("users", "totp_secret_encrypted")
