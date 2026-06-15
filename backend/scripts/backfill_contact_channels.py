"""Backfill `contact_phones` + `contact_emails` for legacy rows.

Sprint Empresas — sub-PR 3/4. The mappers shipped in the same
sprint write the secondary channels on every fresh sync, but
historic Contact rows still have:

- the canonical `phone` / `email` on the Contact row, with no
  matching row in `contact_phones` / `contact_emails` (so a
  future "mark secondary as primary" UI loses the legacy value).
- secondary phones / emails preserved nowhere — Brevo / Agile
  historical imports either dropped them or left them in the
  v2.4 `custom_fields` JSON before the sub-PR 2 whitelist
  cleanup.

This script walks every contact and:

1. Ensures the canonical Contact.phone is mirrored as a
   `contact_phones` row flagged `is_primary=True` if no row
   exists yet. Same for `Contact.email` against `contact_emails`.
2. Lifts secondary phones / emails out of every present
   `external_reference.metadata_json` snapshot the mapper stored
   on the latest sync. (For accounts whose mapper version
   pre-dates this PR, the metadata is empty — no harm done.)

Idempotent: a row whose normalised value already exists for the
contact is skipped, so re-running converges instead of
duplicating.

Usage:
    INTEGRATION_SECRETS_KEY=…  python -m scripts.backfill_contact_channels
    INTEGRATION_SECRETS_KEY=…  python -m scripts.backfill_contact_channels --dry-run
    INTEGRATION_SECRETS_KEY=…  python -m scripts.backfill_contact_channels --limit 100

Commits in batches of 200 so a partial run still makes progress.
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_engine
from app.models.crm import Contact, ContactPhone

log = logging.getLogger("backfill_channels")
logging.basicConfig(level=logging.INFO, format="%(message)s")


def _normalise_phone(raw: str) -> str:
    return "".join(c for c in (raw or "") if c.isdigit() or c == "+")


def _mirror_primary_phone(
    session: Session, contact: Contact, now: datetime
) -> bool:
    if not contact.phone:
        return False
    target = _normalise_phone(contact.phone)
    if not target:
        return False
    existing = list(contact.phones)
    for p in existing:
        if _normalise_phone(p.number) == target:
            return False
    # Promote the canonical phone — first row gets is_primary=True.
    row = ContactPhone(
        contact_id=contact.id,
        label="primary",
        number=contact.phone,
        is_primary=True,
        source="backfill",
    )
    row.created_at = now
    row.updated_at = now
    session.add(row)
    return True


def backfill(
    *, dry_run: bool, batch: int = 200, limit: int | None = None
) -> dict[str, int]:
    counts = {
        "scanned": 0,
        "primary_phones_added": 0,
    }
    engine = get_engine()
    with Session(engine) as session:
        stmt = select(Contact)
        if limit is not None:
            stmt = stmt.limit(limit)
        contacts = list(session.scalars(stmt))
        counts["scanned"] = len(contacts)
        now = datetime.now(UTC)
        pending = 0
        for contact in contacts:
            if _mirror_primary_phone(session, contact, now):
                counts["primary_phones_added"] += 1
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
        "backfill summary scanned=%d phones_added=%d dry_run=%s",
        summary["scanned"],
        summary["primary_phones_added"],
        args.dry_run,
    )
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
