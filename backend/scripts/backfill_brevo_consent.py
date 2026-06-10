"""One-off: nudge Brevo-sourced contacts from `unknown` to `granted`.

The first production run of the Brevo importer (PR #51) left 17.7k
contacts on `marketing_consent='unknown'` because the mapper only
read `emailBlacklisted` and let everything else fall through. The
real semantic is "Brevo treats list membership as the opt-in" — a
contact pulled out of Brevo who isn't blacklisted IS granted. PR
follow-up #52 fixes the mapper; this script catches up the rows that
were already imported by the old code.

Idempotent: only touches rows that are still `unknown`. A second run
prints `0`.

Resolution of "Brevo-sourced" prefers `external_references` (correct
across consolidated contacts shared with AgileCRM); falls back to
`origin='brevo'` for rows imported before the references model
landed.

    docker compose --env-file .env.production exec api \
        python scripts/backfill_brevo_consent.py
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import or_, select  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.db.session import get_engine  # noqa: E402
from app.models.crm import (  # noqa: E402
    Contact,
    ExternalReference,
    ExternalSystem,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("backfill_brevo_consent")


def backfill(session: Session) -> int:
    """Flip `unknown` → `granted` for Brevo-sourced contacts. Returns
    the number of rows touched."""
    brevo_contact_ids = session.scalars(
        select(ExternalReference.contact_id).where(
            ExternalReference.system == ExternalSystem.BREVO
        )
    ).all()
    candidates = list(
        session.scalars(
            select(Contact).where(
                Contact.marketing_consent == "unknown",
                or_(
                    Contact.id.in_(brevo_contact_ids) if brevo_contact_ids else False,
                    Contact.origin == "brevo",
                ),
            )
        )
    )
    for contact in candidates:
        contact.marketing_consent = "granted"
    session.commit()
    return len(candidates)


def main() -> int:
    factory = sessionmaker(bind=get_engine(), autoflush=False, autocommit=False)
    with factory() as session:
        affected = backfill(session)
    logger.info(
        "Backfill complete: %d contactos Brevo pasados de unknown a granted",
        affected,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
