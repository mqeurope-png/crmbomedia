"""Backfill `contacts.company_id` from Brevo custom-fields + email
domains.

Sprint Empresas — sub-PR 1/4. One-shot script after the migration
lands. The flow:

1. For every contact missing a `company_id`, look at the Brevo
   `EMPRESA` / `CIF` / `WEB` custom-fields first. If any of them
   point at an existing Company by `tax_id` or `domain`, link.
   Otherwise create a new Company with `source='brevo'`.
2. Fall back to deriving the company from the email domain. Free-
   mail addresses (gmail.com etc.) are skipped — those contacts
   stay company-less until an admin assigns one by hand.
3. Anything left unresolved logs a counter and moves on.

Usage:
    INTEGRATION_SECRETS_KEY=…  python -m scripts.backfill_companies_from_contacts
    INTEGRATION_SECRETS_KEY=…  python -m scripts.backfill_companies_from_contacts --dry-run
    INTEGRATION_SECRETS_KEY=…  python -m scripts.backfill_companies_from_contacts --limit 100

Commits in batches so a partial run still makes progress.
"""
from __future__ import annotations

import argparse
import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_engine
from app.models.crm import Company, Contact
from app.services.company_extraction import (
    derive_company_name_from_domain,
    extract_company_domain,
    normalise_domain,
)

log = logging.getLogger("backfill_companies")
logging.basicConfig(level=logging.INFO, format="%(message)s")


def _contact_custom_fields(contact: Contact) -> dict[str, Any]:
    raw = contact.custom_fields
    if not raw:
        return {}
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _brevo_value(custom_fields: dict[str, Any], key: str) -> str | None:
    """Read a Brevo-prefixed custom-field. Tolerates both the
    legacy `brevo:KEY` shape and the bare key the Brevo mapper
    actually writes."""
    raw = custom_fields.get(f"brevo:{key}") or custom_fields.get(key)
    if raw is None:
        return None
    raw_str = str(raw).strip()
    return raw_str or None


def _resolve_from_brevo(
    session: Session,
    *,
    name: str,
    cif: str | None,
    web: str | None,
    address_line: str | None,
    city: str | None,
    state: str | None,
    postal_code: str | None,
    country: str | None,
    region: str | None,
) -> Company:
    """Find an existing company by CIF or domain, else create one
    with the Brevo data flagged `source='brevo'`."""
    company: Company | None = None
    if cif:
        company = session.scalar(
            select(Company).where(Company.tax_id == cif)
        )
    domain = normalise_domain(web)
    if company is None and domain:
        company = session.scalar(
            select(Company).where(Company.domain == domain)
        )
    if company is not None:
        # Don't overwrite the canonical name when an existing row
        # already carries a (possibly hand-edited) value — just
        # extend the external_references trail.
        refs = _decode_refs(company.external_references_json)
        refs.setdefault("brevo", {})["empresa"] = name
        company.external_references_json = json.dumps(refs)
        return company

    refs_payload = {"brevo": {"empresa": name}}
    company = Company(
        name=name,
        website=web,
        domain=domain,
        tax_id=cif,
        address_line=address_line,
        city=city,
        state=state,
        postal_code=postal_code,
        country=country,
        region=region,
        source="brevo",
        external_references_json=json.dumps(refs_payload),
    )
    session.add(company)
    session.flush()
    return company


def _decode_refs(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _resolve_from_email_domain(
    session: Session, *, email: str
) -> Company | None:
    """Auto-link by email domain. Returns None when the address is
    free-mail (gmail.com etc.) so the caller can record the skip."""
    domain = extract_company_domain(email)
    if not domain:
        return None
    company = session.scalar(
        select(Company).where(Company.domain == domain)
    )
    if company is not None:
        return company
    company = Company(
        name=derive_company_name_from_domain(domain),
        domain=domain,
        source="auto-domain",
    )
    session.add(company)
    session.flush()
    return company


def backfill(
    *, dry_run: bool, batch: int = 200, limit: int | None = None
) -> dict[str, int]:
    counts = {
        "scanned": 0,
        "linked_brevo": 0,
        "linked_domain": 0,
        "skipped_personal_domain": 0,
        "skipped_no_data": 0,
        "companies_created": 0,
    }
    engine = get_engine()
    with Session(engine) as session:
        stmt = select(Contact).where(Contact.company_id.is_(None))
        if limit is not None:
            stmt = stmt.limit(limit)
        contacts = list(session.scalars(stmt))
        counts["scanned"] = len(contacts)
        pending = 0
        for contact in contacts:
            cf = _contact_custom_fields(contact)
            brevo_name = _brevo_value(cf, "EMPRESA")
            brevo_cif = _brevo_value(cf, "CIF")
            brevo_web = _brevo_value(cf, "WEB")
            company: Company | None = None
            if brevo_name:
                before_id = session.scalar(
                    select(Company.id).where(
                        Company.tax_id == brevo_cif if brevo_cif else False
                    )
                )
                company = _resolve_from_brevo(
                    session,
                    name=brevo_name,
                    cif=brevo_cif,
                    web=brevo_web,
                    address_line=_brevo_value(cf, "ADDRESS"),
                    city=_brevo_value(cf, "CIUDAD"),
                    state=_brevo_value(cf, "PROVINCIA"),
                    postal_code=_brevo_value(cf, "CODIGO_POSTAL"),
                    country=contact.address_country or contact.country,
                    region=_brevo_value(cf, "PAIS_REGION"),
                )
                counts["linked_brevo"] += 1
                if before_id is None and company.id is not None:
                    counts["companies_created"] += 1
            else:
                pre_domain = (
                    session.scalar(
                        select(Company.id).where(
                            Company.domain
                            == extract_company_domain(contact.email)
                        )
                    )
                    if contact.email
                    else None
                )
                company = _resolve_from_email_domain(
                    session, email=contact.email or ""
                )
                if company is None:
                    if contact.email and "@" in contact.email:
                        counts["skipped_personal_domain"] += 1
                    else:
                        counts["skipped_no_data"] += 1
                else:
                    counts["linked_domain"] += 1
                    if pre_domain is None:
                        counts["companies_created"] += 1
            if company is not None and not dry_run:
                contact.company_id = company.id
                pending += 1
                if pending >= batch:
                    session.commit()
                    pending = 0
        if not dry_run and pending:
            session.commit()
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    counts = backfill(dry_run=args.dry_run, limit=args.limit)
    log.info(
        "backfill summary scanned=%d linked_brevo=%d linked_domain=%d "
        "personal_skip=%d no_data_skip=%d companies_created=%d dry_run=%s",
        counts["scanned"],
        counts["linked_brevo"],
        counts["linked_domain"],
        counts["skipped_personal_domain"],
        counts["skipped_no_data"],
        counts["companies_created"],
        args.dry_run,
    )
    print(json.dumps(counts))


if __name__ == "__main__":
    main()
