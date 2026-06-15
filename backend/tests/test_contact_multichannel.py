"""Sprint Empresas — sub-PR 3/4 backend tests.

Covers the new contact_phones / contact_emails CRUD endpoints,
the Brevo + Agile mapper extraction helpers, the reconcilers'
idempotency, and the backfill that mirrors the canonical
phone/email into the new collections.
"""
from __future__ import annotations

import json
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
    extract_agilecrm_secondary_channels,
    map_agilecrm_contact_to_internal,
)
from app.integrations.brevo.mapper import extract_brevo_secondary_channels
from app.main import app
from app.models.crm import (
    Base,
    Contact,
    ContactEmail,
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


def test_brevo_extracts_secondary_phones_and_emails() -> None:
    phones, emails = extract_brevo_secondary_channels(
        {
            "id": 1,
            "attributes": {
                "TELEFONO_1": "+34600111222",
                "TELEFONO_2": "+34 600 111 222",  # dup of 1 after normalise
                "TELEFONO_3": "+34987654321",
                "LANDLINE_NUMBER": "934567890",
                "TEL": "",
                "EMAIL_SECUNDARIO": "Bart@OTHER.com",
                "EMAIL2": "bart@other.com",  # dup after lowercase
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
    assert [e["email"] for e in emails] == ["bart@other.com"]
    assert emails[0]["label"] == "EMAIL_SECUNDARIO"


def test_agilecrm_extracts_phone_email_subtypes_and_socials() -> None:
    payload = {
        "id": 1,
        "properties": [
            {"name": "phone", "subtype": "", "value": "+34600000000"},
            {"name": "phone", "subtype": "mobile", "value": "+34600111111"},
            {"name": "phone", "subtype": "work", "value": "+34932222222"},
            {"name": "phone", "subtype": "home-fax", "value": "+34933333333"},
            {"name": "email", "subtype": "", "value": "default@example.com"},
            {
                "name": "email",
                "subtype": "personal",
                "value": "Personal@Example.com",
            },
            {"name": "email", "subtype": "work", "value": "work@example.com"},
            {"name": "twitter", "value": "https://twitter.com/bart"},
            {"name": "facebook", "value": "fb.com/bart"},
            {"name": "github", "value": "https://github.com/bart"},
            {"name": "skype", "value": "bart.skype"},
        ],
    }
    phones, emails, socials = extract_agilecrm_secondary_channels(payload)
    labels = [(p["label"], p["number"]) for p in phones]
    assert ("mobile", "+34600111111") in labels
    assert ("work", "+34932222222") in labels
    assert ("home-fax", "+34933333333") in labels
    # default phone went to Contact.phone, NOT into the secondary list.
    assert all(p["number"] != "+34600000000" for p in phones)
    addresses = [(e["label"], e["email"]) for e in emails]
    assert ("personal", "personal@example.com") in addresses
    assert ("work", "work@example.com") in addresses
    assert all("default" not in e["email"] for e in emails)
    assert socials == {
        "twitter": "https://twitter.com/bart",
        "facebook": "fb.com/bart",
        "github": "https://github.com/bart",
        "skype": "bart.skype",
    }


def test_agilecrm_mapper_pins_twitter_facebook_and_jsons_the_rest() -> None:
    payload = {
        "id": 7,
        "properties": [
            {"name": "first_name", "value": "Bart"},
            {"name": "email", "value": "bart@bomedia.net"},
            {"name": "twitter", "value": "https://twitter.com/bart"},
            {"name": "facebook", "value": "https://facebook.com/bart"},
            {"name": "github", "value": "https://github.com/bart"},
            {"name": "skype", "value": "bart.skype"},
        ],
    }
    record, _ = map_agilecrm_contact_to_internal(payload)
    assert record["twitter_url"] == "https://twitter.com/bart"
    assert record["facebook_url"] == "https://facebook.com/bart"
    others = json.loads(record["social_profiles_json"])
    assert others == {
        "github": "https://github.com/bart",
        "skype": "bart.skype",
    }


# -- Brevo + Agile reconcilers --------------------------------------


def test_brevo_reconcile_channels_inserts_then_idempotent(
    db: _Fixture,
) -> None:
    from app.integrations.brevo.jobs import reconcile_brevo_channels  # noqa: PLC0415

    contact_id = _seed_contact(db.factory)
    payload = {
        "id": 1,
        "attributes": {
            "TELEFONO_2": "+34600111222",
            "EMAIL_SECUNDARIO": "alt@bomedia.net",
        },
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
    assert first == (1, 1)
    assert second == (0, 0)
    with db.factory() as session:
        phones = list(
            session.scalars(
                select(ContactPhone).where(ContactPhone.contact_id == contact_id)
            )
        )
        emails = list(
            session.scalars(
                select(ContactEmail).where(ContactEmail.contact_id == contact_id)
            )
        )
    assert len(phones) == 1
    assert phones[0].source == "brevo"
    assert phones[0].label == "TELEFONO_2"
    assert len(emails) == 1
    assert emails[0].label == "EMAIL_SECUNDARIO"
    assert emails[0].source == "brevo"


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


def test_emails_dedupe_and_primary_flip(
    client: TestClient, db: _Fixture
) -> None:
    contact_id = _seed_contact(db.factory)
    headers = auth_headers(client, "user")

    res = client.post(
        f"/api/contacts/{contact_id}/emails",
        json={"email": "Personal@Example.com", "label": "personal"},
        headers=headers,
    )
    assert res.status_code == 201
    assert res.json()["email"] == "personal@example.com"

    res = client.post(
        f"/api/contacts/{contact_id}/emails",
        json={"email": "PERSONAL@example.COM"},
        headers=headers,
    )
    assert res.status_code == 409

    res = client.post(
        f"/api/contacts/{contact_id}/emails",
        json={"email": "work@example.com", "is_primary": True},
        headers=headers,
    )
    assert res.status_code == 201
    work_id = res.json()["id"]

    res = client.get(f"/api/contacts/{contact_id}/emails", headers=headers)
    by_email = {r["email"]: r for r in res.json()}
    assert by_email["work@example.com"]["is_primary"] is True
    assert by_email["personal@example.com"]["is_primary"] is False

    _ = work_id  # only used by the assertion shape above


# -- backfill -------------------------------------------------------


def test_backfill_mirrors_primary_phone_and_email(db: _Fixture) -> None:
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
    assert first["primary_emails_added"] == 1
    assert second == {
        "scanned": 1,
        "primary_phones_added": 0,
        "primary_emails_added": 0,
    }

    with db.factory() as session:
        phones = list(
            session.scalars(
                select(ContactPhone).where(ContactPhone.contact_id == contact_id)
            )
        )
        emails = list(
            session.scalars(
                select(ContactEmail).where(ContactEmail.contact_id == contact_id)
            )
        )
    assert len(phones) == 1 and phones[0].is_primary is True
    assert phones[0].source == "backfill"
    assert len(emails) == 1 and emails[0].is_primary is True
    _ = _user_id  # silence pyflakes when the helper isn't called directly
