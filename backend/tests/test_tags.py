"""Tag CRUD, M:N assignment, bulk action and tag-filter coverage."""
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


def _create_contact(client: TestClient, email: str = "ana@example.com") -> dict:
    response = client.post(
        "/api/contacts",
        json={"first_name": "Ana", "email": email, "marketing_consent": "unknown"},
        headers=auth_headers(client, "manager"),
    )
    assert response.status_code == 201
    return response.json()


def _create_tag(client: TestClient, name: str = "VIP", color: str = "#FF5733") -> dict:
    response = client.post(
        "/api/tags",
        json={"name": name, "color": color},
        headers=auth_headers(client, "manager"),
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_tag_create_is_case_insensitive_unique(client: TestClient):
    """Two tags with the same normalized name (case folded + stripped)
    must collide. The 409 protects the UI from accidentally producing
    "VIP" + "vip" as separate rows."""
    _create_tag(client, name="VIP")
    duplicate = client.post(
        "/api/tags",
        json={"name": " vip "},
        headers=auth_headers(client, "manager"),
    )
    assert duplicate.status_code == 409


def test_tag_list_returns_contact_count(client: TestClient):
    """List endpoint exposes `contact_count` per tag so the admin UI
    can show "5 contactos" next to each row without a per-tag query."""
    tag = _create_tag(client, name="newsletter")
    contact = _create_contact(client, "boris@example.com")
    response = client.post(
        f"/api/contacts/{contact['id']}/tags",
        json={"tag_id": tag["id"]},
        headers=auth_headers(client, "manager"),
    )
    assert response.status_code == 201
    listed = client.get("/api/tags", headers=auth_headers(client, "viewer")).json()
    assert listed["total"] == 1
    assert listed["items"][0]["contact_count"] == 1


def test_assign_tag_by_name_upserts(client: TestClient):
    """Sending `tag_name` for a non-existing tag both creates the tag
    AND assigns it — saves the UI a round-trip when the operator
    types a new tag in the autocomplete and presses Enter."""
    contact = _create_contact(client)
    response = client.post(
        f"/api/contacts/{contact['id']}/tags",
        json={"tag_name": "Lead caliente", "color": "#FF0000"},
        headers=auth_headers(client, "manager"),
    )
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Lead caliente"
    detail = client.get(
        f"/api/contacts/{contact['id']}", headers=auth_headers(client, "viewer")
    ).json()
    assert any(t["name"] == "Lead caliente" for t in detail["tag_objects"])


def test_assign_tag_is_idempotent(client: TestClient):
    """A double-click on "Add tag" must not produce duplicate rows or
    a 409. The endpoint returns the tag both times."""
    tag = _create_tag(client, name="vip")
    contact = _create_contact(client)
    first = client.post(
        f"/api/contacts/{contact['id']}/tags",
        json={"tag_id": tag["id"]},
        headers=auth_headers(client, "manager"),
    )
    second = client.post(
        f"/api/contacts/{contact['id']}/tags",
        json={"tag_id": tag["id"]},
        headers=auth_headers(client, "manager"),
    )
    assert first.status_code == 201
    assert second.status_code == 201
    detail = client.get(
        f"/api/contacts/{contact['id']}", headers=auth_headers(client, "viewer")
    ).json()
    assert sum(1 for t in detail["tag_objects"] if t["id"] == tag["id"]) == 1


def test_remove_tag_returns_message_when_not_attached(client: TestClient):
    tag = _create_tag(client, name="vip")
    contact = _create_contact(client)
    response = client.delete(
        f"/api/contacts/{contact['id']}/tags/{tag['id']}",
        headers=auth_headers(client, "manager"),
    )
    assert response.status_code == 200
    assert "not attached" in response.json()["message"].lower()


def test_bulk_tag_add_and_skip_missing(client: TestClient):
    tag = _create_tag(client, name="bulk")
    contact_ids = [_create_contact(client, f"u{i}@example.com")["id"] for i in range(3)]
    contact_ids.append("does-not-exist")
    response = client.post(
        "/api/contacts/bulk-tag",
        json={"action": "add", "tag_id": tag["id"], "contact_ids": contact_ids},
        headers=auth_headers(client, "manager"),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["affected"] == 3
    assert body["skipped"] == 1


def test_bulk_tag_requires_manager_role(client: TestClient):
    tag = _create_tag(client, name="bulk")
    contact = _create_contact(client)
    forbidden = client.post(
        "/api/contacts/bulk-tag",
        json={"action": "add", "tag_id": tag["id"], "contact_ids": [contact["id"]]},
        headers=auth_headers(client, "user"),
    )
    assert forbidden.status_code == 403


def test_filter_contacts_by_tag_ids_any(client: TestClient):
    tag_a = _create_tag(client, name="alpha")
    tag_b = _create_tag(client, name="beta")
    ana = _create_contact(client, "ana@example.com")
    boris = _create_contact(client, "boris@example.com")
    carla = _create_contact(client, "carla@example.com")
    for tag, contact in (
        (tag_a, ana),
        (tag_b, boris),
        (tag_a, carla),
        (tag_b, carla),
    ):
        client.post(
            f"/api/contacts/{contact['id']}/tags",
            json={"tag_id": tag["id"]},
            headers=auth_headers(client, "manager"),
        )
    response = client.get(
        f"/api/contacts?tag_ids={tag_a['id']}&tag_ids={tag_b['id']}&tag_match_mode=any",
        headers=auth_headers(client, "viewer"),
    )
    emails = sorted(item["email"] for item in response.json()["items"])
    assert emails == ["ana@example.com", "boris@example.com", "carla@example.com"]


def test_filter_contacts_by_tag_ids_all(client: TestClient):
    """`tag_match_mode=all` requires every tag to be present, so a
    contact with one of two tags is filtered out. Cardinal regression:
    a previous implementation used IN which silently behaved as `any`
    in `all` mode."""
    tag_a = _create_tag(client, name="alpha")
    tag_b = _create_tag(client, name="beta")
    ana = _create_contact(client, "ana@example.com")
    boris = _create_contact(client, "boris@example.com")
    carla = _create_contact(client, "carla@example.com")
    for tag, contact in (
        (tag_a, ana),
        (tag_a, carla),
        (tag_b, carla),
        (tag_b, boris),
    ):
        client.post(
            f"/api/contacts/{contact['id']}/tags",
            json={"tag_id": tag["id"]},
            headers=auth_headers(client, "manager"),
        )
    response = client.get(
        f"/api/contacts?tag_ids={tag_a['id']}&tag_ids={tag_b['id']}&tag_match_mode=all",
        headers=auth_headers(client, "viewer"),
    )
    emails = [item["email"] for item in response.json()["items"]]
    assert emails == ["carla@example.com"]


def test_filter_contacts_by_lead_score_range(client: TestClient):
    from app.models.crm import Contact

    for email in ("low@example.com", "mid@example.com", "high@example.com"):
        _create_contact(client, email)
    session_factory = app.dependency_overrides[get_session]
    gen = session_factory()
    session = next(gen)
    try:
        for contact in session.query(Contact).all():
            if contact.email == "low@example.com":
                contact.lead_score = 1
            elif contact.email == "mid@example.com":
                contact.lead_score = 50
            else:
                contact.lead_score = 90
        session.commit()
    finally:
        gen.close()

    response = client.get(
        "/api/contacts?lead_score_min=40&lead_score_max=80",
        headers=auth_headers(client, "viewer"),
    )
    emails = sorted(item["email"] for item in response.json()["items"])
    assert emails == ["mid@example.com"]


def test_tag_update_blocks_normalized_collision(client: TestClient):
    _create_tag(client, name="vip")
    other = _create_tag(client, name="newsletter")
    response = client.patch(
        f"/api/tags/{other['id']}",
        json={"name": "VIP"},
        headers=auth_headers(client, "manager"),
    )
    assert response.status_code == 409


def test_delete_tag_cascades_assignments(client: TestClient):
    from app.models.crm import ContactTag

    tag = _create_tag(client, name="ephemeral")
    contact = _create_contact(client)
    client.post(
        f"/api/contacts/{contact['id']}/tags",
        json={"tag_id": tag["id"]},
        headers=auth_headers(client, "manager"),
    )

    response = client.delete(
        f"/api/tags/{tag['id']}", headers=auth_headers(client, "manager")
    )
    assert response.status_code == 200

    session_factory = app.dependency_overrides[get_session]
    gen = session_factory()
    session = next(gen)
    try:
        assert session.query(ContactTag).count() == 0
    finally:
        gen.close()
