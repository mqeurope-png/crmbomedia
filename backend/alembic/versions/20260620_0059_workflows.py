"""Sprint Workflows Bloque 1 — schema inicial del motor de automatización.

Revision ID: 20260620_0059
Revises: 20260620_0058
Create Date: 2026-06-20 12:00:00

Crea 6 tablas:
- `workflows`
- `workflow_steps`
- `workflow_edges`
- `workflow_runs` (con UNIQUE active_dedup_key para el reentry guard)
- `workflow_run_history` (audit append-only de cada acción ejecutada)
- `workflow_event_waits` (runs esperando un evento concreto)
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260620_0059"
down_revision: str | None = "20260620_0058"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workflows",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="draft",
        ),
        sa.Column("trigger_type", sa.String(length=80), nullable=False),
        # MySQL no admite DEFAULT en columnas TEXT/BLOB/JSON (error 1101).
        # Las columnas JSON-en-TEXT son NOT NULL pero el caller rellena
        # `'{}'` / `'[]'` en code (engine + API). SQLite tolera el INSERT
        # con valor explícito desde ambos lados.
        sa.Column(
            "trigger_config_json", sa.Text(), nullable=False
        ),
        sa.Column(
            "allow_reentry",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "cancellation_events_json", sa.Text(), nullable=False
        ),
        sa.Column(
            "total_entered",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "total_completed",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "total_won", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "total_cancelled",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "total_failed",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "created_by_user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False
        ),
    )
    op.create_index("ix_workflows_status", "workflows", ["status"])
    op.create_index(
        "ix_workflows_trigger_type", "workflows", ["trigger_type"]
    )

    op.create_table(
        "workflow_steps",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "workflow_id",
            sa.String(length=36),
            sa.ForeignKey("workflows.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("type", sa.String(length=80), nullable=False),
        sa.Column("config_json", sa.Text(), nullable=False),
        sa.Column(
            "position_x", sa.Float(), nullable=False, server_default="0"
        ),
        sa.Column(
            "position_y", sa.Float(), nullable=False, server_default="0"
        ),
        sa.Column(
            "is_entry",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False
        ),
    )
    op.create_index(
        "ix_workflow_steps_workflow_id", "workflow_steps", ["workflow_id"]
    )

    op.create_table(
        "workflow_edges",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "workflow_id",
            sa.String(length=36),
            sa.ForeignKey("workflows.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "from_step_id",
            sa.String(length=36),
            sa.ForeignKey("workflow_steps.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "to_step_id",
            sa.String(length=36),
            sa.ForeignKey("workflow_steps.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "branch_label",
            sa.String(length=40),
            nullable=False,
            server_default="default",
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False
        ),
    )
    op.create_index(
        "ix_workflow_edges_workflow_id_from",
        "workflow_edges",
        ["workflow_id", "from_step_id"],
    )

    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "workflow_id",
            sa.String(length=36),
            sa.ForeignKey("workflows.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "contact_id",
            sa.String(length=36),
            sa.ForeignKey("contacts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "current_step_id",
            sa.String(length=36),
            sa.ForeignKey("workflow_steps.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "state",
            sa.String(length=24),
            nullable=False,
            server_default="running",
        ),
        sa.Column("exit_kind", sa.String(length=16), nullable=True),
        sa.Column("wake_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "active_dedup_key", sa.String(length=80), nullable=False
        ),
        sa.Column("split_buckets_json", sa.Text(), nullable=False),
        sa.Column("trigger_payload_json", sa.Text(), nullable=False),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column(
            "completed_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.UniqueConstraint(
            "active_dedup_key", name="uq_workflow_runs_dedup"
        ),
    )
    op.create_index(
        "ix_workflow_runs_scheduler",
        "workflow_runs",
        ["state", "wake_at"],
    )
    op.create_index(
        "ix_workflow_runs_contact",
        "workflow_runs",
        ["contact_id", "state"],
    )
    op.create_index(
        "ix_workflow_runs_workflow",
        "workflow_runs",
        ["workflow_id", "state"],
    )
    op.create_index(
        "ix_workflow_runs_wake_at", "workflow_runs", ["wake_at"]
    )

    op.create_table(
        "workflow_run_history",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(length=36),
            sa.ForeignKey("workflow_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "workflow_id",
            sa.String(length=36),
            sa.ForeignKey("workflows.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "contact_id",
            sa.String(length=36),
            sa.ForeignKey("contacts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "step_id",
            sa.String(length=36),
            sa.ForeignKey("workflow_steps.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("step_type", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column(
            "executed_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False
        ),
    )
    op.create_index(
        "ix_workflow_run_history_run",
        "workflow_run_history",
        ["run_id", "executed_at"],
    )
    op.create_index(
        "ix_workflow_run_history_contact",
        "workflow_run_history",
        ["contact_id", "executed_at"],
    )

    op.create_table(
        "workflow_event_waits",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(length=36),
            sa.ForeignKey("workflow_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "workflow_id",
            sa.String(length=36),
            sa.ForeignKey("workflows.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "contact_id",
            sa.String(length=36),
            sa.ForeignKey("contacts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "step_id",
            sa.String(length=36),
            sa.ForeignKey("workflow_steps.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("condition_json", sa.Text(), nullable=True),
        sa.Column(
            "timeout_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False
        ),
    )
    op.create_index(
        "ix_workflow_event_waits_event",
        "workflow_event_waits",
        ["event_type", "timeout_at"],
    )
    op.create_index(
        "ix_workflow_event_waits_run",
        "workflow_event_waits",
        ["run_id"],
    )


def downgrade() -> None:
    op.drop_table("workflow_event_waits")
    op.drop_table("workflow_run_history")
    op.drop_table("workflow_runs")
    op.drop_table("workflow_edges")
    op.drop_table("workflow_steps")
    op.drop_table("workflows")
