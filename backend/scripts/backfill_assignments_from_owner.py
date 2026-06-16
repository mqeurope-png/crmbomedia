"""Backfill `contact_assignments` from `contacts.owner_user_id`.

Sprint Reglas-Assign — PR-A. The Alembic migration 0047 already runs
this backfill inline. This standalone script is the idempotent re-run
path for ops: if the migration's data step was skipped (e.g. a
restore, or owner_user_id values written after the migration), this
converges the assignment table to "one primary per owned contact"
without duplicating.

Idempotent: skips contacts that already have an assignment for their
owner. `--dry-run` rolls back; `--limit` caps for staged runs.

Usage:
    INTEGRATION_SECRETS_KEY=…  python -m scripts.backfill_assignments_from_owner
    INTEGRATION_SECRETS_KEY=…  python -m scripts.backfill_assignments_from_owner --dry-run
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_engine
from app.models.crm import Contact, ContactAssignment

log = logging.getLogger("backfill_assignments")
logging.basicConfig(level=logging.INFO, format="%(message)s")


def backfill(*, dry_run: bool, batch: int = 500, limit: int | None = None) -> dict[str, int]:
    counts = {"scanned": 0, "assignments_added": 0, "skipped_existing": 0}
    engine = get_engine()
    with Session(engine) as session:
        stmt = (
            select(Contact.id, Contact.owner_user_id)
            .where(Contact.owner_user_id.is_not(None))
            .order_by(Contact.id)
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        rows = list(session.execute(stmt).all())
        counts["scanned"] = len(rows)

        # Pre-load existing (contact_id, user_id) pairs so the dedupe
        # is one query, not one-per-row.
        existing: set[tuple[str, str]] = {
            (ca.contact_id, ca.user_id)
            for ca in session.scalars(select(ContactAssignment))
        }

        now = datetime.now(UTC)
        pending = 0
        for contact_id, owner in rows:
            if (contact_id, owner) in existing:
                counts["skipped_existing"] += 1
                continue
            row = ContactAssignment(
                contact_id=contact_id,
                user_id=owner,
                is_primary=True,
                assigned_by_user_id=None,
                assigned_at=now,
                source="backfill",
            )
            row.created_at = now
            row.updated_at = now
            session.add(row)
            existing.add((contact_id, owner))
            counts["assignments_added"] += 1
            pending += 1
            if not dry_run and pending >= batch:
                session.commit()
                pending = 0
        if not dry_run and pending:
            session.commit()
        if dry_run:
            session.rollback()
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    summary = backfill(dry_run=args.dry_run, limit=args.limit)
    log.info(
        "backfill summary scanned=%d added=%d skipped=%d dry_run=%s",
        summary["scanned"],
        summary["assignments_added"],
        summary["skipped_existing"],
        args.dry_run,
    )
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
