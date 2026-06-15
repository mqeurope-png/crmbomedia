"""contact_phones + contact_emails + socials on contacts

Revision ID: 20260615_0043
Revises: 20260615_0042
Create Date: 2026-06-15 11:00:00

Sprint Empresas — sub-PR 3/4. Multi-channel contact data:

- `contact_phones` — one row per phone number a contact owns.
  `label` is the source-system handle (Brevo `TELEFONO_3` /
  Agile `mobile` / operator-typed "Centralita"), preserved
  verbatim. `is_primary` flags the canonical number that the
  contact's main `phone` column mirrors; backend enforces at
  most one primary per contact in app logic + a partial-unique
  index would be MySQL-tricky so we lean on the app guard.
- `contact_emails` — same shape, plus `is_verified` for
  systems that distinguish "delivered" from "confirmed".
- `contacts` gains `twitter_url`, `facebook_url`, and
  `social_profiles` (JSON text) so the operator can curate
  niche networks (Skype, Github, Blog) without each one
  spawning a column.

`contact_phones`/`contact_emails` `contact_id` FK is CASCADE so a
contact delete sweeps the channels — there's no scenario where an
orphan row makes sense.

Index notes:
- `(contact_id)` for the per-contact list.
- `(contact_id, is_primary)` so the v2.4d email/dialer code can
  find the primary in a single query.
- a hard `(contact_id, number_normalised)` / `(contact_id,
  email_normalised)` UNIQUE would be ideal for dedupe; we leave
  it to the mapper / API layer for now (avoids a hashing column
  on a 20k-contact-import migration).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260615_0043"
down_revision: str | None = "20260615_0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "contact_phones",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("contact_id", sa.String(length=36), nullable=False),
        sa.Column("label", sa.String(length=80), nullable=True),
        sa.Column("number", sa.String(length=80), nullable=False),
        sa.Column(
            "is_primary",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("source", sa.String(length=40), nullable=False, server_default="manual"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["contact_id"], ["contacts.id"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_contact_phones_contact", "contact_phones", ["contact_id"]
    )
    op.create_index(
        "ix_contact_phones_primary",
        "contact_phones",
        ["contact_id", "is_primary"],
    )

    op.create_table(
        "contact_emails",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("contact_id", sa.String(length=36), nullable=False),
        sa.Column("label", sa.String(length=80), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column(
            "is_primary",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "is_verified",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("source", sa.String(length=40), nullable=False, server_default="manual"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["contact_id"], ["contacts.id"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_contact_emails_contact", "contact_emails", ["contact_id"]
    )
    op.create_index(
        "ix_contact_emails_primary",
        "contact_emails",
        ["contact_id", "is_primary"],
    )

    op.add_column(
        "contacts",
        sa.Column("twitter_url", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "contacts",
        sa.Column("facebook_url", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "contacts",
        sa.Column("social_profiles_json", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("contacts", "social_profiles_json")
    op.drop_column("contacts", "facebook_url")
    op.drop_column("contacts", "twitter_url")

    op.drop_index("ix_contact_emails_primary", table_name="contact_emails")
    op.drop_index("ix_contact_emails_contact", table_name="contact_emails")
    op.drop_table("contact_emails")

    op.drop_index("ix_contact_phones_primary", table_name="contact_phones")
    op.drop_index("ix_contact_phones_contact", table_name="contact_phones")
    op.drop_table("contact_phones")
