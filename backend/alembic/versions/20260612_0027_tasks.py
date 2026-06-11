"""tasks — expand to productivity layer (assigned_user_id, priority, etc.)

Revision ID: 20260612_0027
Revises: 20260611_0026
Create Date: 2026-06-12 09:00:00

Mini-PR C Fase 1. The Sprint A `tasks` table was a thin contact
sub-resource (title, status, due_at, assignee_user_id, contact_id, +
AgileCRM provenance). Productivity layer needs description, priority,
optional company / pipeline-stage links, a separate creator, and
Google Calendar mirror columns.

This migration is ALTER-in-place — production already carries
imported AgileCRM tasks under the legacy shape, so we preserve every
row:

- Rename `assignee_user_id` → `assigned_user_id`, fill NULLs from
  the row's creator if any (else first admin) and make NOT NULL.
- Relax `contact_id` to NULL so the operator can keep personal todos.
- Add `description`, `priority` (default 'medium'), `company_id`,
  `pipeline_stage_id`, `created_by_user_id` (default = assignee for
  legacy rows), `google_event_id`, `google_calendar_id`,
  `reminder_minutes_before`, `completed_at`.
- Map legacy `status='open'` → 'pending'. 'done'/'cancelled' stay.
- Add the three hot-path indexes the new UI needs.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260612_0027"
down_revision: str | None = "20260611_0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()

    with op.batch_alter_table("tasks") as batch:
        batch.add_column(sa.Column("description", sa.Text(), nullable=True))
        batch.add_column(
            sa.Column(
                "priority",
                sa.String(length=32),
                nullable=False,
                server_default="medium",
            )
        )
        batch.add_column(
            sa.Column("company_id", sa.String(length=36), nullable=True)
        )
        batch.add_column(
            sa.Column("pipeline_stage_id", sa.String(length=36), nullable=True)
        )
        batch.add_column(
            sa.Column("created_by_user_id", sa.String(length=36), nullable=True)
        )
        batch.add_column(
            sa.Column("google_event_id", sa.String(length=255), nullable=True)
        )
        batch.add_column(
            sa.Column("google_calendar_id", sa.String(length=255), nullable=True)
        )
        batch.add_column(
            sa.Column("reminder_minutes_before", sa.Integer(), nullable=True)
        )
        batch.add_column(
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.add_column(
            sa.Column("assigned_user_id", sa.String(length=36), nullable=True)
        )

    # Copy legacy `assignee_user_id` → new `assigned_user_id`.
    bind.execute(
        sa.text(
            "UPDATE tasks SET assigned_user_id = assignee_user_id "
            "WHERE assignee_user_id IS NOT NULL"
        )
    )
    # Backfill legacy rows that lacked an assignee with the first
    # admin so `assigned_user_id` can become NOT NULL.
    admin_id = bind.scalar(
        sa.text(
            "SELECT id FROM users WHERE role = 'admin' ORDER BY created_at LIMIT 1"
        )
    )
    if admin_id is not None:
        bind.execute(
            sa.text(
                "UPDATE tasks SET assigned_user_id = :admin "
                "WHERE assigned_user_id IS NULL"
            ),
            {"admin": admin_id},
        )
        bind.execute(
            sa.text(
                "UPDATE tasks SET created_by_user_id = COALESCE("
                "  created_by_user_id, assigned_user_id, :admin) "
                "WHERE created_by_user_id IS NULL"
            ),
            {"admin": admin_id},
        )
    # status 'open' (Sprint A) → 'pending' (productivity layer).
    bind.execute(sa.text("UPDATE tasks SET status = 'pending' WHERE status = 'open'"))

    # Tighten constraints now that data is migrated.
    dialect = bind.dialect.name
    if dialect == "mysql":
        bind.execute(
            sa.text(
                "ALTER TABLE tasks MODIFY assigned_user_id VARCHAR(36) NOT NULL"
            )
        )
        bind.execute(
            sa.text(
                "ALTER TABLE tasks MODIFY created_by_user_id VARCHAR(36) NOT NULL"
            )
        )
        bind.execute(sa.text("ALTER TABLE tasks MODIFY contact_id VARCHAR(36) NULL"))
        # `assignee_user_id` keeps its data path for any external read
        # that still uses the old name, dropped later.
    else:
        # SQLite: batch_alter_table can rebuild with the new
        # constraints. Drop the legacy column outright too.
        pass

    # Drop the legacy assignee column on every dialect.
    with op.batch_alter_table("tasks") as batch:
        batch.drop_column("assignee_user_id")
        batch.create_foreign_key(
            "fk_tasks_assigned_user", "users", ["assigned_user_id"], ["id"]
        )
        batch.create_foreign_key(
            "fk_tasks_created_by_user", "users", ["created_by_user_id"], ["id"]
        )
        batch.create_foreign_key(
            "fk_tasks_company",
            "companies",
            ["company_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_foreign_key(
            "fk_tasks_pipeline_stage",
            "pipeline_stages",
            ["pipeline_stage_id"],
            ["id"],
            ondelete="SET NULL",
        )

    op.create_index(
        "ix_tasks_assigned_user_due", "tasks", ["assigned_user_id", "due_at"]
    )
    op.create_index("ix_tasks_status", "tasks", ["status"])
    op.create_index("ix_tasks_due_at", "tasks", ["due_at"])


def downgrade() -> None:
    op.drop_index("ix_tasks_due_at", table_name="tasks")
    op.drop_index("ix_tasks_status", table_name="tasks")
    op.drop_index("ix_tasks_assigned_user_due", table_name="tasks")
    with op.batch_alter_table("tasks") as batch:
        batch.drop_constraint("fk_tasks_pipeline_stage", type_="foreignkey")
        batch.drop_constraint("fk_tasks_company", type_="foreignkey")
        batch.drop_constraint("fk_tasks_created_by_user", type_="foreignkey")
        batch.drop_constraint("fk_tasks_assigned_user", type_="foreignkey")
        batch.add_column(
            sa.Column("assignee_user_id", sa.String(length=36), nullable=True)
        )
    op.execute(
        "UPDATE tasks SET assignee_user_id = assigned_user_id "
        "WHERE assigned_user_id IS NOT NULL"
    )
    with op.batch_alter_table("tasks") as batch:
        batch.drop_column("completed_at")
        batch.drop_column("reminder_minutes_before")
        batch.drop_column("google_calendar_id")
        batch.drop_column("google_event_id")
        batch.drop_column("created_by_user_id")
        batch.drop_column("pipeline_stage_id")
        batch.drop_column("company_id")
        batch.drop_column("priority")
        batch.drop_column("description")
        batch.drop_column("assigned_user_id")
