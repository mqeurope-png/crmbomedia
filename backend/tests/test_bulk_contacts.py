"""Bulk-action endpoint smoke tests."""
from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import get_session
from app.main import app
from app.models.crm import (
    Base,
    Contact,
    ContactTag,
    Tag,
    User,
    UserRole,
)
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
        seed.add_all(
            [
                Contact(first_name="A", email="a@example.com"),
                Contact(first_name="B", email="b@example.com"),
                Contact(first_name="C", email="c@example.com"),
            ]
        )
        seed.add(Tag(name="VIP", name_normalized="vip"))
        seed.commit()
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


def _all_contact_ids(session_factory: sessionmaker) -> list[str]:
    with session_factory() as s:
        return [c.id for c in s.scalars(select(Contact))]


def test_assign_owner_accepts_role_user(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """PR-Ca hotfix — decisión §1 spec Reglas-Assign. Cualquier
    comercial puede auto-asignarse o asignar a otro vía bulk; antes el
    endpoint pedía manager+ y rompía el flujo de cartera personal."""
    contact_ids = _all_contact_ids(session_factory)
    with session_factory() as s:
        manager_id = s.scalar(select(User.id).where(User.role == UserRole.MANAGER))
    resp = client.post(
        "/api/contacts/bulk-action",
        json={
            "contact_ids": contact_ids,
            "action": "assign_owner",
            "payload": {"owner_user_id": manager_id},
        },
        headers=auth_headers(client, "user"),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["affected_count"] == len(contact_ids)


def test_assign_owner_manager_succeeds(
    client: TestClient, session_factory: sessionmaker
) -> None:
    contact_ids = _all_contact_ids(session_factory)
    with session_factory() as s:
        manager_id = s.scalar(select(User.id).where(User.role == UserRole.MANAGER))
    resp = client.post(
        "/api/contacts/bulk-action",
        json={
            "contact_ids": contact_ids,
            "action": "assign_owner",
            "payload": {"owner_user_id": manager_id},
        },
        headers=auth_headers(client, "manager"),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["affected_count"] == 3
    with session_factory() as s:
        owners = {c.owner_user_id for c in s.scalars(select(Contact))}
    assert owners == {manager_id}


def test_change_status_user_ok(
    client: TestClient, session_factory: sessionmaker
) -> None:
    contact_ids = _all_contact_ids(session_factory)
    resp = client.post(
        "/api/contacts/bulk-action",
        json={
            "contact_ids": contact_ids,
            "action": "change_status",
            "payload": {"new_status": "qualified"},
        },
        headers=auth_headers(client, "user"),
    )
    assert resp.status_code == 200
    assert resp.json()["affected_count"] == 3


def test_add_tag_creates_assignments_only_once(
    client: TestClient, session_factory: sessionmaker
) -> None:
    contact_ids = _all_contact_ids(session_factory)
    with session_factory() as s:
        tag_id = s.scalar(select(Tag.id))
    resp1 = client.post(
        "/api/contacts/bulk-action",
        json={
            "contact_ids": contact_ids,
            "action": "add_tag",
            "payload": {"tag_id": tag_id},
        },
        headers=auth_headers(client, "user"),
    )
    assert resp1.status_code == 200
    assert resp1.json()["affected_count"] == 3
    # Re-run — none should be added a second time.
    resp2 = client.post(
        "/api/contacts/bulk-action",
        json={
            "contact_ids": contact_ids,
            "action": "add_tag",
            "payload": {"tag_id": tag_id},
        },
        headers=auth_headers(client, "user"),
    )
    assert resp2.json()["affected_count"] == 0
    with session_factory() as s:
        assignments = list(s.scalars(select(ContactTag)))
    assert len(assignments) == 3


def test_deactivate_only_admin(
    client: TestClient, session_factory: sessionmaker
) -> None:
    contact_ids = _all_contact_ids(session_factory)
    blocked = client.post(
        "/api/contacts/bulk-action",
        json={"contact_ids": contact_ids, "action": "deactivate"},
        headers=auth_headers(client, "manager"),
    )
    assert blocked.status_code == 403
    ok = client.post(
        "/api/contacts/bulk-action",
        json={"contact_ids": contact_ids, "action": "deactivate"},
        headers=auth_headers(client, "admin"),
    )
    assert ok.status_code == 200
    assert ok.json()["affected_count"] == 3


def test_bulk_rejects_oversized_selection(client: TestClient) -> None:
    resp = client.post(
        "/api/contacts/bulk-action",
        json={
            "contact_ids": [f"x-{i}" for i in range(1001)],
            "action": "change_status",
            "payload": {"new_status": "qualified"},
        },
        headers=auth_headers(client, "user"),
    )
    assert resp.status_code == 422


def test_search_ids_returns_uuids_only(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """`POST /api/contacts/search/ids` returns just the UUIDs of the
    matching contacts — used by the "Seleccionar todos del filtro"
    banner so the client can expand the selection without pulling
    every Contact body."""
    response = client.post(
        "/api/contacts/search/ids",
        json={},
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert isinstance(body["ids"], list)
    assert body["count"] == 3
    assert body["truncated"] is False
    assert body["max_ids"] == 10_000


def test_search_ids_respects_assigned_to_me(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """The same `assigned_to_me` flag the search endpoint honours
    must trim the id list — otherwise the bulk banner could end up
    asking the user to act on contacts they don't own."""
    with session_factory() as s:
        user_id = s.scalar(select(User.id).where(User.role == UserRole.USER))
        # Tag the first contact as owned by the user.
        first = s.scalars(select(Contact)).first()
        assert first is not None
        first.owner_user_id = user_id
        s.commit()
    response = client.post(
        "/api/contacts/search/ids",
        json={"assigned_to_me": True},
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 200
    assert response.json()["count"] == 1
