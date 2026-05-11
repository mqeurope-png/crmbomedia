"""extend audit_logs with target_*/actor_email/metadata/ip_address/user_agent

Revision ID: 20260513_0005
Revises: 20260512_0004
Create Date: 2026-05-13 00:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260513_0005"
down_revision: str | None = "20260512_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "audit_logs",
        "entity_type",
        new_column_name="target_type",
        existing_type=sa.String(length=120),
        existing_nullable=False,
    )
    op.alter_column(
        "audit_logs",
        "entity_id",
        new_column_name="target_id",
        existing_type=sa.String(length=36),
        existing_nullable=True,
    )
    op.add_column("audit_logs", sa.Column("actor_email", sa.String(length=255), nullable=True))
    op.add_column("audit_logs", sa.Column("metadata", sa.Text(), nullable=True))
    op.add_column("audit_logs", sa.Column("ip_address", sa.String(length=45), nullable=True))
    op.add_column("audit_logs", sa.Column("user_agent", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("audit_logs", "user_agent")
    op.drop_column("audit_logs", "ip_address")
    op.drop_column("audit_logs", "metadata")
    op.drop_column("audit_logs", "actor_email")
    op.alter_column(
        "audit_logs",
        "target_id",
        new_column_name="entity_id",
        existing_type=sa.String(length=36),
        existing_nullable=True,
    )
    op.alter_column(
        "audit_logs",
        "target_type",
        new_column_name="entity_type",
        existing_type=sa.String(length=120),
        existing_nullable=False,
    )
