"""CRUD + preview + permissions for `/api/segments`."""
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import get_session
from app.main import app
from app.models.crm import Base
from tests._test_helpers import auth_headers, seed_test_users


@pytest.fixture()
def client() -> Generator[TestClient, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with testing_session() as seed_session:
        seed_test_users(seed_session)

    def override_session() -> Generator[Session, None, None]:
        with testing_session() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)


def _create_contact(client: TestClient, email: str = "ana@example.com", **overrides) -> dict:
    payload = {
        "first_name": "Ana",
        "email": email,
        "marketing_consent": "unknown",
    }
    payload.update(overrides)
    response = client.post(
        "/api/contacts",
        json=payload,
        headers=auth_headers(client, "manager"),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _basic_rules() -> dict:
    return {
        "type": "rule",
        "field": "marketing_consent",
        "comparator": "eq",
        "value": "granted",
    }


def test_available_fields_endpoint_lists_whitelist(client: TestClient):
    response = client.get(
        "/api/segments/available-fields",
        headers=auth_headers(client, "viewer"),
    )
    assert response.status_code == 200
    keys = {row["key"] for row in response.json()}
    assert {"name", "email", "tags", "lead_score", "pipeline_id"} <= keys


def test_create_segment_evaluates_and_caches_count(client: TestClient):
    _create_contact(client, "ana@example.com", marketing_consent="granted")
    _create_contact(client, "boris@example.com", marketing_consent="denied")
    response = client.post(
        "/api/segments",
        json={"name": "Marketing OK", "rules": _basic_rules()},
        headers=auth_headers(client, "manager"),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["cached_count"] == 1
    assert body["last_evaluated_at"] is not None


def test_invalid_rules_at_create_return_400(client: TestClient):
    response = client.post(
        "/api/segments",
        json={
            "name": "Bad",
            "rules": {
                "type": "rule",
                "field": "secret",  # not in whitelist
                "comparator": "contains",
                "value": "x",
            },
        },
        headers=auth_headers(client, "manager"),
    )
    assert response.status_code == 400


def test_list_includes_own_and_shared(client: TestClient):
    """A manager sees their own segments + every shared row from
    other users. Private rows of others stay hidden."""
    own = client.post(
        "/api/segments",
        json={"name": "Mío", "rules": _basic_rules()},
        headers=auth_headers(client, "manager"),
    ).json()
    shared = client.post(
        "/api/segments",
        json={"name": "Compartido", "is_shared": True, "rules": _basic_rules()},
        headers=auth_headers(client, "admin"),
    ).json()
    private_of_admin = client.post(
        "/api/segments",
        json={"name": "Solo admin", "rules": _basic_rules()},
        headers=auth_headers(client, "admin"),
    ).json()

    listed = client.get(
        "/api/segments", headers=auth_headers(client, "manager")
    ).json()
    ids = {row["id"] for row in listed}
    assert own["id"] in ids
    assert shared["id"] in ids
    assert private_of_admin["id"] not in ids


def test_patch_blocked_for_non_owner(client: TestClient):
    shared = client.post(
        "/api/segments",
        json={"name": "Compartido", "is_shared": True, "rules": _basic_rules()},
        headers=auth_headers(client, "admin"),
    ).json()
    response = client.patch(
        f"/api/segments/{shared['id']}",
        json={"name": "Hackeado"},
        headers=auth_headers(client, "manager"),
    )
    assert response.status_code == 403


def test_segment_contacts_returns_matching_rows(client: TestClient):
    _create_contact(client, "ana@example.com", marketing_consent="granted")
    _create_contact(client, "boris@example.com", marketing_consent="denied")
    segment = client.post(
        "/api/segments",
        json={"name": "OK", "is_shared": True, "rules": _basic_rules()},
        headers=auth_headers(client, "manager"),
    ).json()
    response = client.get(
        f"/api/segments/{segment['id']}/contacts",
        headers=auth_headers(client, "viewer"),
    )
    assert response.status_code == 200
    body = response.json()
    emails = sorted(item["email"] for item in body["items"])
    assert emails == ["ana@example.com"]


def test_preview_returns_count_and_sample(client: TestClient):
    _create_contact(client, "ana@example.com", marketing_consent="granted")
    _create_contact(client, "boris@example.com", marketing_consent="granted")
    response = client.post(
        "/api/segments/preview",
        json={"rules": _basic_rules()},
        headers=auth_headers(client, "viewer"),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 2
    assert len(body["sample"]) == 2


def test_preview_rejects_invalid_rules(client: TestClient):
    response = client.post(
        "/api/segments/preview",
        json={
            "rules": {
                "type": "rule",
                "field": "password",
                "comparator": "eq",
                "value": "x",
            }
        },
        headers=auth_headers(client, "viewer"),
    )
    assert response.status_code == 400


def test_force_refresh_count_re_evaluates(client: TestClient):
    """`?force_refresh=true` re-runs the SQL even when a cached value
    exists. Used by the "Refrescar count" button on the detail page."""
    _create_contact(client, "ana@example.com", marketing_consent="granted")
    segment = client.post(
        "/api/segments",
        json={"name": "X", "is_shared": True, "rules": _basic_rules()},
        headers=auth_headers(client, "manager"),
    ).json()
    assert segment["cached_count"] == 1

    _create_contact(client, "boris@example.com", marketing_consent="granted")
    response = client.get(
        f"/api/segments/{segment['id']}/count?force_refresh=true",
        headers=auth_headers(client, "viewer"),
    )
    assert response.json() == {"total": 2}


def test_segment_templates_endpoint_lists_starter_set(client: TestClient):
    response = client.get(
        "/api/segments/templates", headers=auth_headers(client, "viewer")
    )
    assert response.status_code == 200
    ids = {row["id"] for row in response.json()}
    assert {"hot_leads", "inactive_90_days", "new_this_week"} <= ids


def test_duplicate_segment_creates_owned_copy(client: TestClient):
    shared = client.post(
        "/api/segments",
        json={"name": "Compartido", "is_shared": True, "rules": _basic_rules()},
        headers=auth_headers(client, "admin"),
    ).json()
    response = client.post(
        f"/api/segments/{shared['id']}/duplicate",
        json={"name": "Mi copia"},
        headers=auth_headers(client, "manager"),
    )
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Mi copia"
    assert body["is_owner"] is True
    assert body["is_shared"] is False
