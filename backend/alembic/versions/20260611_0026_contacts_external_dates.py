"""contacts.created_at_external + updated_at_external + backfill from refs

Revision ID: 20260611_0026
Revises: 20260610_0025
Create Date: 2026-06-11 09:00:00

The contact card showed the CRM row's `created_at` (when the sync
first wrote the row, e.g. May 2026) as if it were the contact's real
age. The operator wants the source-system date instead ("entered
Brevo in March 2025"). Two nullable columns carry it:

- `created_at_external` — the OLDEST creation across every system the
  contact lives in (the earliest system is the real origin).
- `updated_at_external` — the NEWEST modification across systems.

The connector mappers populate them going forward. This migration
also backfills existing rows — and the data is already on hand: the
`external_references` rows the mappers have always written carry
`external_created_at` / `external_updated_at` mirrored from each
payload. So the backfill is a straight aggregate, no JSON parsing
needed:

    created_at_external = MIN(external_references.external_created_at)
    updated_at_external = MAX(external_references.external_updated_at)

Contacts with no dated reference stay NULL — we never invent a date.
Idempotent: re-running recomputes the same aggregate. ~18k contacts
in production; the two correlated UPDATEs finish well under a minute.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260611_0026"
down_revision: str | None = "20260610_0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("contacts") as batch:
        batch.add_column(
            sa.Column("created_at_external", sa.DateTime(timezone=True), nullable=True)
        )
        batch.add_column(
            sa.Column("updated_at_external", sa.DateTime(timezone=True), nullable=True)
        )
    _backfill_external_dates(op.get_bind())


def downgrade() -> None:
    with op.batch_alter_table("contacts") as batch:
        batch.drop_column("updated_at_external")
        batch.drop_column("created_at_external")


def _backfill_external_dates(bind: sa.engine.Connection) -> None:
    """Fill the two new columns from the existing per-system reference
    timestamps. Portable across MySQL + SQLite (correlated subquery,
    no dialect-specific JSON functions). Only writes where the
    aggregate is non-NULL so a contact without dated references stays
    NULL rather than getting a bogus value."""
    bind.execute(
        sa.text(
            """
            UPDATE contacts
            SET created_at_external = (
                SELECT MIN(er.external_created_at)
                FROM external_references er
                WHERE er.contact_id = contacts.id
                  AND er.external_created_at IS NOT NULL
            )
            WHERE EXISTS (
                SELECT 1 FROM external_references er
                WHERE er.contact_id = contacts.id
                  AND er.external_created_at IS NOT NULL
            )
            """
        )
    )
    bind.execute(
        sa.text(
            """
            UPDATE contacts
            SET updated_at_external = (
                SELECT MAX(er.external_updated_at)
                FROM external_references er
                WHERE er.contact_id = contacts.id
                  AND er.external_updated_at IS NOT NULL
            )
            WHERE EXISTS (
                SELECT 1 FROM external_references er
                WHERE er.contact_id = contacts.id
                  AND er.external_updated_at IS NOT NULL
            )
            """
        )
    )
