"""Sprint Empresas — sub-PR 3 (post-revert) backend tests.

Covers the surviving multichannel surface: `contact_phones` CRUD,
Brevo + Agile secondary-phone extractors, the reconcilers'
idempotency, and the backfill that mirrors the canonical phone
into the new collection.

The email + socials counterparts that the original sub-PR 3
shipped were reverted in this PR — contacts only have one email
in practice and the CRM has never used social links.
"""
from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import get_session
from app.integrations.agilecrm.mapper import (
    extract_agilecrm_secondary_phones,
)
from app.integrations.brevo.mapper import extract_brevo_secondary_phones
from app.main import app
from app.models.crm import (
    Base,
    Contact,
    ContactPhone,
    User,
    UserRole,
)
from tests._test_helpers import auth_headers, seed_test_users


@dataclass
class _Fixture:
    engine: Engine
    factory: sessionmaker


@pytest.fixture()
def db() -> Generator[_Fixture, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as seed:
        seed_test_users(seed)
    yield _Fixture(engine=engine, factory=factory)
    Base.metadata.drop_all(engine)


@pytest.fixture()
def client(db: _Fixture) -> Generator[TestClient, None, None]:
    def override_session() -> Generator[Session, None, None]:
        with db.factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _user_id(session: Session, role: UserRole) -> str:
    return session.scalar(select(User.id).where(User.role == role))


def _seed_contact(factory: sessionmaker) -> str:
    with factory() as session:
        contact = Contact(
            first_name="Bart",
            email="bart@bomedia.net",
            tags="",
            commercial_status="new",
        )
        session.add(contact)
        session.commit()
        return contact.id


# -- Brevo + Agile extractors ---------------------------------------


def test_brevo_extracts_secondary_phones() -> None:
    phones = extract_brevo_secondary_phones(
        {
            "id": 1,
            "attributes": {
                "TELEFONO_1": "+34600111222",
                "TELEFONO_2": "+34 600 111 222",  # dup after normalise
                "TELEFONO_3": "+34987654321",
                "LANDLINE_NUMBER": "934567890",
                "TEL": "",
            },
        }
    )
    assert [p["number"] for p in phones] == [
        "+34600111222",
        "+34987654321",
        "934567890",
    ]
    assert [p["label"] for p in phones] == [
        "TELEFONO_1",
        "TELEFONO_3",
        "LANDLINE_NUMBER",
    ]


def test_brevo_extracts_secondary_phones_case_and_accent_insensitive() -> None:
    """Regression for the prod "zero secondary phones" bug. ES
    Sendinblue-era accounts ship the phone attributes with accents
    (`TELÉFONO_2`), mixed case (`Telefono_3`), or stray separators
    (`TELEFONO 4`). The old exact-`dict.get("TELEFONO_2")` match
    silently skipped every one of them. The label preserves the
    real payload key so the operator sees what Brevo carried."""
    phones = extract_brevo_secondary_phones(
        {
            "id": 1,
            "attributes": {
                "TELÉFONO_2": "+34600111222",
                "Telefono_3": "+34987654321",
                "TELEFONO 4": "933334444",
                "landline_number": "934567890",
            },
        }
    )
    assert [p["number"] for p in phones] == [
        "+34600111222",
        "+34987654321",
        "933334444",
        "934567890",
    ]
    # Labels keep the exact key Brevo sent, not the normalised form.
    assert [p["label"] for p in phones] == [
        "TELÉFONO_2",
        "Telefono_3",
        "TELEFONO 4",
        "landline_number",
    ]


def test_agilecrm_extracts_phone_subtypes() -> None:
    payload = {
        "id": 1,
        "properties": [
            {"name": "phone", "subtype": "", "value": "+34600000000"},
            {"name": "phone", "subtype": "mobile", "value": "+34600111111"},
            {"name": "phone", "subtype": "work", "value": "+34932222222"},
            {"name": "phone", "subtype": "home-fax", "value": "+34933333333"},
            # Non-phone properties are ignored entirely.
            {"name": "email", "subtype": "personal", "value": "x@y.com"},
            {"name": "twitter", "value": "https://twitter.com/bart"},
        ],
    }
    phones = extract_agilecrm_secondary_phones(payload)
    labels = [(p["label"], p["number"]) for p in phones]
    assert ("mobile", "+34600111111") in labels
    assert ("work", "+34932222222") in labels
    assert ("home-fax", "+34933333333") in labels
    # default phone went to Contact.phone, NOT into the secondary list.
    assert all(p["number"] != "+34600000000" for p in phones)


# -- Brevo reconciler ----------------------------------------------


def test_brevo_reconcile_channels_inserts_then_idempotent(
    db: _Fixture,
) -> None:
    from app.integrations.brevo.jobs import reconcile_brevo_channels  # noqa: PLC0415

    contact_id = _seed_contact(db.factory)
    payload = {
        "id": 1,
        "attributes": {"TELEFONO_2": "+34600111222"},
    }
    with db.factory() as session:
        first = reconcile_brevo_channels(
            session, contact_id=contact_id, payload=payload
        )
        session.commit()
        second = reconcile_brevo_channels(
            session, contact_id=contact_id, payload=payload
        )
        session.commit()
    assert first == 1
    assert second == 0
    with db.factory() as session:
        phones = list(
            session.scalars(
                select(ContactPhone).where(ContactPhone.contact_id == contact_id)
            )
        )
    assert len(phones) == 1
    assert phones[0].source == "brevo"
    assert phones[0].label == "TELEFONO_2"


def test_upsert_brevo_contact_persists_secondary_phone_and_stats(
    db: _Fixture,
) -> None:
    """End-to-end regression: a brand-new Brevo contact with an
    accented secondary-phone attribute lands a `contact_phones`
    row via `upsert_brevo_contact`, and the `stats` counter the
    sync loop threads through reports it."""
    from app.integrations.brevo.jobs import upsert_brevo_contact  # noqa: PLC0415

    stats: dict[str, int] = {"secondary_phones_added": 0}
    with db.factory() as session:
        action, contact_id = upsert_brevo_contact(
            session,
            account_id="acc-1",
            payload={
                "id": 18518,
                "email": "lead@th-containers.es",
                "attributes": {
                    "NOMBRE": "Lead",
                    "SMS": "+34600000000",  # → Contact.phone (native)
                    "TELÉFONO_2": "+34911223344",
                    "LANDLINE_NUMBER": "934567890",
                },
            },
            stats=stats,
        )
        session.commit()
    assert action == "created"
    assert stats["secondary_phones_added"] == 2

    with db.factory() as session:
        phones = list(
            session.scalars(
                select(ContactPhone).where(
                    ContactPhone.contact_id == contact_id
                )
            )
        )
    labels = {p.label for p in phones}
    assert labels == {"TELÉFONO_2", "LANDLINE_NUMBER"}
    assert all(p.source == "brevo" for p in phones)
    # The native SMS phone is NOT duplicated as a secondary row.
    assert all(p.number != "+34600000000" for p in phones)


# -- /api/contacts/{id}/phones CRUD ---------------------------------


def test_phones_crud_round_trip(client: TestClient, db: _Fixture) -> None:
    contact_id = _seed_contact(db.factory)
    headers = auth_headers(client, "user")

    res = client.get(f"/api/contacts/{contact_id}/phones", headers=headers)
    assert res.status_code == 200 and res.json() == []

    res = client.post(
        f"/api/contacts/{contact_id}/phones",
        json={"label": "mobile", "number": "+34600111222", "is_primary": True},
        headers=headers,
    )
    assert res.status_code == 201, res.text
    first_id = res.json()["id"]
    assert res.json()["is_primary"] is True

    # Add a second one; primary stays on the first.
    res = client.post(
        f"/api/contacts/{contact_id}/phones",
        json={"label": "centralita", "number": "934567890"},
        headers=headers,
    )
    assert res.status_code == 201
    second_id = res.json()["id"]

    # Flip primary to the second via the dedicated route.
    res = client.post(
        f"/api/contacts/{contact_id}/phones/{second_id}/primary",
        headers=headers,
    )
    assert res.status_code == 200
    res = client.get(f"/api/contacts/{contact_id}/phones", headers=headers)
    primaries = {r["id"]: r["is_primary"] for r in res.json()}
    assert primaries == {first_id: False, second_id: True}

    # Dedupe on create: same number (different formatting) → 409.
    res = client.post(
        f"/api/contacts/{contact_id}/phones",
        json={"number": "+34 600 111 222"},
        headers=headers,
    )
    assert res.status_code == 409

    # Delete the second one — primary goes "nobody".
    res = client.delete(
        f"/api/contacts/{contact_id}/phones/{second_id}", headers=headers
    )
    assert res.status_code == 204


# -- Brevo whitelist -----------------------------------------------


def test_brevo_secondary_emails_land_in_custom_fields_whitelist() -> None:
    """EMAIL_SECUNDARIO / EMAIL2 used to materialise into
    `contact_emails`; the table was dropped so they must instead
    survive the whitelist and reach `custom_fields` JSON."""
    import json as _json  # noqa: PLC0415

    from app.integrations.brevo.mapper import (  # noqa: PLC0415
        map_brevo_contact_to_internal,
    )

    record, _ = map_brevo_contact_to_internal(
        {
            "id": 100,
            "email": "bart@bomedia.net",
            "attributes": {
                "EMAIL_SECUNDARIO": "bart.alt@bomedia.net",
                "EMAIL2": "bart2@bomedia.net",
            },
        },
        account_id="acc-1",
    )
    custom = _json.loads(record["custom_fields"])
    assert custom == {
        "EMAIL_SECUNDARIO": "bart.alt@bomedia.net",
        "EMAIL2": "bart2@bomedia.net",
    }


# -- backfill -------------------------------------------------------


def test_backfill_mirrors_primary_phone(db: _Fixture) -> None:
    from scripts.backfill_contact_channels import backfill  # noqa: PLC0415

    with db.factory() as session:
        contact = Contact(
            first_name="Bart",
            email="bart@bomedia.net",
            phone="+34600111222",
            tags="",
            commercial_status="new",
            is_email_valid=True,
        )
        session.add(contact)
        session.commit()
        contact_id = contact.id

    from unittest.mock import patch

    with patch(
        "scripts.backfill_contact_channels.get_engine",
        return_value=db.engine,
    ):
        first = backfill(dry_run=False)
        second = backfill(dry_run=False)

    assert first["primary_phones_added"] == 1
    assert second == {"scanned": 1, "primary_phones_added": 0}

    with db.factory() as session:
        phones = list(
            session.scalars(
                select(ContactPhone).where(ContactPhone.contact_id == contact_id)
            )
        )
    assert len(phones) == 1 and phones[0].is_primary is True
    assert phones[0].source == "backfill"
    _ = _user_id  # silence pyflakes when the helper isn't used directly
