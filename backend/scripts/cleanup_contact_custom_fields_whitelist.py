"""Purge non-whitelist keys from `contacts.custom_fields`.

Sprint Empresas — sub-PR 2 fix. The Brevo + Agile mappers used to
copy every unknown attribute into the contact's `custom_fields`
JSON, surfacing internal housekeeping (sib_contact_owner,
EXT_ID, TELEFONO_2..6, ETIQUETA, EMAILABLE_UNSUBSCRIBED, …) on
the ficha. The mapper now enforces a business-curated whitelist
(`brevo.mapper.CUSTOM_FIELDS_WHITELIST`); this script does the
same cleanup for historic rows.

Idempotent: a row whose JSON already only contains whitelist
keys is skipped, so re-running is cheap. NULL `custom_fields`
stay NULL.

Usage:
    INTEGRATION_SECRETS_KEY=…  python -m scripts.cleanup_contact_custom_fields_whitelist
    INTEGRATION_SECRETS_KEY=…  python -m scripts.cleanup_contact_custom_fields_whitelist --dry-run
    INTEGRATION_SECRETS_KEY=…  python -m scripts.cleanup_contact_custom_fields_whitelist --limit 100

Commits in batches of 200 so a partial run still makes progress.
"""
from __future__ import annotations

import argparse
import json
import logging
from collections import Counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_engine
from app.integrations.brevo.mapper import CUSTOM_FIELDS_WHITELIST
from app.models.crm import Contact

log = logging.getLogger("cleanup_custom_fields")
logging.basicConfig(level=logging.INFO, format="%(message)s")


def _decode(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def cleanup(
    *, dry_run: bool, batch: int = 200, limit: int | None = None
) -> dict[str, Any]:
    counts: dict[str, Any] = {
        "scanned": 0,
        "contacts_touched": 0,
        "keys_removed": 0,
        "rows_cleared_to_null": 0,
        "removed_key_freq": Counter(),
    }
    engine = get_engine()
    with Session(engine) as session:
        stmt = select(Contact).where(Contact.custom_fields.is_not(None))
        if limit is not None:
            stmt = stmt.limit(limit)
        contacts = list(session.scalars(stmt))
        counts["scanned"] = len(contacts)
        pending = 0
        for contact in contacts:
            decoded = _decode(contact.custom_fields)
            if not decoded:
                continue
            kept = {
                k: v
                for k, v in decoded.items()
                if str(k).upper() in CUSTOM_FIELDS_WHITELIST
                and v not in (None, "")
            }
            removed = {
                k: v for k, v in decoded.items() if k not in kept
            }
            if not removed:
                continue
            counts["contacts_touched"] += 1
            counts["keys_removed"] += len(removed)
            for k in removed:
                counts["removed_key_freq"][k] += 1
            if not kept:
                new_value: str | None = None
                counts["rows_cleared_to_null"] += 1
            else:
                new_value = json.dumps(kept, default=str)
            if not dry_run:
                contact.custom_fields = new_value
                pending += 1
                if pending >= batch:
                    session.commit()
                    pending = 0
        if not dry_run and pending:
            session.commit()
        if dry_run:
            session.rollback()
    # Cap the per-key frequency in the JSON output to the noisiest
    # 20 — a 7k-contact run with hundreds of stray keys is enough
    # signal without flooding the operator's terminal.
    top_removed = dict(counts["removed_key_freq"].most_common(20))
    counts["removed_key_freq"] = top_removed
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    summary = cleanup(dry_run=args.dry_run, limit=args.limit)
    log.info(
        "cleanup summary scanned=%d touched=%d keys_removed=%d "
        "rows_cleared_to_null=%d dry_run=%s",
        summary["scanned"],
        summary["contacts_touched"],
        summary["keys_removed"],
        summary["rows_cleared_to_null"],
        args.dry_run,
    )
    print(json.dumps(summary, default=str))


if __name__ == "__main__":
    main()
