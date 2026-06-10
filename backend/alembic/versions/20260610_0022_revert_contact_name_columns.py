"""revert contacts.first_name/last_name to declared lengths after manual prod widening

Revision ID: 20260610_0022
Revises: 20260610_0021
Create Date: 2026-06-10 12:00:00

Sprint Brevo follow-up. Production had `first_name` widened from
`VARCHAR(120)` to `VARCHAR(500)` by hand (and `last_name` from
`VARCHAR(160)` to `VARCHAR(500)`) to unblock a sync that crashed on a
240-char name. The right fix is the mapper truncating the offender;
this migration brings the schema back in line.

Two steps in `upgrade`:

1. Truncate any pre-existing row whose value already exceeds the
   target size so the subsequent `alter_column` doesn't fail on a
   strict-mode MySQL.
2. Shrink the columns back to the declared lengths.

`downgrade` widens to VARCHAR(500) without touching data — safe and
lossless. SQLite test runs hit the noop path because the columns
were never widened in the test schema.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260610_0022"
down_revision: str | None = "20260610_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FIRST_NAME_LEN = 120
LAST_NAME_LEN = 160


def _truncate(column: str, max_len: int) -> None:
    """Replace any value > `max_len` with its first `max_len-1` chars
    plus the standard truncation marker. SQLite supports SUBSTR/
    LENGTH; MySQL supports SUBSTRING/CHAR_LENGTH. We pick the SQL-
    standard `SUBSTR`/`LENGTH` form so both dialects run the same
    statement."""
    op.execute(
        sa.text(
            f"UPDATE contacts SET {column} = SUBSTR({column}, 1, :keep) || '…' "
            f"WHERE {column} IS NOT NULL AND LENGTH({column}) > :max"
        ).bindparams(keep=max_len - 1, max=max_len)
    )


def upgrade() -> None:
    _truncate("first_name", FIRST_NAME_LEN)
    _truncate("last_name", LAST_NAME_LEN)
    with op.batch_alter_table("contacts") as batch:
        batch.alter_column(
            "first_name",
            existing_type=sa.String(length=500),
            type_=sa.String(length=FIRST_NAME_LEN),
            existing_nullable=False,
        )
        batch.alter_column(
            "last_name",
            existing_type=sa.String(length=500),
            type_=sa.String(length=LAST_NAME_LEN),
            existing_nullable=True,
        )


def downgrade() -> None:
    with op.batch_alter_table("contacts") as batch:
        batch.alter_column(
            "first_name",
            existing_type=sa.String(length=FIRST_NAME_LEN),
            type_=sa.String(length=500),
            existing_nullable=False,
        )
        batch.alter_column(
            "last_name",
            existing_type=sa.String(length=LAST_NAME_LEN),
            type_=sa.String(length=500),
            existing_nullable=True,
        )
