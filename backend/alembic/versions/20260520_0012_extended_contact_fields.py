"""extended contact + external_reference fields for richer AgileCRM mapping

Revision ID: 20260520_0012
Revises: 20260519_0011
Create Date: 2026-05-20 00:00:00

Sprint A PR-2 (post follow-up) lands the broader AgileCRM mapping:
custom properties, parsed address, lead score, and per-account
provenance (created/updated time, owner, source). Adds the
corresponding columns to `contacts` and `external_references`. All
nullable so existing rows keep working without backfill.

Uses `op.batch_alter_table` so SQLite (tests) and MySQL (production)
both apply cleanly.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260520_0012"
down_revision: str | None = "20260519_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("contacts") as batch_op:
        batch_op.add_column(sa.Column("custom_fields", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("address_country", sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column("address_country_name", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("address_state", sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column("address_city", sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column("lead_score", sa.Integer(), nullable=True))

    with op.batch_alter_table("external_references") as batch_op:
        batch_op.add_column(
            sa.Column("external_created_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("external_updated_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(sa.Column("origin_detail", sa.String(length=255), nullable=True))
        # Stored as JSON text. The Python attribute is `metadata_json`
        # because `metadata` clashes with SQLAlchemy's `Base.metadata`.
        batch_op.add_column(sa.Column("metadata", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("external_references") as batch_op:
        batch_op.drop_column("metadata")
        batch_op.drop_column("origin_detail")
        batch_op.drop_column("external_updated_at")
        batch_op.drop_column("external_created_at")

    with op.batch_alter_table("contacts") as batch_op:
        batch_op.drop_column("lead_score")
        batch_op.drop_column("address_city")
        batch_op.drop_column("address_state")
        batch_op.drop_column("address_country_name")
        batch_op.drop_column("address_country")
        batch_op.drop_column("custom_fields")
