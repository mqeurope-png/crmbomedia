"""contact_notes table for Agile Note1..Note10 import + manual notes

Revision ID: 20260615_0045
Revises: 20260615_0044
Create Date: 2026-06-15 13:00:00

Sprint Empresas — sub-PR 4/4 (último). Backing storage for the
new "Notas" section on the contact ficha:

- Imports the `Note1..Note10` properties AgileCRM accounts use as
  free-form annotations on the contact record (separate from the
  `/dev/api/contacts/{id}/notes` sub-resource that the legacy
  `notes` table already syncs — those are activity-stream notes;
  `Note1..Note10` are CRM-form fields the operator fills in).
- Hosts manually-written notes the operator adds from the UI.

`content` is `MEDIUMTEXT` (≈16MB) because the AgileCRM payload has
no length cap on the `Note*` properties — accounts use them as
scratchpads + paste meeting transcripts. `source` discriminates
provenance (`agile:Note1` … `agile:Note10` / `manual`). `pinned`
floats important notes to the top of the list. `created_by_user_id`
is NULL for imported rows; the operator sees the source instead.

The (contact_id, source, content) triplet is the dedupe key the
mapper/backfill uses to stay idempotent — no DB unique index
because manual notes legitimately share content (e.g. two "call
later" entries) and we don't want to block the operator.

CASCADE on `contact_id` matches the rest of the per-contact tables
(phones, emails, tags) — a contact delete sweeps every note row.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "20260615_0045"
down_revision: str | None = "20260615_0044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "contact_notes",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("contact_id", sa.String(length=36), nullable=False),
        sa.Column(
            "content",
            sa.Text().with_variant(mysql.MEDIUMTEXT(), "mysql"),
            nullable=False,
        ),
        sa.Column(
            "source",
            sa.String(length=40),
            nullable=False,
            server_default="manual",
        ),
        sa.Column(
            "pinned",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["contact_id"], ["contacts.id"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_contact_notes_contact", "contact_notes", ["contact_id"]
    )
    op.create_index(
        "ix_contact_notes_pinned",
        "contact_notes",
        ["contact_id", "pinned"],
    )


def downgrade() -> None:
    op.drop_index("ix_contact_notes_pinned", table_name="contact_notes")
    op.drop_index("ix_contact_notes_contact", table_name="contact_notes")
    op.drop_table("contact_notes")
