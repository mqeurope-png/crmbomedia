"""segments.external_source + refresh tracking for Brevo-managed mirrors

Revision ID: 20260610_0024
Revises: 20260610_0023
Create Date: 2026-06-10 13:00:00

Sprint Brevo follow-up. Brevo segments live behind a UI that doesn't
expose the rule tree via API — only the current member list. Importing
them as native CRM segments would mean reverse-engineering the filters;
instead they ride as **mirrors**: an ordinary `segments` row marked as
externally-managed, with the member list refreshed periodically by a
connector job.

Implementation reuses the existing `is_dynamic=False` +
`static_contact_ids` machinery (no engine change). Three new
`segments` columns identify and timestamp the mirror:

- `external_source` — `"<system>:<account_id>:<external_id>"`.
  NULL on every CRM-native segment, populated for mirrors. Unique
  per (system, account_id, external_id) so a Brevo segment can't be
  imported twice into the same account.
- `external_last_refreshed_at` — when the periodic job last refreshed
  the member list.
- `external_refresh_interval_minutes` — operator-tunable period.
  NULL → use the system default from `BREVO_SEGMENTS_REFRESH_INTERVAL_HOURS`.

A partial unique index can't be expressed portably (SQLite supports
partial, MySQL doesn't), so we settle for a non-unique index on
`external_source` to make the connector's lookup fast.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260610_0024"
down_revision: str | None = "20260610_0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("segments") as batch:
        batch.add_column(
            sa.Column("external_source", sa.String(length=150), nullable=True)
        )
        batch.add_column(
            sa.Column(
                "external_last_refreshed_at",
                sa.DateTime(timezone=True),
                nullable=True,
            )
        )
        batch.add_column(
            sa.Column(
                "external_refresh_interval_minutes",
                sa.Integer(),
                nullable=True,
            )
        )
    op.create_index(
        "ix_segments_external_source",
        "segments",
        ["external_source"],
    )


def downgrade() -> None:
    op.drop_index("ix_segments_external_source", table_name="segments")
    with op.batch_alter_table("segments") as batch:
        batch.drop_column("external_refresh_interval_minutes")
        batch.drop_column("external_last_refreshed_at")
        batch.drop_column("external_source")
