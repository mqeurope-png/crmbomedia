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


def test_preview_rejects_tag_string_value_with_400_not_500(client: TestClient):
    """Production bug: an operator typing free-text into the value editor
    for `tags` sent a plain string where the engine expects a list of
    UUIDs. The route used to return 500 because `validate_value` raised
    a plain `ValueError` that didn't match the `SegmentRuleError`
    handler. With the engine wrapping the validator, the same payload
    now surfaces as a 400 with a clear field-aware message the UI can
    show next to the offending row."""
    response = client.post(
        "/api/segments/preview",
        json={
            "rules": {
                "type": "rule",
                "field": "tags",
                "comparator": "contains_any",
                "value": "formmbo",  # string, not a list of UUIDs
            }
        },
        headers=auth_headers(client, "viewer"),
    )
    assert response.status_code == 400, response.text
    body = response.json()
    detail = body.get("detail", "")
    assert "tags" in detail
    assert "contains_any" in detail or "list" in detail


def test_create_segment_with_bad_tags_value_returns_400(client: TestClient):
    """Same fix applies at POST: invalid value types must reach the UI
    as a clean 400 instead of crashing the create flow."""
    response = client.post(
        "/api/segments",
        json={
            "name": "Bad",
            "rules": {
                "type": "rule",
                "field": "tags",
                "comparator": "contains_any",
                "value": "not-a-uuid-list",
            },
        },
        headers=auth_headers(client, "manager"),
    )
    assert response.status_code == 400


def test_update_segment_with_bad_lead_score_returns_400(client: TestClient):
    segment = client.post(
        "/api/segments",
        json={"name": "S", "rules": _basic_rules()},
        headers=auth_headers(client, "manager"),
    ).json()
    response = client.patch(
        f"/api/segments/{segment['id']}",
        json={
            "rules": {
                "type": "rule",
                "field": "lead_score",
                "comparator": "gte",
                "value": "not-a-number",
            }
        },
        headers=auth_headers(client, "manager"),
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
