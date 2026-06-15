"""Lift custom-field values into the new first-class contact columns.

Sprint Empresas — sub-PR 2/4. The mappers shipped in the same
sprint write the new columns from now on, but historic rows still
carry the values inside `contacts.custom_fields` JSON. This script
walks every contact, looks at the existing JSON for known keys and
copies them into the matching column when the destination is NULL.

Idempotent: a contact whose new columns are already populated is
skipped, so re-running is cheap. The script never overwrites a
non-NULL column — operator edits win over the upstream snapshot.

A second pass materialises `email_unsubscribes` rows for any
contact whose JSON carries `EMAILABLE_UNSUBSCRIBED` (true/yes/1)
but who doesn't have a Brevo-sourced unsubscribe yet — same
fall-back logic as `reconcile_brevo_unsubscribe` in the live
sync.

Usage:
    INTEGRATION_SECRETS_KEY=…  python -m scripts.backfill_contact_professional_fields
    INTEGRATION_SECRETS_KEY=…  python -m scripts.backfill_contact_professional_fields --dry-run
    INTEGRATION_SECRETS_KEY=…  python -m scripts.backfill_contact_professional_fields --limit 100

Commits in batches of 200 so a partial run still makes progress.
"""
from __future__ import annotations

import argparse
import json
import logging
import secrets
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_engine
from app.models.crm import (
    Contact,
    EmailUnsubscribe,
    EmailUnsubscribeScope,
)

log = logging.getLogger("backfill_contact_fields")
logging.basicConfig(level=logging.INFO, format="%(message)s")

# Source-attribute name (any case) → contact column. The lookup
# uppercases the key when matching, so callers can store the
# attribute under whichever case the upstream CRM used.
LIFT_MAP: dict[str, str] = {
    "JOB_TITLE": "job_title",
    "JOBTITLE": "job_title",
    "TITLE": "job_title",
    "PUESTO": "job_title",
    "CARGO": "job_title",
    "LINKEDIN": "linkedin_url",
    "LINKEDIN_URL": "linkedin_url",
    "WEB": "personal_website",
    "WEBSITE": "personal_website",
    "ADDRESS": "address_line",
    "DIRECCION": "address_line",
    "DIRECCIO": "address_line",
    "PROVINCIA": "address_state",
    "STATE": "address_state",
    "CODIGO_POSTAL": "address_postal_code",
    "CODIGOPOSTAL": "address_postal_code",
    "POSTAL_CODE": "address_postal_code",
    "POSTCODE": "address_postal_code",
    "ZIP": "address_postal_code",
    "PAIS_REGION": "address_region",
    "REGION": "address_region",
}


def _decode_custom_fields(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _lift(contact: Contact, custom: dict[str, Any]) -> int:
    """Copy any LIFT_MAP key whose destination column is NULL.
    Returns the number of columns updated."""
    upcased = {str(k).upper(): v for k, v in custom.items()}
    changes = 0
    for raw_key, column in LIFT_MAP.items():
        if getattr(contact, column, None) is not None:
            continue
        value = _clean(upcased.get(raw_key))
        if value is None:
            continue
        setattr(contact, column, value)
        changes += 1
    return changes


def _materialise_unsubscribe(
    session: Session, contact: Contact, custom: dict[str, Any]
) -> bool:
    """Add a Brevo-sourced unsubscribe row when the JSON flag is on
    and no row already exists for the contact. Returns True when a
    fresh row was inserted."""
    upcased = {str(k).upper(): v for k, v in custom.items()}
    raw = upcased.get("EMAILABLE_UNSUBSCRIBED")
    if raw is None:
        return False
    flag = str(raw).strip().lower()
    if flag not in {"1", "true", "yes", "si", "sí"}:
        return False
    existing = session.scalar(
        select(EmailUnsubscribe).where(
            EmailUnsubscribe.contact_id == contact.id,
            EmailUnsubscribe.source == "brevo",
            EmailUnsubscribe.scope == EmailUnsubscribeScope.MARKETING,
        )
    )
    if existing is not None:
        return False
    session.add(
        EmailUnsubscribe(
            contact_id=contact.id,
            scope=EmailUnsubscribeScope.MARKETING,
            source="brevo",
            token=secrets.token_urlsafe(32),
            unsubscribed_at=datetime.now(UTC),
            metadata_json=json.dumps(
                {"backfill": True, "custom_unsubscribed": True}
            ),
        )
    )
    return True


def backfill(
    *, dry_run: bool, batch: int = 200, limit: int | None = None
) -> dict[str, int]:
    counts = {
        "scanned": 0,
        "fields_filled": 0,
        "unsubscribes_inserted": 0,
        "contacts_touched": 0,
    }
    engine = get_engine()
    with Session(engine) as session:
        stmt = select(Contact)
        if limit is not None:
            stmt = stmt.limit(limit)
        contacts = list(session.scalars(stmt))
        counts["scanned"] = len(contacts)
        pending = 0
        for contact in contacts:
            custom = _decode_custom_fields(contact.custom_fields)
            if not custom:
                continue
            filled = _lift(contact, custom)
            unsub = _materialise_unsubscribe(session, contact, custom)
            if filled or unsub:
                counts["fields_filled"] += filled
                counts["unsubscribes_inserted"] += int(unsub)
                counts["contacts_touched"] += 1
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
    counts = backfill(dry_run=args.dry_run, limit=args.limit)
    log.info(
        "backfill summary scanned=%d touched=%d fields_filled=%d "
        "unsubscribes=%d dry_run=%s",
        counts["scanned"],
        counts["contacts_touched"],
        counts["fields_filled"],
        counts["unsubscribes_inserted"],
        args.dry_run,
    )
    print(json.dumps(counts))


if __name__ == "__main__":
    main()
