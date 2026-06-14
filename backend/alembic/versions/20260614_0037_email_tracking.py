"""email tracking — events, tokens, unsubscribes, user pref column

Revision ID: 20260614_0037
Revises: 20260614_0036
Create Date: 2026-06-14 11:30:00

Sprint Email v2.3 — backend foundation for open/click/unsubscribe/
bounce tracking on the 1-a-1 send flow. Brevo campaigns already have
their own tracking story so they stay untouched.

Three new tables + one new column on `users`:

- `email_message_events` — append-only feed of lifecycle events on
  an outbound message (sent, delivered, open, click, bounce,
  complaint, unsubscribe). The service layer dedupes opens/clicks
  within a 60-second window so a recipient's preview pane doesn't
  inflate the counts.
- `email_message_tokens` — one URL-safe random token per outbound
  message. Same token powers both the open pixel and the click
  redirect; the click handler reads the destination URL from a
  `?d=<base64>` query param so the token table stays small.
- `email_unsubscribes` — opt-out events tied to a contact + scope
  ('all', 'marketing', 'transactional'). The token surface backs
  both the RFC 8058 One-Click POST endpoint and a confirm-then-POST
  HTML page; the source column records which mechanic produced the
  row.
- `users.email_include_unsubscribe_default` — per-operator default
  for the "incluir opción de baja" checkbox in the send modal.

All booleans use `sa.false()` server defaults to stay MySQL-8 safe.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260614_0037"
down_revision: str | None = "20260614_0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "email_include_unsubscribe_default",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    op.create_table(
        "email_message_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("message_id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ip", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.String(length=500), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["message_id"], ["email_messages.id"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_email_message_events_message",
        "email_message_events",
        ["message_id"],
    )
    op.create_index(
        "ix_email_message_events_type_time",
        "email_message_events",
        ["event_type", "occurred_at"],
    )

    op.create_table(
        "email_message_tokens",
        sa.Column("token", sa.String(length=64), primary_key=True),
        sa.Column("message_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["message_id"], ["email_messages.id"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_email_message_tokens_message",
        "email_message_tokens",
        ["message_id"],
    )

    op.create_table(
        "email_unsubscribes",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("contact_id", sa.String(length=36), nullable=False),
        sa.Column(
            "scope",
            sa.String(length=32),
            nullable=False,
            server_default="marketing",
        ),
        sa.Column(
            "source",
            sa.String(length=60),
            nullable=False,
            server_default="one-click",
        ),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("unsubscribed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("message_id", sa.String(length=36), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["contact_id"], ["contacts.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["message_id"], ["email_messages.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint("token", name="uq_email_unsubscribes_token"),
    )
    op.create_index(
        "ix_email_unsubscribes_contact_scope",
        "email_unsubscribes",
        ["contact_id", "scope"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_email_unsubscribes_contact_scope",
        table_name="email_unsubscribes",
    )
    op.drop_table("email_unsubscribes")
    op.drop_index(
        "ix_email_message_tokens_message", table_name="email_message_tokens"
    )
    op.drop_table("email_message_tokens")
    op.drop_index(
        "ix_email_message_events_type_time",
        table_name="email_message_events",
    )
    op.drop_index(
        "ix_email_message_events_message",
        table_name="email_message_events",
    )
    op.drop_table("email_message_events")
    op.drop_column("users", "email_include_unsubscribe_default")
