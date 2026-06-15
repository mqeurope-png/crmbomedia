"""Sprint Empresas — sub-PR 2/4 backend tests.

Covers:
- Brevo mapper lifts JOB_TITLE / LINKEDIN / WEB / ADDRESS / CIUDAD /
  PROVINCIA / CODIGO_POSTAL / PAIS_REGION into first-class columns
  AND leaves the business custom fields (GRADO_DE_INTERES etc.)
  in `custom_fields` JSON.
- Brevo mapper materialises an `EmailUnsubscribe` row when
  emailBlacklisted OR `EMAILABLE_UNSUBSCRIBED` is truthy.
- AgileCRM mapper lifts Title / LinkedIn / Website / Address /
  Zip into the same columns.
- Backfill script lifts custom-field values into NULL columns
  and is idempotent on re-run.
"""
from __future__ import annotations

import json
from collections.abc import Generator
from dataclasses import dataclass
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.integrations.agilecrm.mapper import map_agilecrm_contact_to_internal
from app.integrations.brevo.mapper import map_brevo_contact_to_internal
from app.models.crm import (
    Base,
    Contact,
    EmailUnsubscribe,
    User,
    UserRole,
)
from tests._test_helpers import seed_test_users


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


def _user_id(session: Session, role: UserRole) -> str:
    return session.scalar(select(User.id).where(User.role == role))


# -- Brevo mapper ---------------------------------------------------


def test_brevo_mapper_lifts_professional_and_address_fields() -> None:
    payload = {
        "id": 100,
        "email": "bart@bomedia.net",
        "attributes": {
            "NOMBRE": "Bart",
            "JOB_TITLE": "Head of CRM",
            "LINKEDIN": "https://www.linkedin.com/in/bart",
            "WEB": "bomedia.net",
            "ADDRESS": "C/ Aragó 123",
            "CIUDAD": "Barcelona",
            "PROVINCIA": "Barcelona",
            "CODIGO_POSTAL": "08015",
            "PAIS_REGION": "EU",
            # Business custom fields should stay in JSON.
            "GRADO_DE_INTERES": "alto",
            "INTERESADO_EN_DEMO": True,
        },
    }
    record, _ = map_brevo_contact_to_internal(payload, account_id="acc-1")
    assert record["job_title"] == "Head of CRM"
    assert record["linkedin_url"] == "https://www.linkedin.com/in/bart"
    assert record["personal_website"] == "bomedia.net"
    assert record["address_line"] == "C/ Aragó 123"
    assert record["address_city"] == "Barcelona"
    assert record["address_state"] == "Barcelona"
    assert record["address_postal_code"] == "08015"
    assert record["address_region"] == "EU"
    # Business attrs stay in custom_fields JSON.
    custom = json.loads(record["custom_fields"])
    assert custom["GRADO_DE_INTERES"] == "alto"
    assert custom["INTERESADO_EN_DEMO"] is True
    # And the lifted ones are NOT duplicated there.
    assert "JOB_TITLE" not in custom
    assert "ADDRESS" not in custom


def test_brevo_mapper_accepts_lowercase_and_english_aliases() -> None:
    payload = {
        "id": 101,
        "email": "x@example.com",
        "attributes": {
            "PUESTO": "Engineer",
            "WEBSITE": "https://example.com",
            "STATE": "CA",
            "ZIP": "94105",
            "REGION": "NA",
        },
    }
    record, _ = map_brevo_contact_to_internal(payload, account_id="acc-1")
    assert record["job_title"] == "Engineer"
    assert record["personal_website"] == "https://example.com"
    assert record["address_state"] == "CA"
    assert record["address_postal_code"] == "94105"
    assert record["address_region"] == "NA"


# -- Brevo unsubscribe reconciliation ------------------------------


def test_brevo_blacklisted_creates_email_unsubscribe(db: _Fixture) -> None:
    from app.integrations.brevo.jobs import reconcile_brevo_unsubscribe  # noqa: PLC0415

    with db.factory() as session:
        contact = Contact(
            first_name="Bart",
            email="bart@bomedia.net",
            tags="",
            commercial_status="new",
        )
        session.add(contact)
        session.commit()
        contact_id = contact.id

        added = reconcile_brevo_unsubscribe(
            session,
            contact_id=contact_id,
            payload={"id": 1, "emailBlacklisted": True},
        )
        session.commit()
    assert added is True

    with db.factory() as session:
        rows = list(
            session.scalars(
                select(EmailUnsubscribe).where(
                    EmailUnsubscribe.contact_id == contact_id
                )
            )
        )
        assert len(rows) == 1
        assert rows[0].source == "brevo"
        assert rows[0].scope.value == "marketing"


def test_brevo_unsubscribe_is_idempotent_on_resync(db: _Fixture) -> None:
    from app.integrations.brevo.jobs import reconcile_brevo_unsubscribe  # noqa: PLC0415

    with db.factory() as session:
        contact = Contact(
            first_name="Bart",
            email="bart@bomedia.net",
            tags="",
            commercial_status="new",
        )
        session.add(contact)
        session.commit()
        contact_id = contact.id

        first = reconcile_brevo_unsubscribe(
            session,
            contact_id=contact_id,
            payload={"id": 1, "emailBlacklisted": True},
        )
        session.commit()
        second = reconcile_brevo_unsubscribe(
            session,
            contact_id=contact_id,
            payload={"id": 1, "emailBlacklisted": True},
        )
        session.commit()
    assert first is True
    assert second is False
    with db.factory() as session:
        rows = list(
            session.scalars(
                select(EmailUnsubscribe).where(
                    EmailUnsubscribe.contact_id == contact_id
                )
            )
        )
        assert len(rows) == 1


def test_brevo_emailable_unsubscribed_custom_attr_also_works(
    db: _Fixture,
) -> None:
    from app.integrations.brevo.jobs import reconcile_brevo_unsubscribe  # noqa: PLC0415

    with db.factory() as session:
        contact = Contact(
            first_name="A",
            email="a@example.com",
            tags="",
            commercial_status="new",
        )
        session.add(contact)
        session.commit()
        contact_id = contact.id

        added = reconcile_brevo_unsubscribe(
            session,
            contact_id=contact_id,
            payload={
                "id": 2,
                "emailBlacklisted": False,
                "attributes": {"EMAILABLE_UNSUBSCRIBED": "true"},
            },
        )
        session.commit()
    assert added is True


# -- Agile mapper ---------------------------------------------------


def test_agilecrm_mapper_lifts_title_linkedin_website_zip() -> None:
    payload = {
        "id": 1,
        "properties": [
            {"name": "first_name", "value": "Bart"},
            {"name": "email", "value": "bart@bomedia.net"},
            {"name": "title", "value": "CTO"},
            {"name": "linkedin", "value": "https://linkedin.com/in/bart"},
            {"name": "website", "value": "bomedia.net"},
            {
                "name": "address",
                "value": json.dumps(
                    {
                        "address": "C/ Aragó 123",
                        "city": "Barcelona",
                        "state": "Barcelona",
                        "zip": "08015",
                        "country": "ES",
                    }
                ),
            },
        ],
    }
    record, _ = map_agilecrm_contact_to_internal(payload)
    assert record["job_title"] == "CTO"
    assert record["linkedin_url"] == "https://linkedin.com/in/bart"
    assert record["personal_website"] == "bomedia.net"
    assert record["address_line"] == "C/ Aragó 123"
    assert record["address_city"] == "Barcelona"
    assert record["address_postal_code"] == "08015"


# -- backfill -------------------------------------------------------


def test_backfill_lifts_json_into_columns_and_is_idempotent(
    db: _Fixture,
) -> None:
    from scripts.backfill_contact_professional_fields import backfill  # noqa: PLC0415

    with db.factory() as session:
        uid = _user_id(session, UserRole.USER)
        contact = Contact(
            first_name="Bart",
            email="bart@bomedia.net",
            tags="",
            commercial_status="new",
            custom_fields=json.dumps(
                {
                    "JOB_TITLE": "Head of CRM",
                    "LINKEDIN": "https://linkedin.com/in/bart",
                    "WEB": "bomedia.net",
                    "ADDRESS": "C/ Aragó 123",
                    "EMAILABLE_UNSUBSCRIBED": "1",
                }
            ),
        )
        session.add(contact)
        session.commit()
        contact_id = contact.id
    _ = uid

    with patch(
        "scripts.backfill_contact_professional_fields.get_engine",
        return_value=db.engine,
    ):
        first = backfill(dry_run=False)
        second = backfill(dry_run=False)

    assert first["contacts_touched"] == 1
    assert first["fields_filled"] == 4
    assert first["unsubscribes_inserted"] == 1
    # Re-run: nothing new because all columns are already populated
    # AND the unsubscribe row already exists.
    assert second["contacts_touched"] == 0
    assert second["fields_filled"] == 0
    assert second["unsubscribes_inserted"] == 0

    with db.factory() as session:
        c = session.get(Contact, contact_id)
        assert c.job_title == "Head of CRM"
        assert c.linkedin_url == "https://linkedin.com/in/bart"
        assert c.personal_website == "bomedia.net"
        assert c.address_line == "C/ Aragó 123"
        rows = list(
            session.scalars(
                select(EmailUnsubscribe).where(
                    EmailUnsubscribe.contact_id == contact_id
                )
            )
        )
        assert len(rows) == 1


def test_backfill_does_not_overwrite_existing_column(
    db: _Fixture,
) -> None:
    from scripts.backfill_contact_professional_fields import backfill  # noqa: PLC0415

    with db.factory() as session:
        contact = Contact(
            first_name="Bart",
            email="bart@bomedia.net",
            tags="",
            commercial_status="new",
            job_title="OPERATOR EDIT",
            custom_fields=json.dumps({"JOB_TITLE": "Stale"}),
        )
        session.add(contact)
        session.commit()
        contact_id = contact.id

    with patch(
        "scripts.backfill_contact_professional_fields.get_engine",
        return_value=db.engine,
    ):
        result = backfill(dry_run=False)
    assert result["contacts_touched"] == 0
    with db.factory() as session:
        c = session.get(Contact, contact_id)
        assert c.job_title == "OPERATOR EDIT"
