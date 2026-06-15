"""Sprint Filtros & Listas — PR-B tests.

Covers the multi-entity views table + the generic search endpoints:

- `contact_views.entity_type` discriminator: legacy rows stay on
  `'contact'`, new endpoint writes per-entity values.
- Default uniqueness scoped per `(owner, entity_type)`: a user can have
  one default contact view AND one default company view simultaneously.
- `POST /api/entities/{entity}/search` (paginated, sorted, filtered) +
  `/search/ids` (truncation cap).
- Anti-injection for sort keys (unknown / non-sortable → 400).
- Legacy `/api/contact-views` keeps working and only sees contact-typed
  views.
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
from app.main import app
from app.models.crm import Base, Company, Contact, ContactView
from app.services.entities import get_entity
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
        test_client._factory = db.factory  # type: ignore[attr-defined]
        yield test_client
    app.dependency_overrides.clear()


def _seed_companies(factory: sessionmaker, names: list[tuple[str, str, str]]) -> None:
    with factory() as session:
        for name, country, source in names:
            session.add(Company(name=name, country=country, source=source))
        session.commit()


# -- views: schema + back-compat ------------------------------------


def test_entity_type_column_defaults_to_contact(db: _Fixture) -> None:
    """A bare insert (legacy path) lands `entity_type='contact'`."""
    with db.factory() as session:
        seed_user_id = session.scalar(select(Base.metadata.tables["users"].c.id))
        view = ContactView(
            name="Legacy view",
            owner_user_id=seed_user_id,
        )
        session.add(view)
        session.commit()
        loaded = session.get(ContactView, view.id)
        assert loaded is not None and loaded.entity_type == "contact"


# -- views CRUD per entity ------------------------------------------


def test_create_company_view(client: TestClient) -> None:
    headers = auth_headers(client, "admin")
    res = client.post(
        "/api/entity-views/company",
        json={
            "name": "España activas",
            "is_shared": True,
            "filters": {
                "rules_json": {
                    "type": "rule",
                    "field": "country",
                    "comparator": "eq",
                    "value": "ES",
                }
            },
            "columns": {"visible": ["name", "domain", "country"]},
            "sort": {"sort_by": "name", "sort_dir": "asc"},
        },
        headers=headers,
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["entity_type"] == "company"
    assert body["is_owner"] is True
    assert body["filters"]["rules_json"]["field"] == "country"

    # The view shows up in the list-by-entity.
    listed = client.get("/api/entity-views/company", headers=headers).json()
    assert any(row["id"] == body["id"] for row in listed)


def test_view_isolation_between_entities(client: TestClient) -> None:
    """A company view doesn't leak into the contact list, and vice versa."""
    headers = auth_headers(client, "admin")
    company_id = client.post(
        "/api/entity-views/company",
        json={"name": "C view"},
        headers=headers,
    ).json()["id"]
    contact_id = client.post(
        "/api/entity-views/contact",
        json={"name": "K view"},
        headers=headers,
    ).json()["id"]

    company_listed = client.get(
        "/api/entity-views/company", headers=headers
    ).json()
    contact_listed = client.get(
        "/api/entity-views/contact", headers=headers
    ).json()
    assert {row["id"] for row in company_listed} == {company_id}
    assert {row["id"] for row in contact_listed} == {contact_id}

    # Cross-entity GET on someone else's-entity view 404s rather than leaking.
    res = client.get(f"/api/entity-views/company/{contact_id}", headers=headers)
    assert res.status_code == 404


def test_default_uniqueness_scoped_per_entity(client: TestClient) -> None:
    """A user can have one default per entity simultaneously."""
    headers = auth_headers(client, "admin")
    contact_v = client.post(
        "/api/entity-views/contact",
        json={"name": "K1", "is_default": True},
        headers=headers,
    ).json()
    company_v = client.post(
        "/api/entity-views/company",
        json={"name": "C1", "is_default": True},
        headers=headers,
    ).json()
    # Setting a NEW contact view as default demotes the first contact one
    # but does NOT touch the company default.
    contact_v2 = client.post(
        "/api/entity-views/contact",
        json={"name": "K2", "is_default": True},
        headers=headers,
    ).json()

    fetched_contact_v = client.get(
        f"/api/entity-views/contact/{contact_v['id']}", headers=headers
    ).json()
    fetched_company_v = client.get(
        f"/api/entity-views/company/{company_v['id']}", headers=headers
    ).json()
    fetched_contact_v2 = client.get(
        f"/api/entity-views/contact/{contact_v2['id']}", headers=headers
    ).json()
    assert fetched_contact_v["is_default"] is False
    assert fetched_contact_v2["is_default"] is True
    assert fetched_company_v["is_default"] is True  # untouched


def test_duplicate_inherits_entity_type(client: TestClient) -> None:
    headers = auth_headers(client, "admin")
    source = client.post(
        "/api/entity-views/company", json={"name": "Origen"}, headers=headers
    ).json()
    res = client.post(
        f"/api/entity-views/company/{source['id']}/duplicate",
        json={"name": "Copia"},
        headers=headers,
    )
    assert res.status_code == 201
    dup = res.json()
    assert dup["entity_type"] == "company"
    assert dup["name"] == "Copia"
    assert dup["is_default"] is False and dup["is_shared"] is False


def test_unknown_entity_404(client: TestClient) -> None:
    headers = auth_headers(client, "admin")
    res = client.get("/api/entity-views/dragons", headers=headers)
    assert res.status_code == 404


def test_update_requires_owner(client: TestClient) -> None:
    admin_headers = auth_headers(client, "admin")
    user_headers = auth_headers(client, "user")
    created = client.post(
        "/api/entity-views/company",
        json={"name": "Owned by admin", "is_shared": True},
        headers=admin_headers,
    ).json()
    # 'user' can read (shared) but not edit.
    res = client.patch(
        f"/api/entity-views/company/{created['id']}",
        json={"name": "Hacked"},
        headers=user_headers,
    )
    assert res.status_code == 403


# -- legacy /api/contact-views still works -------------------------


def test_legacy_contact_views_only_sees_contact_typed(client: TestClient) -> None:
    headers = auth_headers(client, "admin")
    client.post(
        "/api/entity-views/contact",
        json={"name": "Solo contactos"},
        headers=headers,
    )
    client.post(
        "/api/entity-views/company",
        json={"name": "Solo empresas"},
        headers=headers,
    )
    res = client.get("/api/contact-views", headers=headers)
    assert res.status_code == 200
    names = {row["name"] for row in res.json()}
    assert "Solo contactos" in names
    assert "Solo empresas" not in names


# -- search --------------------------------------------------------


def test_search_company_paginates_and_sorts(client: TestClient) -> None:
    factory = client._factory  # type: ignore[attr-defined]
    _seed_companies(
        factory,
        [
            ("Alpha", "ES", "manual"),
            ("Beta", "ES", "brevo"),
            ("Gamma", "FR", "manual"),
            ("Delta", "ES", "manual"),
        ],
    )
    headers = auth_headers(client, "viewer")
    res = client.post(
        "/api/entities/company/search",
        json={
            "rules_json": {
                "type": "rule",
                "field": "country",
                "comparator": "eq",
                "value": "ES",
            },
            "sort_by": "name",
            "sort_dir": "asc",
            "limit": 2,
            "offset": 0,
        },
        headers=headers,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["total"] == 3
    assert [row["name"] for row in body["items"]] == ["Alpha", "Beta"]
    # `id` always present + every column-source spec serialised.
    assert "id" in body["items"][0]
    assert body["items"][0]["country"] == "ES"

    # next page
    page2 = client.post(
        "/api/entities/company/search",
        json={
            "rules_json": {
                "type": "rule",
                "field": "country",
                "comparator": "eq",
                "value": "ES",
            },
            "sort_by": "name",
            "sort_dir": "asc",
            "limit": 2,
            "offset": 2,
        },
        headers=headers,
    ).json()
    assert [row["name"] for row in page2["items"]] == ["Delta"]


def test_search_empty_body_returns_universe(client: TestClient) -> None:
    factory = client._factory  # type: ignore[attr-defined]
    _seed_companies(factory, [("Alpha", "ES", "manual"), ("Beta", "FR", "manual")])
    headers = auth_headers(client, "viewer")
    res = client.post(
        "/api/entities/company/search", json={}, headers=headers
    )
    assert res.status_code == 200
    assert res.json()["total"] == 2


def test_search_rejects_non_sortable_field(client: TestClient) -> None:
    """Sort whitelist is the anti-injection boundary for ordering."""
    headers = auth_headers(client, "viewer")
    res = client.post(
        "/api/entities/company/search",
        json={"sort_by": "address_line"},  # registered, but sortable=False
        headers=headers,
    )
    assert res.status_code == 400


def test_search_rejects_unknown_sort_field(client: TestClient) -> None:
    headers = auth_headers(client, "viewer")
    res = client.post(
        "/api/entities/company/search",
        json={"sort_by": "lead_score"},  # Contact-only, not on Company
        headers=headers,
    )
    assert res.status_code == 400


def test_search_invalid_rule_returns_400(client: TestClient) -> None:
    headers = auth_headers(client, "viewer")
    res = client.post(
        "/api/entities/company/search",
        json={
            "rules_json": {
                "type": "rule",
                "field": "lead_score",  # Contact field, not Company
                "comparator": "eq",
                "value": 5,
            }
        },
        headers=headers,
    )
    assert res.status_code == 400


def test_search_ids_returns_matching_ids(client: TestClient) -> None:
    factory = client._factory  # type: ignore[attr-defined]
    _seed_companies(
        factory,
        [("Alpha", "ES", "manual"), ("Beta", "FR", "manual"), ("Delta", "ES", "manual")],
    )
    headers = auth_headers(client, "viewer")
    res = client.post(
        "/api/entities/company/search/ids",
        json={
            "rules_json": {
                "type": "rule",
                "field": "country",
                "comparator": "eq",
                "value": "ES",
            }
        },
        headers=headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["count"] == 2
    assert body["truncated"] is False
    assert body["max_ids"] == 10_000
    assert len(body["ids"]) == 2


def test_search_ids_reports_truncation(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cap is enforced and surfaced to the caller."""
    factory = client._factory  # type: ignore[attr-defined]
    _seed_companies(
        factory, [(f"Co {i}", "ES", "manual") for i in range(6)]
    )
    # Shrink the cap so the test doesn't need 10k rows.
    from app.api import entities as entities_module

    monkeypatch.setattr(entities_module, "MAX_IDS", 3)
    headers = auth_headers(client, "viewer")
    body = client.post(
        "/api/entities/company/search/ids",
        json={},
        headers=headers,
    ).json()
    assert body["truncated"] is True
    assert body["count"] == 3
    assert body["max_ids"] == 3


def test_search_unknown_entity_404(client: TestClient) -> None:
    headers = auth_headers(client, "viewer")
    res = client.post("/api/entities/dragons/search", json={}, headers=headers)
    assert res.status_code == 404


# -- PR-Cb hotfix: serialize_row handles computed/concat fields ----


def test_contact_serialize_row_computes_full_name() -> None:
    """Regression guard for the empty `name` column in the entity table:
    PR-B's `serialize_row` skipped `computed` specs and `Contact.name`
    is declared computed with `extras={'concat': (first, last)}`. PR-Cb
    handles that case so the unified table shows the full name."""
    descriptor = get_entity("contact")
    assert descriptor is not None
    bart = Contact(first_name="Bart", last_name="Simpson", email="b@bomedia.net")
    row = descriptor.serialize_row(bart)
    assert row["name"] == "Bart Simpson"
    # Only first → still renders without the trailing space.
    homer = Contact(first_name="Homer", last_name=None, email="h@bomedia.net")
    assert descriptor.serialize_row(homer)["name"] == "Homer"
    # No name at all → None (column shows "—" upstream).
    nobody = Contact(first_name=None, last_name=None, email="x@bomedia.net")
    assert descriptor.serialize_row(nobody)["name"] is None
