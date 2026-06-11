"""email_threads + email_messages + gmail_pubsub_watches

Revision ID: 20260612_0030
Revises: 20260612_0028
Create Date: 2026-06-12 11:00:00

Sprint Email v1. Adds the three tables that back the Gmail
integration:

- `email_threads`: one row per conversation; the unique key is
  `(gmail_account_user_id, gmail_thread_id)` so each user's Gmail
  threads can't collide across the CRM.
- `email_messages`: one row per individual message (outbound or
  inbound) inside a thread; unique by
  `(gmail_account_user_id, gmail_message_id)`.
- `gmail_pubsub_watches`: tracks the watch each user has registered
  with Gmail Push Notifications + the last `history_id` we
  processed. Used by the cron renewer (watches expire every 7 days)
  and the webhook to know where to resume.

Downgrade reversible: drop all three tables. No data migration —
the feature is new.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260612_0030"
down_revision: str | None = "20260612_0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "email_threads",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("contact_id", sa.String(length=36), nullable=True),
        sa.Column("initiated_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("gmail_thread_id", sa.String(length=255), nullable=False),
        sa.Column("gmail_account_user_id", sa.String(length=36), nullable=False),
        sa.Column("subject", sa.String(length=500), nullable=True),
        sa.Column("participants_json", sa.Text(), nullable=True),
        sa.Column("first_message_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("message_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "has_unread_replies",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "is_archived",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["contact_id"], ["contacts.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["initiated_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["gmail_account_user_id"], ["users.id"]),
        sa.UniqueConstraint(
            "gmail_account_user_id",
            "gmail_thread_id",
            name="uq_email_threads_account_thread",
        ),
    )
    op.create_index(
        "ix_email_threads_contact_last",
        "email_threads",
        ["contact_id", "last_message_at"],
    )
    op.create_index(
        "ix_email_threads_user_last",
        "email_threads",
        ["initiated_by_user_id", "last_message_at"],
    )

    op.create_table(
        "email_messages",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("thread_id", sa.String(length=36), nullable=False),
        sa.Column("gmail_message_id", sa.String(length=255), nullable=False),
        sa.Column("gmail_account_user_id", sa.String(length=36), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("from_email", sa.String(length=255), nullable=False),
        sa.Column("from_name", sa.String(length=255), nullable=True),
        sa.Column("to_emails_json", sa.Text(), nullable=False),
        sa.Column("cc_emails_json", sa.Text(), nullable=True),
        sa.Column("bcc_emails_json", sa.Text(), nullable=True),
        sa.Column("subject", sa.String(length=500), nullable=True),
        sa.Column("body_html", sa.Text(), nullable=True),
        sa.Column("body_text", sa.Text(), nullable=True),
        sa.Column("snippet", sa.String(length=255), nullable=True),
        sa.Column("attachments_json", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("contact_id", sa.String(length=36), nullable=True),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["thread_id"], ["email_threads.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["gmail_account_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(
            ["contact_id"], ["contacts.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.UniqueConstraint(
            "gmail_account_user_id",
            "gmail_message_id",
            name="uq_email_messages_account_message",
        ),
    )
    op.create_index(
        "ix_email_messages_thread_sent",
        "email_messages",
        ["thread_id", "sent_at"],
    )
    op.create_index(
        "ix_email_messages_contact_sent",
        "email_messages",
        ["contact_id", "sent_at"],
    )

    op.create_table(
        "gmail_pubsub_watches",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("history_id", sa.BigInteger(), nullable=False),
        sa.Column("watch_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_renewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("topic_name", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", name="uq_gmail_pubsub_watches_user"),
    )


def downgrade() -> None:
    op.drop_table("gmail_pubsub_watches")
    op.drop_index("ix_email_messages_contact_sent", table_name="email_messages")
    op.drop_index("ix_email_messages_thread_sent", table_name="email_messages")
    op.drop_table("email_messages")
    op.drop_index("ix_email_threads_user_last", table_name="email_threads")
    op.drop_index("ix_email_threads_contact_last", table_name="email_threads")
    op.drop_table("email_threads")
