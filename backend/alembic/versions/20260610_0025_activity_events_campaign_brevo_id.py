"""activity_events.campaign_brevo_id + backfill from existing rows

Revision ID: 20260610_0025
Revises: 20260610_0024
Create Date: 2026-06-10 23:30:00

The `/campaigns/{id}/recipients/{event_type}` endpoint joined
`activity_events` to a campaign by the catch-all
`(system='brevo', account_id, event_type)` filter — which mixed every
campaign's recipients into the same response. Production runtime was
patched with `external_id LIKE 'backfill:{brevo_campaign_id}:%'`, but
that substring scan misses live webhook events (their `external_id`
is a deduped Brevo message-id, not a `backfill:` token) and can't use
an index.

Fix: a dedicated `campaign_brevo_id` column on `activity_events`,
indexed, populated by both writers (webhook mapper +
historical-backfill mapper) and consulted by the endpoint.

Backfill of existing rows:

1. Rows from the historical backfill carry the campaign id verbatim
   in `external_id` as `backfill:{brevo_campaign_id}:...`. We parse
   the int out with the dialect's substring/regex primitives.
2. Webhook rows store the whole event payload as JSON in `metadata`;
   Brevo's webhook carries `campaign-id` (a number). We pull it with
   `JSON_EXTRACT` on MySQL and Python json.loads on SQLite (CI runs
   on SQLite + JSON1 which doesn't ship a compatible JSON_EXTRACT
   in CPython's pysqlite build).
3. Rows that can't be resolved by either path stay NULL — the
   endpoint falls back to the `external_id LIKE 'backfill:%'` scan
   for them (defensive belt-and-braces, see commit 4).

The UPDATE runs against ~8k rows in production (Brevo events only),
expected < 30 s.
"""
from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260610_0025"
down_revision: str | None = "20260610_0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("activity_events") as batch:
        batch.add_column(
            sa.Column("campaign_brevo_id", sa.Integer(), nullable=True)
        )
    op.create_index(
        "ix_activity_events_campaign_brevo_id",
        "activity_events",
        ["campaign_brevo_id"],
    )
    _backfill_campaign_brevo_id()


def downgrade() -> None:
    op.drop_index(
        "ix_activity_events_campaign_brevo_id",
        table_name="activity_events",
    )
    with op.batch_alter_table("activity_events") as batch:
        batch.drop_column("campaign_brevo_id")


def _backfill_campaign_brevo_id() -> None:
    """Populate the new column from `external_id` (backfilled rows)
    and `metadata.campaign-id` / `metadata.campaign_brevo_id`
    (webhook rows). Runs on every Brevo activity event; the operation
    is idempotent because it only writes when `campaign_brevo_id IS
    NULL`."""
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "mysql":
        # 1. Rows whose external_id starts with `backfill:<int>:...`.
        bind.execute(
            sa.text(
                """
                UPDATE activity_events
                SET campaign_brevo_id = CAST(
                    SUBSTRING_INDEX(
                        SUBSTRING(external_id, 10), ':', 1
                    ) AS UNSIGNED
                )
                WHERE system = 'brevo'
                  AND campaign_brevo_id IS NULL
                  AND external_id LIKE 'backfill:%'
                  AND SUBSTRING_INDEX(
                          SUBSTRING(external_id, 10), ':', 1
                      ) REGEXP '^[0-9]+$'
                """
            )
        )
        # 2. Webhook rows: try both keys Brevo uses across payload
        # versions.
        bind.execute(
            sa.text(
                """
                UPDATE activity_events
                SET campaign_brevo_id = CAST(
                    COALESCE(
                        JSON_EXTRACT(metadata, '$."campaign-id"'),
                        JSON_EXTRACT(metadata, '$.campaign_brevo_id'),
                        JSON_EXTRACT(metadata, '$.campaign_id')
                    ) AS UNSIGNED
                )
                WHERE system = 'brevo'
                  AND campaign_brevo_id IS NULL
                  AND metadata IS NOT NULL
                  AND (
                    JSON_EXTRACT(metadata, '$."campaign-id"') IS NOT NULL
                    OR JSON_EXTRACT(metadata, '$.campaign_brevo_id') IS NOT NULL
                    OR JSON_EXTRACT(metadata, '$.campaign_id') IS NOT NULL
                  )
                """
            )
        )
        return

    # SQLite path (and any other dialect): walk rows in Python. The
    # CI suite runs against in-memory SQLite which doesn't carry a
    # compatible JSON_EXTRACT in all builds, so this stays the
    # portable fallback. Sub-second on the typical test fixture.
    rows = bind.execute(
        sa.text(
            "SELECT id, external_id, metadata FROM activity_events "
            "WHERE system = 'brevo' AND campaign_brevo_id IS NULL"
        )
    ).fetchall()
    for row_id, external_id, metadata in rows:
        campaign_id = _resolve_campaign_id_py(external_id, metadata)
        if campaign_id is None:
            continue
        bind.execute(
            sa.text(
                "UPDATE activity_events SET campaign_brevo_id = :cid "
                "WHERE id = :rid"
            ),
            {"cid": campaign_id, "rid": row_id},
        )


def _resolve_campaign_id_py(
    external_id: str | None, metadata: str | None
) -> int | None:
    if external_id and external_id.startswith("backfill:"):
        rest = external_id[len("backfill:") :]
        head = rest.split(":", 1)[0]
        if head.isdigit():
            return int(head)
    if not metadata:
        return None
    try:
        decoded = json.loads(metadata)
    except (TypeError, ValueError):
        return None
    if not isinstance(decoded, dict):
        return None
    for key in ("campaign-id", "campaign_brevo_id", "campaign_id"):
        value = decoded.get(key)
        if isinstance(value, (int, str)) and str(value).isdigit():
            return int(value)
    return None
