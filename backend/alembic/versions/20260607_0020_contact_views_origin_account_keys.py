"""rename contact_views.filters_json origin_account_id to origin_account_keys

Revision ID: 20260607_0020
Revises: 20260606_0019
Create Date: 2026-06-07 00:00:00

Sprint UX — saved-view filters used `{origin_system, origin_account_id}`
to scope the contact list to a single integration system + account.
With 9 AgileCRM accounts the operator needs to pick concrete
combinations, so we move to `origin_account_keys: ["system:account_id", ...]`.

Data transformation:

- `{origin_account_id: "default", origin_system: "agilecrm"}`
   →  `{origin_account_keys: ["agilecrm:default"]}`
- `{origin_account_id: "default"}` (no system, legacy)
   →  unchanged — we don't know which system the account belongs to,
       and the route layer keeps reading the legacy key alongside the
       new one as a fallback.
- `{origin_system: "agilecrm"}` (system only, legacy)
   →  unchanged — the route still accepts `origin_system` for
       backwards compatibility; the operator just sees broader filters
       than they would have if they had specified the account.

Both legacy keys remain readable on read-out via the route layer's
fallback so bookmarked URLs and any not-yet-migrated frontend code
keep working through the transition.
"""
from __future__ import annotations

import json
from collections.abc import Sequence

from alembic import op

revision: str = "20260607_0020"
down_revision: str | None = "20260606_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    rows = connection.exec_driver_sql(
        "SELECT id, filters_json FROM contact_views WHERE filters_json IS NOT NULL"
    ).fetchall()
    for row in rows:
        view_id, raw = row[0], row[1]
        if not raw:
            continue
        try:
            filters = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if not isinstance(filters, dict):
            continue
        origin_system = filters.get("origin_system")
        origin_account_id = filters.get("origin_account_id")
        keys = filters.get("origin_account_keys")
        if (
            origin_system
            and origin_account_id
            and (not isinstance(keys, list) or not keys)
        ):
            filters["origin_account_keys"] = [
                f"{origin_system}:{origin_account_id}"
            ]
            # Keep `origin_system` + `origin_account_id` so the route's
            # backwards-compat layer doesn't lose context for a view
            # that's about to be edited in the new builder.
            connection.exec_driver_sql(
                "UPDATE contact_views SET filters_json = ? WHERE id = ?",
                (json.dumps(filters, ensure_ascii=False), view_id),
            )


def downgrade() -> None:
    connection = op.get_bind()
    rows = connection.exec_driver_sql(
        "SELECT id, filters_json FROM contact_views WHERE filters_json IS NOT NULL"
    ).fetchall()
    for row in rows:
        view_id, raw = row[0], row[1]
        if not raw:
            continue
        try:
            filters = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if not isinstance(filters, dict):
            continue
        keys = filters.get("origin_account_keys")
        if isinstance(keys, list) and keys:
            first = str(keys[0])
            if ":" in first:
                system, _, account_id = first.partition(":")
                filters.setdefault("origin_system", system)
                filters.setdefault("origin_account_id", account_id)
            filters.pop("origin_account_keys", None)
            connection.exec_driver_sql(
                "UPDATE contact_views SET filters_json = ? WHERE id = ?",
                (json.dumps(filters, ensure_ascii=False), view_id),
            )
