"""email templates + folders

Revision ID: 20260612_0034
Revises: 20260612_0033
Create Date: 2026-06-12 14:30:00

Sprint Email v2.2 — gives users a place to store their own
reusable email templates inside the CRM, organised into a
folder tree (max 3 levels enforced at the API layer; the BD is
unconstrained so legacy imports don't fail).

Two new tables:

- `email_template_folders` — hierarchical (`parent_folder_id`
  self-reference). `is_global` lets admins publish folders for
  every user; otherwise rows are owned by the creator.
- `email_templates` — `body_html` is the source of truth;
  `body_text` is auto-derived at write time (used for
  multipart sends). `usage_count` + `last_used_at` are bumped
  whenever the template is loaded into the send modal, so the
  picker can rank by "most used".

JSON-free schema: every column is a primitive or a foreign key.
The CRM uses these tables on every Gmail send flow; treating
them as first-class relational tables keeps the migration
boring (no JSON DEFAULT-on-TEXT trap, no DOWN/UP repair).

Indexes target the access patterns the picker + page run
(folder lookup, owner lookup, usage_count DESC ORDER BY).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260612_0034"
down_revision: str | None = "20260612_0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "email_template_folders",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("parent_folder_id", sa.String(length=36), nullable=True),
        sa.Column("owner_user_id", sa.String(length=36), nullable=True),
        sa.Column(
            "is_global",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "sort_order",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["parent_folder_id"],
            ["email_template_folders.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"], ["users.id"], ondelete="SET NULL"
        ),
    )
    op.create_index(
        "ix_email_template_folders_parent",
        "email_template_folders",
        ["parent_folder_id"],
    )
    op.create_index(
        "ix_email_template_folders_owner",
        "email_template_folders",
        ["owner_user_id"],
    )

    op.create_table(
        "email_templates",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("subject", sa.String(length=500), nullable=True),
        sa.Column("body_html", sa.Text(), nullable=False),
        sa.Column("body_text", sa.Text(), nullable=True),
        sa.Column("folder_id", sa.String(length=36), nullable=True),
        sa.Column("owner_user_id", sa.String(length=36), nullable=True),
        sa.Column(
            "is_global",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "usage_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["folder_id"], ["email_template_folders.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"], ["users.id"], ondelete="SET NULL"
        ),
    )
    op.create_index(
        "ix_email_templates_folder", "email_templates", ["folder_id"]
    )
    op.create_index(
        "ix_email_templates_owner", "email_templates", ["owner_user_id"]
    )
    op.create_index(
        "ix_email_templates_usage",
        "email_templates",
        [sa.text("usage_count DESC"), sa.text("last_used_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_email_templates_usage", table_name="email_templates")
    op.drop_index("ix_email_templates_owner", table_name="email_templates")
    op.drop_index("ix_email_templates_folder", table_name="email_templates")
    op.drop_table("email_templates")
    op.drop_index(
        "ix_email_template_folders_owner",
        table_name="email_template_folders",
    )
    op.drop_index(
        "ix_email_template_folders_parent",
        table_name="email_template_folders",
    )
    op.drop_table("email_template_folders")
