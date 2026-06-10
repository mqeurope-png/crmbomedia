"""make contacts.email nullable for tolerant ingestion

Revision ID: 20260606_0019
Revises: 20260526_0018
Create Date: 2026-06-06 00:00:00

Sprint UX — `Contact.email` was NOT NULL. AgileCRM ingests
occasionally surface obvious garbage ("emete@emete@emete.cat") and
the mapper had to either drop the contact or insert the garbage; the
latter blew up the read schema as soon as anyone listed contacts.

With this migration the mapper can write NULL when the address can't
be parsed; the unique index keeps working (MySQL and SQLite both
allow multiple NULLs under UNIQUE) and `is_email_valid` keeps its
auditing role for rows that DO have a value but failed the validator.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260606_0019"
down_revision: str | None = "20260526_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("contacts") as batch:
        batch.alter_column(
            "email",
            existing_type=sa.String(length=255),
            nullable=True,
        )


def downgrade() -> None:
    # Backfills the column with a synthetic placeholder before flipping
    # NOT NULL back on — otherwise downgrading after a bad-row landed
    # would crash the migration.
    op.execute(
        "UPDATE contacts "
        "SET email = CONCAT('invalid-', id, '@no-email.invalid'), "
        "    is_email_valid = 0 "
        "WHERE email IS NULL"
    )
    with op.batch_alter_table("contacts") as batch:
        batch.alter_column(
            "email",
            existing_type=sa.String(length=255),
            nullable=False,
        )
