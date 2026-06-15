"""Sprint Filtros & Listas — PR-A tests.

Covers the multi-entity foundation: the registry exposes all five
entities; each entity's filter-schema is well-formed; the generalised
engine compiles trees for non-Contact entities AND keeps the Contact
anti-injection boundary intact; the `/api/entities` surface returns the
declarative schema.
"""
from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import get_session
from app.main import app
from app.models.brevo import BrevoCampaignCache, BrevoTemplateCache
from app.models.crm import Base, Company, EmailThread
from app.services.entities import (
    get_entity,
    list_entities,
    list_fields_for_entity,
)
from app.services.segments.engine import (
    SegmentRuleError,
    build_entity_filter,
    build_filter,
)
from tests._test_helpers import auth_headers, seed_test_users

_ALL_ENTITIES = {
    "contact",
    "company",
    "email_thread",
    "brevo_template",
    "brevo_campaign",
}


@pytest.fixture()
def client() -> Generator[TestClient, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as seed:
        seed_test_users(seed)

    def override_session() -> Generator[Session, None, None]:
        with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as test_client:
        test_client._factory = factory  # type: ignore[attr-defined]
        test_client._engine = engine  # type: ignore[attr-defined]
        yield test_client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)


# -- registry ------------------------------------------------------


def test_registry_exposes_all_five_entities() -> None:
    assert set(list_entities()) == _ALL_ENTITIES


def test_unknown_entity_returns_none() -> None:
    assert get_entity("nope") is None
    assert list_fields_for_entity("nope") is None


@pytest.mark.parametrize("entity", sorted(_ALL_ENTITIES))
def test_every_field_schema_is_well_formed(entity: str) -> None:
    fields = list_fields_for_entity(entity)
    assert fields, f"{entity} has no fields"
    keys = [f["key"] for f in fields]
    assert len(keys) == len(set(keys)), f"{entity} has duplicate field keys"
    for f in fields:
        # Required UI/column metadata present on every descriptor.
        for attr in (
            "key",
            "label",
            "type",
            "comparators",
            "sortable",
            "displayable",
            "filterable",
            "default_visible",
            "grouped_under",
            "source",
        ):
            assert attr in f, f"{entity}.{f.get('key')} missing {attr}"
        # A field with comparators is filterable; without, it isn't.
        assert f["filterable"] == bool(f["comparators"])
        # At least one default-visible column so the list isn't empty.
    assert any(f["default_visible"] for f in fields), entity


# -- generalised engine --------------------------------------------


def test_engine_compiles_company_tree() -> None:
    company = get_entity("company")
    tree = {
        "operator": "AND",
        "children": [
            {"type": "rule", "field": "country", "comparator": "eq", "value": "ES"},
            {
                "operator": "NOT",
                "children": [
                    {"type": "rule", "field": "is_active", "comparator": "eq", "value": False}
                ],
            },
        ],
    }
    clause = build_entity_filter(company, tree)
    # Compiles to a real SQLAlchemy clause usable in a select().
    stmt = select(Company).where(clause)
    assert stmt is not None


def test_engine_compiles_email_and_brevo_trees() -> None:
    thread = get_entity("email_thread")
    clause = build_entity_filter(
        thread,
        {"type": "rule", "field": "state", "comparator": "eq", "value": "inbox"},
    )
    assert select(EmailThread).where(clause) is not None

    tpl = get_entity("brevo_template")
    clause = build_entity_filter(
        tpl,
        {"type": "rule", "field": "is_active", "comparator": "eq", "value": True},
    )
    assert select(BrevoTemplateCache).where(clause) is not None

    camp = get_entity("brevo_campaign")
    clause = build_entity_filter(
        camp,
        {"type": "rule", "field": "status", "comparator": "in", "value": ["sent", "queued"]},
    )
    assert select(BrevoCampaignCache).where(clause) is not None


def test_engine_rejects_unknown_field_per_entity() -> None:
    company = get_entity("company")
    # `lead_score` is a Contact field; it must NOT leak into Company.
    with pytest.raises(SegmentRuleError):
        build_entity_filter(
            company,
            {"type": "rule", "field": "lead_score", "comparator": "eq", "value": 5},
        )


def test_engine_rejects_disallowed_comparator_per_entity() -> None:
    company = get_entity("company")
    # is_active is bool → only `eq`. `contains` must be rejected.
    with pytest.raises(SegmentRuleError):
        build_entity_filter(
            company,
            {"type": "rule", "field": "is_active", "comparator": "contains", "value": "x"},
        )


def test_company_filter_executes_against_db(client: TestClient) -> None:
    """End-to-end: compile a Company tree and actually run it."""
    factory = client._factory  # type: ignore[attr-defined]
    with factory() as session:
        session.add(Company(name="BO Media", country="ES", source="manual"))
        session.add(Company(name="Foreign Inc", country="US", source="manual"))
        session.commit()

    company = get_entity("company")
    clause = build_entity_filter(
        company,
        {"type": "rule", "field": "country", "comparator": "eq", "value": "ES"},
    )
    with factory() as session:
        rows = list(session.scalars(select(Company).where(clause)))
    assert [r.name for r in rows] == ["BO Media"]


def test_owner_filter_supports_is_null_for_unassigned() -> None:
    """Sprint decision: owner exposed as filter; is_null == 'Sin asignar'."""
    contact_fields = {f["key"]: f for f in list_fields_for_entity("contact")}
    assert "owner_user_id" in contact_fields
    owner = contact_fields["owner_user_id"]
    assert owner["type"] == "reference"
    assert owner["reference_table"] == "users"
    assert "is_null" in owner["comparators"]
    # And it actually compiles on the Contact path.
    clause = build_filter(
        {"type": "rule", "field": "owner_user_id", "comparator": "is_null", "value": None}
    )
    assert clause is not None


# -- API surface ---------------------------------------------------


def test_list_entities_endpoint(client: TestClient) -> None:
    headers = auth_headers(client, "viewer")
    res = client.get("/api/entities", headers=headers)
    assert res.status_code == 200
    keys = {row["key"] for row in res.json()}
    assert keys == _ALL_ENTITIES


def test_filter_schema_endpoint(client: TestClient) -> None:
    headers = auth_headers(client, "viewer")
    res = client.get("/api/entities/company/filter-schema", headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert body["entity"] == "company"
    assert body["default_sort"] == "name"
    labels = {f["key"]: f["label"] for f in body["fields"]}
    # §2.7 confirmed labels.
    assert labels["tax_id"] == "CIF/NIF"
    assert labels["vat"] == "VAT intracomunitario"


def test_filter_schema_unknown_entity_404(client: TestClient) -> None:
    headers = auth_headers(client, "viewer")
    res = client.get("/api/entities/dragons/filter-schema", headers=headers)
    assert res.status_code == 404


def test_filter_schema_requires_auth(client: TestClient) -> None:
    res = client.get("/api/entities/contact/filter-schema")
    assert res.status_code in (401, 403)


def test_segments_available_fields_still_works(client: TestClient) -> None:
    """Back-compat: the legacy Contact endpoint keeps its shape (plus the
    additive PR-A keys)."""
    headers = auth_headers(client, "viewer")
    res = client.get("/api/segments/available-fields", headers=headers)
    assert res.status_code == 200
    fields = res.json()
    keys = {f["key"] for f in fields}
    assert "commercial_status" in keys
    assert "owner_user_id" in keys  # newly added
    # Additive metadata present without breaking the original keys.
    sample = fields[0]
    assert {"key", "label", "type", "comparators", "enum_values"} <= set(sample)
