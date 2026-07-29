"""Sprint Workflows - workflow_trigger_memberships.

Revision ID: 20260729_0076
Revises: 20260627_0075
Create Date: 2026-07-29 14:00:00

Tabla de membresia para el trigger custom contact.matches_conditions
(deteccion de transicion no-cumple -> cumple por diff en el scheduler).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260729_0076"
down_revision: str | None = "20260627_0075"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workflow_trigger_memberships",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column(
            "workflow_id",
            sa.String(36),
            sa.ForeignKey("workflows.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "contact_id",
            sa.String(36),
            sa.ForeignKey("contacts.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "first_matched_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column(
            "last_seen_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.UniqueConstraint(
            "workflow_id", "contact_id",
            name="uq_workflow_trigger_membership",
        ),
    )


def downgrade() -> None:
    op.drop_table("workflow_trigger_memberships")
