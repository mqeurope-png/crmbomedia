"""CRUD + permissions + duplicate / default + view_id merging on the
saved contact-views endpoints."""
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


def _create_view(client: TestClient, role: str = "manager", **overrides) -> dict:
    payload = {
        "name": "Vista test",
        "description": None,
        "is_shared": False,
        "is_default": False,
        "filters": {"q": "demo"},
        "columns": {"visible": ["name", "email"], "order": ["name", "email"], "widths": {}},
        "sort": {"sort_by": "updated_at", "sort_dir": "desc"},
    }
    payload.update(overrides)
    response = client.post(
        "/api/contact-views",
        json=payload,
        headers=auth_headers(client, role),
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_create_view_returns_owner_and_decoded_filters(client: TestClient):
    view = _create_view(client, role="manager")
    assert view["is_owner"] is True
    assert view["filters"]["q"] == "demo"
    assert view["columns"]["visible"] == ["name", "email"]
    assert view["sort"]["sort_by"] == "updated_at"


def test_list_includes_own_and_shared_views(client: TestClient):
    """Owner sees every own view + any other user's shared view; the
    private view of another user must NOT leak into the list."""
    own = _create_view(client, role="manager", name="Mía")
    shared = _create_view(
        client, role="admin", name="Compartida", is_shared=True
    )
    _create_view(client, role="admin", name="Solo admin")

    response = client.get(
        "/api/contact-views", headers=auth_headers(client, "manager")
    )
    body = response.json()
    names = {v["name"]: v for v in body}
    assert "Mía" in names
    assert "Compartida" in names
    assert "Solo admin" not in names
    assert names["Mía"]["is_owner"] is True
    assert names["Compartida"]["is_owner"] is False
    # Sanity: keep referencing for debugging
    assert own["id"] in {v["id"] for v in body}
    assert shared["id"] in {v["id"] for v in body}


def test_patch_view_blocked_for_non_owner(client: TestClient):
    """Even shared views can only be edited by the owner — operators
    duplicate to mutate."""
    shared = _create_view(client, role="admin", is_shared=True)
    response = client.patch(
        f"/api/contact-views/{shared['id']}",
        json={"name": "Robo"},
        headers=auth_headers(client, "manager"),
    )
    assert response.status_code == 403


def test_setting_default_demotes_previous_default(client: TestClient):
    a = _create_view(client, role="manager", name="A", is_default=True)
    b = _create_view(client, role="manager", name="B")
    # Promote B → A must drop its is_default.
    response = client.post(
        f"/api/contact-views/{b['id']}/set-default",
        headers=auth_headers(client, "manager"),
    )
    assert response.status_code == 200
    listed = client.get(
        "/api/contact-views", headers=auth_headers(client, "manager")
    ).json()
    by_id = {v["id"]: v for v in listed}
    assert by_id[a["id"]]["is_default"] is False
    assert by_id[b["id"]]["is_default"] is True


def test_duplicate_creates_owned_copy(client: TestClient):
    """Any user who can read a view can duplicate. The duplicate is
    owned by the duplicator with sharing/default reset so a copy of
    someone else's default doesn't become my default."""
    shared = _create_view(
        client, role="admin", name="Compartida", is_shared=True, is_default=True
    )
    response = client.post(
        f"/api/contact-views/{shared['id']}/duplicate",
        json={"name": "Mi copia"},
        headers=auth_headers(client, "manager"),
    )
    assert response.status_code == 201
    duplicate = response.json()
    assert duplicate["name"] == "Mi copia"
    assert duplicate["is_owner"] is True
    assert duplicate["is_shared"] is False
    assert duplicate["is_default"] is False


def test_delete_view_blocked_for_non_owner(client: TestClient):
    shared = _create_view(client, role="admin", is_shared=True)
    response = client.delete(
        f"/api/contact-views/{shared['id']}",
        headers=auth_headers(client, "manager"),
    )
    assert response.status_code == 403


def test_view_id_applies_filters_to_contacts_list(client: TestClient):
    """A saved filter (e.g. q="ana") narrows the result set when the
    operator points the contacts list at the view via `?view_id=...`."""
    headers = auth_headers(client, "manager")
    for first_name, email in (
        ("Ana", "ana@example.com"),
        ("Boris", "boris@example.com"),
    ):
        client.post(
            "/api/contacts",
            json={
                "first_name": first_name,
                "email": email,
                "marketing_consent": "unknown",
            },
            headers=headers,
        )
    view = _create_view(client, filters={"q": "ana"})

    response = client.get(
        f"/api/contacts?view_id={view['id']}",
        headers=auth_headers(client, "manager"),
    )
    body = response.json()
    emails = sorted(item["email"] for item in body["items"])
    assert emails == ["ana@example.com"]


def test_view_id_filters_overridden_by_explicit_query_param(client: TestClient):
    """A URL param wins over a view's saved value. Operator typed
    `q=` (explicit reset) → view's q="ana" is dropped."""
    headers = auth_headers(client, "manager")
    for first_name, email in (
        ("Ana", "ana@example.com"),
        ("Boris", "boris@example.com"),
    ):
        client.post(
            "/api/contacts",
            json={
                "first_name": first_name,
                "email": email,
                "marketing_consent": "unknown",
            },
            headers=headers,
        )
    view = _create_view(client, filters={"q": "ana"})

    response = client.get(
        f"/api/contacts?view_id={view['id']}&q=boris",
        headers=auth_headers(client, "manager"),
    )
    body = response.json()
    emails = sorted(item["email"] for item in body["items"])
    assert emails == ["boris@example.com"]


def test_view_id_for_private_view_of_other_user_is_404(client: TestClient):
    """A private view belonging to another user must never leak. The
    UI route should 404 rather than 403 so listings don't enumerate
    private ids."""
    private = _create_view(client, role="admin", name="Privada")
    response = client.get(
        f"/api/contact-views/{private['id']}",
        headers=auth_headers(client, "manager"),
    )
    assert response.status_code == 404
