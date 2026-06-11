#!/usr/bin/env python3
"""Normalise `Contact.address_country` to ISO Alpha-2 + sync the
display name into `address_country_name`.

Mini-PR C Fase 3. Before this PR the mappers stored whatever the
remote system handed over: ISO codes mixed with full names mixed
with localised typing ("España" / "Spain" / "ES"). This script
sweeps the existing rows through the same `normalize_country`
helper the mappers now use so the data matches the new convention.

Idempotent: re-running on an already-normalised database is a
no-op.

Usage (inside the api container):

    docker compose --env-file .env.production exec api \\
        python scripts/normalize_country_fields.py [--dry-run]
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Add backend/ to PYTHONPATH so this script runs both inside the
# container (where the workdir already is /app) and outside via
# `python backend/scripts/...`.
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.db.session import get_engine  # noqa: E402
from app.integrations.country_codes import normalize_country  # noqa: E402
from app.models.crm import Contact  # noqa: E402

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
)
logger = logging.getLogger("normalize_country_fields")


def normalize_all(*, dry_run: bool = False) -> tuple[int, int]:
    """Iterate every contact with a country-ish field and replace it
    with the canonical pair. Returns `(scanned, changed)`."""
    scanned = 0
    changed = 0
    with Session(get_engine()) as session:
        stmt = select(Contact).where(
            (Contact.address_country.is_not(None))
            | (Contact.address_country_name.is_not(None))
        )
        for contact in session.scalars(stmt):
            scanned += 1
            raw = contact.address_country or contact.address_country_name
            iso, name = normalize_country(raw)
            if iso is None:
                # Unknown country — leave the row alone so we don't
                # destroy the operator's input. Log it so we can audit
                # the long tail.
                logger.warning(
                    "country.unknown id=%s raw=%r — left untouched",
                    contact.id,
                    raw,
                )
                continue
            if (
                contact.address_country == iso
                and contact.address_country_name == name
            ):
                continue
            logger.info(
                "country.normalize id=%s %r/%r -> %s/%s",
                contact.id,
                contact.address_country,
                contact.address_country_name,
                iso,
                name,
            )
            if not dry_run:
                contact.address_country = iso
                contact.address_country_name = name
            changed += 1
        if not dry_run:
            session.commit()
    return scanned, changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log what would change without writing to the database.",
    )
    args = parser.parse_args()
    scanned, changed = normalize_all(dry_run=args.dry_run)
    logger.info(
        "country.backfill_done scanned=%d changed=%d dry_run=%s",
        scanned,
        changed,
        args.dry_run,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
