"""Sprint Empresas — backend tests for the new Companies feature.

Covers the CRUD endpoints, the extraction helpers, the Brevo
resolver hook, and the end-to-end backfill happy path against an
in-memory SQLite session.
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
from app.integrations.brevo.jobs import resolve_brevo_company
from app.main import app
from app.models.crm import Base, Company, Contact, User, UserRole
from app.services.company_extraction import (
    derive_company_name_from_domain,
    extract_company_domain,
    normalise_domain,
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


# -- extraction helpers ---------------------------------------------


def test_extract_company_domain_skips_personal_addresses() -> None:
    assert extract_company_domain("bart@bomedia.net") == "bomedia.net"
    assert extract_company_domain("bart@GMAIL.com") is None
    assert extract_company_domain("bart@yahoo.es") is None
    assert extract_company_domain("") is None
    assert extract_company_domain("no-at-sign") is None


def test_derive_company_name_titlecases_short_tokens_as_acronyms() -> None:
    assert derive_company_name_from_domain("bomedia.net") == "Bomedia"
    assert derive_company_name_from_domain("th-containers.es") == "TH Containers"
    assert derive_company_name_from_domain("openai.com") == "Openai"


def test_normalise_domain_strips_scheme_and_www() -> None:
    assert normalise_domain("https://www.Bomedia.net/about") == "bomedia.net"
    assert normalise_domain("Bomedia.net") == "bomedia.net"
    assert normalise_domain("  ") is None
    assert normalise_domain(None) is None


# -- /api/companies CRUD --------------------------------------------


def test_create_get_update_delete_company(client: TestClient) -> None:
    headers = auth_headers(client, "admin")

    res = client.post(
        "/api/companies",
        json={"name": "Bomedia", "domain": "bomedia.net"},
        headers=headers,
    )
    assert res.status_code == 201, res.text
    company_id = res.json()["id"]
    assert res.json()["source"] == "manual"
    assert res.json()["contacts_count"] == 0

    res = client.get(f"/api/companies/{company_id}", headers=headers)
    assert res.status_code == 200

    res = client.put(
        f"/api/companies/{company_id}",
        json={
            "name": "Bomedia Studios",
            "domain": "bomedia.net",
            "tax_id": "B12345678",
            "country": "España",
        },
        headers=headers,
    )
    assert res.status_code == 200
    assert res.json()["name"] == "Bomedia Studios"

    # delete is admin-only — the seeded admin works.
    res = client.delete(f"/api/companies/{company_id}", headers=headers)
    assert res.status_code == 204


def test_create_company_with_taken_domain_is_409(client: TestClient) -> None:
    headers = auth_headers(client, "admin")
    client.post(
        "/api/companies",
        json={"name": "Bomedia", "domain": "bomedia.net"},
        headers=headers,
    )
    res = client.post(
        "/api/companies",
        json={"name": "Otra", "domain": "bomedia.net"},
        headers=headers,
    )
    assert res.status_code == 409


def test_list_companies_q_country_source_filters(
    client: TestClient, db: _Fixture
) -> None:
    headers = auth_headers(client, "admin")
    with db.factory() as session:
        session.add_all(
            [
                Company(name="Bomedia", domain="bomedia.net", source="manual"),
                Company(
                    name="TH Containers",
                    domain="th-containers.es",
                    country="España",
                    source="brevo",
                ),
                Company(
                    name="Acme",
                    domain="acme.com",
                    country="Reino Unido",
                    source="auto-domain",
                ),
            ]
        )
        session.commit()

    res = client.get("/api/companies?q=bom", headers=headers)
    assert {c["name"] for c in res.json()["items"]} == {"Bomedia"}

    res = client.get("/api/companies?country=España", headers=headers)
    assert {c["name"] for c in res.json()["items"]} == {"TH Containers"}

    res = client.get("/api/companies?source=auto-domain", headers=headers)
    assert {c["name"] for c in res.json()["items"]} == {"Acme"}


def test_list_companies_has_contacts_filter(
    client: TestClient, db: _Fixture
) -> None:
    headers = auth_headers(client, "admin")
    with db.factory() as session:
        with_contacts = Company(name="With", domain="with.com")
        without = Company(name="Without", domain="without.com")
        session.add_all([with_contacts, without])
        session.flush()
        session.add(
            Contact(
                first_name="Lead",
                email="lead@with.com",
                tags="",
                commercial_status="new",
                company_id=with_contacts.id,
            )
        )
        session.commit()

    res = client.get("/api/companies?has_contacts=true", headers=headers)
    assert {c["name"] for c in res.json()["items"]} == {"With"}

    res = client.get("/api/companies?has_contacts=false", headers=headers)
    assert {c["name"] for c in res.json()["items"]} == {"Without"}


def test_assign_company_round_trip(
    client: TestClient, db: _Fixture
) -> None:
    headers = auth_headers(client, "admin")
    with db.factory() as session:
        contact = Contact(
            first_name="Lead",
            email="lead@bomedia.net",
            tags="",
            commercial_status="new",
        )
        company = Company(name="Bomedia", domain="bomedia.net")
        session.add_all([contact, company])
        session.commit()
        contact_id, company_id = contact.id, company.id

    res = client.post(
        f"/api/contacts/{contact_id}/assign-company",
        json={"company_id": company_id},
        headers=headers,
    )
    assert res.status_code == 200
    assert res.json()["company_id"] == company_id

    res = client.post(
        f"/api/contacts/{contact_id}/assign-company",
        json={"company_id": None},
        headers=headers,
    )
    assert res.json()["company_id"] is None


def test_merge_companies_repoints_contacts_and_deletes_source(
    client: TestClient, db: _Fixture
) -> None:
    headers = auth_headers(client, "admin")
    with db.factory() as session:
        source = Company(name="Old", domain="old.com")
        target = Company(name="New", domain="new.com")
        session.add_all([source, target])
        session.flush()
        session.add_all(
            [
                Contact(
                    first_name="L1",
                    email="l1@old.com",
                    tags="",
                    commercial_status="new",
                    company_id=source.id,
                ),
                Contact(
                    first_name="L2",
                    email="l2@old.com",
                    tags="",
                    commercial_status="new",
                    company_id=source.id,
                ),
            ]
        )
        session.commit()
        source_id, target_id = source.id, target.id

    res = client.post(
        f"/api/companies/{source_id}/merge/{target_id}", headers=headers
    )
    assert res.status_code == 200, res.text
    assert res.json()["id"] == target_id

    with db.factory() as session:
        assert session.get(Company, source_id) is None
        for c in session.scalars(select(Contact)):
            assert c.company_id == target_id


# -- Brevo resolver --------------------------------------------------


def test_resolve_brevo_company_creates_from_empresa_attrs(
    db: _Fixture,
) -> None:
    with db.factory() as session:
        company_id = resolve_brevo_company(
            session,
            {
                "EMPRESA": "Bomedia",
                "CIF": "B12345678",
                "WEB": "https://bomedia.net",
                "CIUDAD": "Barcelona",
            },
            fallback_email="bart@bomedia.net",
        )
        session.commit()
    with db.factory() as session:
        company = session.get(Company, company_id)
        assert company is not None
        assert company.name == "Bomedia"
        assert company.tax_id == "B12345678"
        assert company.domain == "bomedia.net"
        assert company.source == "brevo"


def test_resolve_brevo_company_falls_back_to_email_domain(
    db: _Fixture,
) -> None:
    with db.factory() as session:
        company_id = resolve_brevo_company(
            session,
            {},
            fallback_email="bart@th-containers.es",
        )
        session.commit()
    with db.factory() as session:
        company = session.get(Company, company_id)
        assert company is not None
        assert company.name == "TH Containers"
        assert company.domain == "th-containers.es"
        assert company.source == "auto-domain"


def test_resolve_brevo_company_returns_none_for_personal_address(
    db: _Fixture,
) -> None:
    with db.factory() as session:
        company_id = resolve_brevo_company(
            session, {}, fallback_email="anyone@gmail.com"
        )
    assert company_id is None


# -- backfill -------------------------------------------------------


def test_backfill_links_brevo_and_domain_paths(db: _Fixture) -> None:
    from scripts.backfill_companies_from_contacts import backfill  # noqa: PLC0415

    with db.factory() as session:
        session.add_all(
            [
                Contact(
                    first_name="Brevo",
                    email="b@th-containers.es",
                    tags="",
                    commercial_status="new",
                    custom_fields=json.dumps(
                        {
                            "EMPRESA": "TH Containers",
                            "CIF": "B98765432",
                            "WEB": "th-containers.es",
                        }
                    ),
                ),
                Contact(
                    first_name="Domain",
                    email="d@bomedia.net",
                    tags="",
                    commercial_status="new",
                ),
                Contact(
                    first_name="Personal",
                    email="p@gmail.com",
                    tags="",
                    commercial_status="new",
                ),
            ]
        )
        session.commit()

    from unittest.mock import patch

    with patch(
        "scripts.backfill_companies_from_contacts.get_engine",
        return_value=db.engine,
    ):
        summary = backfill(dry_run=False)

    assert summary["scanned"] == 3
    assert summary["linked_brevo"] == 1
    assert summary["linked_domain"] == 1
    assert summary["skipped_personal_domain"] == 1
    assert summary["companies_created"] == 2

    with db.factory() as session:
        contacts = list(session.scalars(select(Contact)))
        by_email = {c.email: c for c in contacts}
        assert by_email["b@th-containers.es"].company_id is not None
        assert by_email["d@bomedia.net"].company_id is not None
        assert by_email["p@gmail.com"].company_id is None
    _ = _user_id  # silence pyflakes when the fixture isn't used directly
