"""Tasks productivity layer — CRUD, buckets, role gating."""
from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import get_session
from app.main import app
from app.models.crm import Base, Contact, Task, User, UserRole
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


def _create_contact(client: TestClient, email: str = "ana@example.com") -> dict:
    response = client.post(
        "/api/contacts",
        json={"first_name": "Ana", "email": email},
        headers=auth_headers(client, "manager"),
    )
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Create + read
# ---------------------------------------------------------------------------


def test_create_minimal_task_defaults_assignee_to_caller(client: TestClient):
    response = client.post(
        "/api/tasks",
        json={"title": "Llamar lead"},
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["title"] == "Llamar lead"
    assert body["status"] == "pending"
    assert body["priority"] == "medium"
    # The caller becomes assignee + creator.
    assert body["assigned_user_id"] == body["created_by_user_id"]


def test_create_task_with_contact_emits_activity_event(client: TestClient):
    contact = _create_contact(client)
    response = client.post(
        "/api/tasks",
        json={"title": "Seguimiento", "contact_id": contact["id"]},
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 201, response.text
    # The contact's activity timeline picked up the create.
    detail = client.get(
        f"/api/contacts/{contact['id']}",
        headers=auth_headers(client, "user"),
    ).json()
    event_types = [evt["event_type"] for evt in detail["activity_events"]]
    assert "task.created" in event_types


def test_create_task_rejects_unknown_contact(client: TestClient):
    response = client.post(
        "/api/tasks",
        json={"title": "x", "contact_id": "00000000-0000-0000-0000-000000000000"},
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 400


def test_list_tasks_for_contact(client: TestClient):
    contact = _create_contact(client)
    headers = auth_headers(client, "user")
    client.post(
        "/api/tasks", json={"title": "A", "contact_id": contact["id"]}, headers=headers
    )
    client.post(
        "/api/tasks", json={"title": "B", "contact_id": contact["id"]}, headers=headers
    )
    items = client.get(
        f"/api/contacts/{contact['id']}/tasks", headers=headers
    ).json()
    assert {item["title"] for item in items} == {"A", "B"}


# ---------------------------------------------------------------------------
# Update + complete
# ---------------------------------------------------------------------------


def test_complete_task_sets_completed_at_and_emits_event(
    client: TestClient, session_factory: sessionmaker
):
    contact = _create_contact(client)
    headers = auth_headers(client, "user")
    task = client.post(
        "/api/tasks",
        json={"title": "Cerrar", "contact_id": contact["id"]},
        headers=headers,
    ).json()
    response = client.post(f"/api/tasks/{task['id']}/complete", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()["task"]
    assert body["status"] == "done"
    assert body["completed_at"] is not None

    detail = client.get(
        f"/api/contacts/{contact['id']}",
        headers=auth_headers(client, "user"),
    ).json()
    event_types = [evt["event_type"] for evt in detail["activity_events"]]
    assert "task.completed" in event_types


def test_reassign_blocked_for_non_admin(
    client: TestClient, session_factory: sessionmaker
):
    headers = auth_headers(client, "user")
    task = client.post(
        "/api/tasks", json={"title": "Mía"}, headers=headers
    ).json()
    # Manager id from the seeded users.
    with session_factory() as session:
        manager_id = session.scalar(
            __import__("sqlalchemy").select(User.id).where(
                User.role == UserRole.MANAGER
            )
        )
    response = client.patch(
        f"/api/tasks/{task['id']}",
        json={"assigned_user_id": manager_id},
        headers=headers,
    )
    assert response.status_code == 403


def test_admin_can_reassign(client: TestClient, session_factory: sessionmaker):
    headers_admin = auth_headers(client, "admin")
    task = client.post(
        "/api/tasks", json={"title": "Admin task"}, headers=headers_admin
    ).json()
    with session_factory() as session:
        manager_id = session.scalar(
            __import__("sqlalchemy").select(User.id).where(
                User.role == UserRole.MANAGER
            )
        )
    response = client.patch(
        f"/api/tasks/{task['id']}",
        json={"assigned_user_id": manager_id},
        headers=headers_admin,
    )
    assert response.status_code == 200
    assert response.json()["assigned_user_id"] == manager_id


def test_non_owner_non_admin_cannot_mutate(client: TestClient):
    """The creator (user role) is also the assignee. A different user
    that's neither admin nor manager can't touch the task."""
    headers_user = auth_headers(client, "user")
    task = client.post(
        "/api/tasks", json={"title": "Privada"}, headers=headers_user
    ).json()
    response = client.patch(
        f"/api/tasks/{task['id']}",
        json={"title": "Pirateada"},
        headers=auth_headers(client, "viewer"),
    )
    # Viewer hits the require_user gate first (403), which is the right
    # outcome — they shouldn't reach the ownership check anyway.
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Buckets
# ---------------------------------------------------------------------------


def test_my_buckets_groups_by_urgency(
    client: TestClient, session_factory: sessionmaker
):
    headers = auth_headers(client, "user")
    now = datetime.now(UTC)
    # Seed three tasks at different due dates. `+2 hours` for the
    # today bucket used to roll past midnight when CI ran late in
    # the day — the task ended up in `tomorrow`. `+5 minutes` is
    # safely within today's bucket as long as we're before ~23:54
    # UTC, which is 99.7 % of the seconds in a day.
    overdue = (now - timedelta(days=2)).isoformat()
    today = (now + timedelta(minutes=5)).isoformat()
    later = (now + timedelta(days=5)).isoformat()
    for title, due in (("Vencida", overdue), ("Hoy", today), ("Próx", later)):
        client.post(
            "/api/tasks",
            json={"title": title, "due_at": due},
            headers=headers,
        )

    response = client.get("/api/tasks/my-buckets", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert [t["title"] for t in body["overdue"]] == ["Vencida"]
    assert [t["title"] for t in body["today"]] == ["Hoy"]
    assert [t["title"] for t in body["later"]] == ["Próx"]
    assert body["total_open"] == 3


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


def test_delete_task_removes_row_and_emits_event(
    client: TestClient, session_factory: sessionmaker
):
    contact = _create_contact(client)
    headers = auth_headers(client, "user")
    task = client.post(
        "/api/tasks",
        json={"title": "Borrar", "contact_id": contact["id"]},
        headers=headers,
    ).json()
    response = client.delete(f"/api/tasks/{task['id']}", headers=headers)
    assert response.status_code == 200

    with session_factory() as session:
        assert session.get(Task, task["id"]) is None

    detail = client.get(
        f"/api/contacts/{contact['id']}",
        headers=auth_headers(client, "user"),
    ).json()
    event_types = [evt["event_type"] for evt in detail["activity_events"]]
    assert "task.deleted" in event_types


def test_viewer_can_read_but_not_create(client: TestClient):
    headers = auth_headers(client, "viewer")
    listed = client.get("/api/tasks", headers=headers)
    assert listed.status_code == 200
    blocked = client.post(
        "/api/tasks", json={"title": "No"}, headers=headers
    )
    assert blocked.status_code == 403
    _ = Contact  # appease the unused-import linter
