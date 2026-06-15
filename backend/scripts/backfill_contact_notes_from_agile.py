"""Backfill `contact_notes` with AgileCRM `Note1..Note10` properties.

Sprint Empresas — sub-PR 4/4. The new mapper writes `Note1..Note10`
into `contact_notes` on every fresh AgileCRM sync, but historic
contacts already imported before sub-PR 4 have those properties
discarded (they're outside `CUSTOM_FIELDS_WHITELIST`, so they
never landed in `custom_fields_json` either — the values are only
recoverable by re-fetching from the AgileCRM API).

This script walks every Contact that carries an AgileCRM
`ExternalReference`, fetches the live contact payload, runs
`extract_agilecrm_notes` + the same dedupe shape as
`reconcile_agile_notes` (content + source + contact_id), and
inserts the missing rows.

Idempotent — re-running converges instead of duplicating. The
`--dry-run` flag rolls the transaction back so an operator can
preview the diff. `--limit` caps the contact count for staged
runs (small batches against the prod API quota first).

Usage:
    INTEGRATION_SECRETS_KEY=…  python -m scripts.backfill_contact_notes_from_agile
    INTEGRATION_SECRETS_KEY=…  python -m scripts.backfill_contact_notes_from_agile --dry-run
    INTEGRATION_SECRETS_KEY=…  python -m scripts.backfill_contact_notes_from_agile --limit 100

Commits in batches of 100 so a partial run still makes progress
if the AgileCRM API rate-limits midway.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_engine
from app.integrations.agilecrm.client import AgileCRMClient
from app.integrations.agilecrm.mapper import extract_agilecrm_notes
from app.models.crm import Contact, ContactNote, ExternalReference, ExternalSystem

log = logging.getLogger("backfill_notes")
logging.basicConfig(level=logging.INFO, format="%(message)s")


def _existing_keys(
    session: Session, contact_id: str
) -> set[tuple[str, str]]:
    """Dedup key set for this contact — mirrors `reconcile_agile_notes`."""
    return {
        (row.source, row.content)
        for row in session.scalars(
            select(ContactNote).where(ContactNote.contact_id == contact_id)
        )
    }


def _apply_notes(
    session: Session,
    *,
    contact_id: str,
    payload: dict[str, Any],
    now: datetime,
) -> int:
    notes = extract_agilecrm_notes(payload)
    if not notes:
        return 0
    seen = _existing_keys(session, contact_id)
    added = 0
    for entry in notes:
        key = (entry["source"], entry["content"])
        if key in seen:
            continue
        row = ContactNote(
            contact_id=contact_id,
            content=entry["content"],
            source=entry["source"],
            pinned=False,
            created_by_user_id=None,
        )
        row.created_at = now
        row.updated_at = now
        session.add(row)
        seen.add(key)
        added += 1
    return added


async def _drive(
    *, dry_run: bool, batch: int, limit: int | None
) -> dict[str, int]:
    counts = {
        "scanned": 0,
        "fetched": 0,
        "notes_added": 0,
        "failed": 0,
    }
    engine = get_engine()
    with Session(engine) as session:
        stmt = (
            select(
                Contact.id,
                ExternalReference.account_id,
                ExternalReference.external_id,
            )
            .join(ExternalReference, ExternalReference.contact_id == Contact.id)
            .where(ExternalReference.system == ExternalSystem.AGILECRM)
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        targets = list(session.execute(stmt).all())
        counts["scanned"] = len(targets)

        # Group by account so one AgileCRMClient covers many contacts.
        by_account: dict[str, list[tuple[str, str]]] = {}
        for contact_id, account_id, external_id in targets:
            by_account.setdefault(account_id, []).append(
                (contact_id, external_id)
            )

        now = datetime.now(UTC)
        pending = 0
        for account_id, rows in by_account.items():
            try:
                async with AgileCRMClient(session, account_id) as client:
                    for contact_id, external_id in rows:
                        try:
                            payload = await client.get_contact(external_id)
                        except Exception as exc:  # noqa: BLE001
                            counts["failed"] += 1
                            log.warning(
                                "fetch failed contact_id=%s external_id=%s: %s",
                                contact_id,
                                external_id,
                                exc,
                            )
                            continue
                        counts["fetched"] += 1
                        added = _apply_notes(
                            session,
                            contact_id=contact_id,
                            payload=payload,
                            now=now,
                        )
                        counts["notes_added"] += added
                        pending += added
                        if not dry_run and pending >= batch:
                            session.commit()
                            pending = 0
            except Exception as exc:  # noqa: BLE001
                # An account-level failure (bad creds, disabled, …)
                # shouldn't take the whole backfill down — the next
                # account still gets a chance.
                log.warning(
                    "account %s failed mid-backfill: %s", account_id, exc
                )
                session.rollback()
                pending = 0
        if not dry_run and pending:
            session.commit()
        if dry_run:
            session.rollback()
    return counts


def backfill(
    *, dry_run: bool, batch: int = 100, limit: int | None = None
) -> dict[str, int]:
    return asyncio.run(_drive(dry_run=dry_run, batch=batch, limit=limit))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    summary = backfill(dry_run=args.dry_run, limit=args.limit)
    log.info(
        "backfill summary scanned=%d fetched=%d notes_added=%d failed=%d dry_run=%s",
        summary["scanned"],
        summary["fetched"],
        summary["notes_added"],
        summary["failed"],
        args.dry_run,
    )
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
