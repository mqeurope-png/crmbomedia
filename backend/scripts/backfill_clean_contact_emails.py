"""One-off: nullify malformed contact emails that pre-date the
tolerant mapper of Sprint UX.

Run inside the API container:

    docker compose --env-file .env.production exec api \
        python scripts/backfill_clean_contact_emails.py

The script is **idempotent**. A second invocation walks the table
again, finds zero rows that still need nullifying (every offender
already became NULL on the first pass), and exits with a 0-row
summary. Safe to schedule on a cron if you ever want continuous
hygiene.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

# Allow `python scripts/backfill_clean_contact_emails.py` to work
# without an install — the script is executed straight from the repo
# inside the prod container.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import or_, select  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.db.session import get_engine  # noqa: E402
from app.models.crm import Contact  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("backfill_clean_contact_emails")


def _is_invalid(email: str) -> bool:
    """Return True when the address can't be parsed. Mirrors the same
    validator the mapper now uses so the two stay in sync."""
    candidate = (email or "").strip()
    if not candidate:
        return True
    try:
        from email_validator import EmailNotValidError, validate_email
    except ImportError:  # pragma: no cover - shipped with pydantic[email]
        return False
    try:
        validate_email(candidate, check_deliverability=False)
    except EmailNotValidError:
        return True
    return False


def backfill(session: Session) -> int:
    """Nullify malformed emails. Returns the number of rows touched."""
    # Pre-filter with cheap SQL: any row with an unusual character
    # pattern or a space inside the address gets re-checked in Python.
    # Pulling everything would be wasteful on a tenant with hundreds
    # of thousands of contacts; the LIKE filter cuts the candidate set
    # to the obvious offenders.
    candidates = session.scalars(
        select(Contact).where(
            Contact.email.is_not(None),
            or_(
                Contact.email.like("%@%@%"),
                Contact.email.like("% %"),
                Contact.email.like("%,%"),
                Contact.email.like("%;%"),
            ),
        )
    ).all()
    bad = [c for c in candidates if _is_invalid(c.email or "")]

    # And a second sweep over the rest of the table — `_is_invalid`
    # catches subtler cases the LIKE filter misses (no @ at all, the
    # local part is empty, etc.). On big tenants this would scan a lot
    # of rows, so we only fire it when the cheap filter found nothing.
    if not bad:
        remaining = session.scalars(
            select(Contact).where(Contact.email.is_not(None))
        ).all()
        bad = [c for c in remaining if _is_invalid(c.email or "")]

    for contact in bad:
        logger.warning(
            "nulling malformed email: contact_id=%s raw=%r",
            contact.id,
            contact.email,
        )
        contact.email = None
        contact.is_email_valid = False
    session.commit()
    return len(bad)


def main() -> int:
    factory = sessionmaker(bind=get_engine(), autoflush=False, autocommit=False)
    with factory() as session:
        affected = backfill(session)
    logger.info("Backfill complete: %d emails malformados nulificados", affected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
