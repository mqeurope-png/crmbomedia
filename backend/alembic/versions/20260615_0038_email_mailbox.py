"""email mailbox — folders, labels, thread state/star/snooze

Revision ID: 20260615_0038
Revises: 20260614_0037
Create Date: 2026-06-15 09:00:00

Sprint Email v2.4a — backend foundation for the Gmail/Outlook-style
mailbox redesign. The CRM gains:

- `email_folders` — per-user organisational tree the operator
  curates from the sidebar. Single-parent (`parent_id`), non
  exclusive vs. labels, optional `color` / `icon` for the sidebar
  badge. `is_system` flags the four built-ins (Bandeja, Enviados,
  Archivados, Papelera) so the UI can prevent deletion without
  hardcoding ids. Never synced with Gmail labels — this is purely
  CRM-side classification.
- `email_labels` — per-user free-form tags applied to threads,
  many-to-many via `email_thread_labels`. A thread can carry
  multiple labels at once and still live in a folder.
- `email_thread_labels` — junction table (`thread_id`, `label_id`).
  `applied_at` is kept so the future "added X label N days ago"
  tooltip works without a separate audit trail.
- `email_threads` gains four columns:
  - `folder_id` — optional FK to `email_folders` (NULL = bandeja).
  - `state` — top-level box: inbox / archived / trashed / spam.
    Independent of `folder_id` so an archived thread keeps its
    folder when the operator restores it.
  - `is_starred` — flag for the star toggle in the list/header.
  - `snooze_until` — when set, the thread is hidden from the
    inbox view until the worker (v2.4c) flips it back.

Backfill: `state` defaults to 'inbox'; any thread with
`is_archived=true` is upgraded to 'archived' so existing data
keeps the same visibility. `is_archived` is kept as a column for
backwards-compat with older readers; we'll drop it in a later
migration once every call site reads `state`.

MySQL-8 safe: every boolean uses `sa.false()` server default; the
new `state` column is VARCHAR(16) with a string default rather
than a native ENUM, mirroring the existing email_direction /
email_event_type pattern.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260615_0038"
down_revision: str | None = "20260614_0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "email_folders",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("parent_id", sa.String(length=36), nullable=True),
        sa.Column("color", sa.String(length=20), nullable=True),
        sa.Column("icon", sa.String(length=40), nullable=True),
        sa.Column(
            "sort_order", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "is_system",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"], ["email_folders.id"], ondelete="SET NULL"
        ),
    )
    op.create_index(
        "ix_email_folders_user", "email_folders", ["user_id"]
    )
    op.create_index(
        "ix_email_folders_parent", "email_folders", ["parent_id"]
    )

    op.create_table(
        "email_labels",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("color", sa.String(length=20), nullable=True),
        sa.Column(
            "sort_order", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "user_id", "name", name="uq_email_labels_user_name"
        ),
    )
    op.create_index(
        "ix_email_labels_user", "email_labels", ["user_id"]
    )

    op.create_table(
        "email_thread_labels",
        sa.Column("thread_id", sa.String(length=36), nullable=False),
        sa.Column("label_id", sa.String(length=36), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("thread_id", "label_id"),
        sa.ForeignKeyConstraint(
            ["thread_id"], ["email_threads.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["label_id"], ["email_labels.id"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_email_thread_labels_label",
        "email_thread_labels",
        ["label_id"],
    )

    op.add_column(
        "email_threads",
        sa.Column("folder_id", sa.String(length=36), nullable=True),
    )
    op.create_foreign_key(
        "fk_email_threads_folder",
        "email_threads",
        "email_folders",
        ["folder_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_email_threads_folder", "email_threads", ["folder_id"]
    )

    op.add_column(
        "email_threads",
        sa.Column(
            "state",
            sa.String(length=16),
            nullable=False,
            server_default="inbox",
        ),
    )
    op.add_column(
        "email_threads",
        sa.Column(
            "is_starred",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "email_threads",
        sa.Column(
            "snooze_until",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    op.execute(
        "UPDATE email_threads SET state = 'archived' WHERE is_archived = 1"
    )

    op.create_index(
        "ix_email_threads_state", "email_threads", ["state"]
    )
    op.create_index(
        "ix_email_threads_snooze", "email_threads", ["snooze_until"]
    )


def downgrade() -> None:
    op.drop_index("ix_email_threads_snooze", table_name="email_threads")
    op.drop_index("ix_email_threads_state", table_name="email_threads")
    op.drop_column("email_threads", "snooze_until")
    op.drop_column("email_threads", "is_starred")
    op.drop_column("email_threads", "state")
    op.drop_index("ix_email_threads_folder", table_name="email_threads")
    op.drop_constraint(
        "fk_email_threads_folder", "email_threads", type_="foreignkey"
    )
    op.drop_column("email_threads", "folder_id")

    op.drop_index(
        "ix_email_thread_labels_label", table_name="email_thread_labels"
    )
    op.drop_table("email_thread_labels")
    op.drop_index("ix_email_labels_user", table_name="email_labels")
    op.drop_table("email_labels")
    op.drop_index("ix_email_folders_parent", table_name="email_folders")
    op.drop_index("ix_email_folders_user", table_name="email_folders")
    op.drop_table("email_folders")
