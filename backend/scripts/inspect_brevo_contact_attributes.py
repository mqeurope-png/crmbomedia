"""Inspect one Brevo contact's raw attributes + secondary-phone extraction.

Sprint Empresas — sub-PR 3 debugging. When a full sync produced
zero `contact_phones` secondary rows, the open question was whether
`extract_brevo_secondary_phones` returns anything for real
contacts, or whether the account names its phone attributes
differently than the `SECONDARY_PHONE_ATTRS` whitelist expects.

This script answers it against live data: it fetches the contact
straight from Brevo, dumps every attribute key + value, then runs
the extractor and prints what it would persist.

Usage:
    INTEGRATION_SECRETS_KEY=…  python -m scripts.inspect_brevo_contact_attributes \
        --account-id default --external-id 18518

`--external-id` accepts either the Brevo numeric id or the email
(Brevo's GET /contacts/{identifier} takes both).
"""
from __future__ import annotations

import argparse
import asyncio
import json

from sqlalchemy.orm import Session

from app.db.session import get_engine
from app.integrations.brevo.client import BrevoClient
from app.integrations.brevo.mapper import (
    _SECONDARY_PHONE_KEYS,
    _normalise_attr_key,
    extract_brevo_secondary_phones,
)


async def _run(account_id: str, external_id: str) -> None:
    with Session(get_engine()) as session:
        async with BrevoClient(session, account_id) as client:
            payload = await client.get_contact(external_id)

    attributes = payload.get("attributes") or {}
    print(f"contact id={payload.get('id')} email={payload.get('email')!r}")
    print(f"emailBlacklisted={payload.get('emailBlacklisted')}")
    print("\n--- raw attribute keys ---")
    for key, value in sorted(attributes.items()):
        normalised = _normalise_attr_key(key)
        is_phone = normalised in _SECONDARY_PHONE_KEYS
        marker = " <-- secondary phone" if is_phone else ""
        print(f"  {key!r} (norm={normalised}) = {value!r}{marker}")

    print("\n--- extract_brevo_secondary_phones output ---")
    phones = extract_brevo_secondary_phones(payload)
    print(json.dumps(phones, indent=2, ensure_ascii=False))
    print(f"\n{len(phones)} secondary phone(s) would be persisted.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account-id", default="default")
    parser.add_argument("--external-id", required=True)
    args = parser.parse_args()
    asyncio.run(_run(args.account_id, args.external_id))


if __name__ == "__main__":
    main()
