"""email drafts table

Revision ID: 20260615_0040
Revises: 20260615_0039
Create Date: 2026-06-15 13:00:00

Sprint Email v2.4d — operator drafts. A dedicated table instead
of overloading `email_messages` because drafts are mutable (every
auto-save overwrites) while sent messages are append-only, and
filtering by `is_draft` on every list-threads query would carry
permanent noise.

Schema:
- `email_drafts` — one row per in-flight compose. `to_emails`,
  `cc_emails`, `bcc_emails` are JSON text columns (same pattern
  the immediate-send path uses for the persisted EmailMessage).
- `thread_id` is nullable — a brand-new compose has no thread
  yet; a reply draft links straight to the parent's thread so
  the future "drafts within this thread" view is cheap.
- `scheduled_for` carried here too so an operator can save a
  draft of a scheduled send and resume it later.
- Two indexes targeting the list-by-user (ordered by
  updated_at DESC) and lookup-by-thread access patterns.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260615_0040"
down_revision: str | None = "20260615_0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "email_drafts",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("thread_id", sa.String(length=36), nullable=True),
        sa.Column("contact_id", sa.String(length=36), nullable=True),
        sa.Column("from_alias", sa.String(length=255), nullable=True),
        sa.Column("from_name", sa.String(length=255), nullable=True),
        sa.Column("subject", sa.String(length=500), nullable=True),
        sa.Column("body_html", sa.Text(), nullable=True),
        sa.Column("body_text", sa.Text(), nullable=True),
        sa.Column("to_emails_json", sa.Text(), nullable=True),
        sa.Column("cc_emails_json", sa.Text(), nullable=True),
        sa.Column("bcc_emails_json", sa.Text(), nullable=True),
        sa.Column(
            "in_reply_to_message_id", sa.String(length=36), nullable=True
        ),
        sa.Column("signature_id", sa.String(length=36), nullable=True),
        sa.Column(
            "include_unsubscribe",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["thread_id"], ["email_threads.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["contact_id"], ["contacts.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["signature_id"], ["email_signatures.id"], ondelete="SET NULL"
        ),
    )
    op.create_index(
        "ix_email_drafts_user_updated",
        "email_drafts",
        ["user_id", sa.text("updated_at DESC")],
    )
    op.create_index(
        "ix_email_drafts_thread", "email_drafts", ["thread_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_email_drafts_thread", table_name="email_drafts")
    op.drop_index("ix_email_drafts_user_updated", table_name="email_drafts")
    op.drop_table("email_drafts")
