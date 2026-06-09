"""pipelines + stages + contact_pipeline_stages + history

Revision ID: 20260525_0017
Revises: 20260524_0016
Create Date: 2026-05-25 00:00:00

Sprint P.2 PR-A. Four new tables for pipeline management:

  pipelines                  — the named flow (Ventas, Reactivación…)
  pipeline_stages            — ordered steps inside a pipeline
  contact_pipeline_stages    — "contact C is in stage S of pipeline P"
                               (unique per (contact, pipeline))
  contact_stage_history      — transition log for reports

No data migration — there are no rows to backfill.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260525_0017"
down_revision: str | None = "20260524_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pipelines",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("color", sa.String(length=7), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column(
            "owner_user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "is_shared", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "pipeline_stages",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "pipeline_id",
            sa.String(length=36),
            sa.ForeignKey("pipelines.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("color", sa.String(length=7), nullable=True),
        sa.Column(
            "is_won", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "is_lost", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("target_days", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "pipeline_id", "position", name="uq_pipeline_stage_position"
        ),
    )

    op.create_table(
        "contact_pipeline_stages",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "contact_id",
            sa.String(length=36),
            sa.ForeignKey("contacts.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "pipeline_id",
            sa.String(length=36),
            sa.ForeignKey("pipelines.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "stage_id",
            sa.String(length=36),
            sa.ForeignKey("pipeline_stages.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("entered_stage_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "added_to_pipeline_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "is_archived", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "contact_id",
            "pipeline_id",
            name="uq_contact_pipeline_single_stage",
        ),
    )

    op.create_table(
        "contact_stage_history",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "contact_pipeline_stage_id",
            sa.String(length=36),
            sa.ForeignKey("contact_pipeline_stages.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "from_stage_id",
            sa.String(length=36),
            sa.ForeignKey("pipeline_stages.id"),
            nullable=True,
        ),
        sa.Column(
            "to_stage_id",
            sa.String(length=36),
            sa.ForeignKey("pipeline_stages.id"),
            nullable=False,
        ),
        sa.Column("moved_by_user_id", sa.String(length=36), nullable=True),
        sa.Column(
            "moved_at", sa.DateTime(timezone=True), nullable=False, index=True
        ),
        sa.Column(
            "duration_seconds_in_previous_stage", sa.Integer(), nullable=True
        ),
        sa.Column("note", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("contact_stage_history")
    op.drop_table("contact_pipeline_stages")
    op.drop_table("pipeline_stages")
    op.drop_table("pipelines")
