"""Per-user email signatures — CRUD + default-toggle tests."""
from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import get_session
from app.main import app
from app.models.crm import Base, EmailSignature
from tests._test_helpers import auth_headers, seed_test_users


@pytest.fixture()
def session_factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as seed:
        seed_test_users(seed)
    yield factory
    Base.metadata.drop_all(engine)


@pytest.fixture()
def client(session_factory: sessionmaker) -> Generator[TestClient, None, None]:
    def override_session() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _create(client: TestClient, headers: dict[str, str], **kwargs) -> dict:
    payload = {
        "name": "Comercial",
        "html_content": "<p>Saludos, Bart</p>",
        "is_default": False,
        "sort_order": 0,
    }
    payload.update(kwargs)
    response = client.post(
        "/api/email-signatures", json=payload, headers=headers
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_create_and_list_signature(
    client: TestClient, session_factory: sessionmaker
) -> None:
    headers = auth_headers(client, role="user")
    created = _create(client, headers, name="Comercial")
    assert created["is_default"] is False

    listing = client.get("/api/email-signatures", headers=headers)
    assert listing.status_code == 200
    items = listing.json()
    assert len(items) == 1
    assert items[0]["id"] == created["id"]

    with session_factory() as session:
        row = session.get(EmailSignature, created["id"])
        assert row is not None
        assert row.user_id is not None


def test_create_with_default_flag_clears_others(
    client: TestClient, session_factory: sessionmaker
) -> None:
    headers = auth_headers(client, role="user")
    first = _create(client, headers, name="Comercial", is_default=True)
    second = _create(client, headers, name="Soporte", is_default=True)

    assert second["is_default"] is True
    with session_factory() as session:
        row = session.get(EmailSignature, first["id"])
        assert row is not None and row.is_default is False


def test_set_default_endpoint_singles_out_one(
    client: TestClient, session_factory: sessionmaker
) -> None:
    headers = auth_headers(client, role="user")
    a = _create(client, headers, name="A", is_default=True)
    b = _create(client, headers, name="B")

    response = client.post(
        f"/api/email-signatures/{b['id']}/default", headers=headers
    )
    assert response.status_code == 200
    assert response.json()["is_default"] is True

    with session_factory() as session:
        rows = list(
            session.scalars(
                select(EmailSignature).where(EmailSignature.is_default.is_(True))
            )
        )
        assert len(rows) == 1
        assert rows[0].id == b["id"]
    assert a["id"] != b["id"]


def test_update_signature(client: TestClient) -> None:
    headers = auth_headers(client, role="user")
    created = _create(client, headers)
    response = client.put(
        f"/api/email-signatures/{created['id']}",
        json={
            "name": "Editada",
            "html_content": "<p>Nuevo cuerpo</p>",
            "is_default": False,
            "sort_order": 5,
        },
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Editada"
    assert body["sort_order"] == 5


def test_delete_signature(
    client: TestClient, session_factory: sessionmaker
) -> None:
    headers = auth_headers(client, role="user")
    created = _create(client, headers)
    response = client.delete(
        f"/api/email-signatures/{created['id']}", headers=headers
    )
    assert response.status_code == 200
    with session_factory() as session:
        assert session.get(EmailSignature, created["id"]) is None


def test_user_cannot_touch_another_users_signature(
    client: TestClient,
) -> None:
    user_headers = auth_headers(client, role="user")
    created = _create(client, user_headers)
    manager_headers = auth_headers(client, role="manager")
    response = client.delete(
        f"/api/email-signatures/{created['id']}", headers=manager_headers
    )
    # Strict isolation: the resource simply doesn't exist for another
    # user — 404 keeps us from leaking the existence of someone else's
    # signature via 403 vs 404.
    assert response.status_code == 404


def test_default_endpoint_returns_null_when_unset(
    client: TestClient,
) -> None:
    headers = auth_headers(client, role="user")
    _create(client, headers)
    response = client.get("/api/email-signatures/default", headers=headers)
    assert response.status_code == 200
    assert response.json() is None


def test_default_endpoint_returns_the_marked_one(
    client: TestClient,
) -> None:
    headers = auth_headers(client, role="user")
    _create(client, headers, name="A")
    chosen = _create(client, headers, name="B", is_default=True)
    response = client.get("/api/email-signatures/default", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body is not None
    assert body["id"] == chosen["id"]
