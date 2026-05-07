from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import hash_password
from app.db.session import get_session
from app.main import app
from app.models.crm import Base, User, UserRole


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
        for role in UserRole:
            seed_session.add(
                User(
                    email=f"{role.value}@example.com",
                    full_name=f"{role.value.title()} User",
                    password_hash=hash_password("password123"),
                    role=role,
                    is_active=True,
                )
            )
        seed_session.commit()

    def override_session() -> Generator[Session, None, None]:
        with testing_session() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)


def auth_headers(client: TestClient, role: str = "admin") -> dict[str, str]:
    response = client.post(
        "/api/auth/login",
        json={"email": f"{role}@example.com", "password": "password123"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def create_contact(client: TestClient, email: str = "ana@example.com") -> dict:
    response = client.post(
        "/api/contacts",
        json={
            "first_name": "Ana",
            "last_name": "García",
            "email": email,
            "origin": "agilecrm",
            "marketing_consent": "granted",
        },
        headers=auth_headers(client, "manager"),
    )
    assert response.status_code == 201
    return response.json()


def test_health_returns_app_metadata(client: TestClient):
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_login_and_current_user(client: TestClient):
    headers = auth_headers(client, "admin")

    response = client.get("/api/auth/me", headers=headers)

    assert response.status_code == 200
    assert response.json()["email"] == "admin@example.com"
    assert response.json()["role"] == "admin"


def test_reject_invalid_login(client: TestClient):
    response = client.post(
        "/api/auth/login", json={"email": "admin@example.com", "password": "wrong"}
    )

    assert response.status_code == 401


def test_protect_crm_endpoints(client: TestClient):
    response = client.get("/api/contacts")

    assert response.status_code == 401


def test_admin_can_create_and_list_users(client: TestClient):
    headers = auth_headers(client, "admin")

    created = client.post(
        "/api/users",
        json={
            "email": "new-user@example.com",
            "full_name": "New User",
            "password": "password123",
            "role": "viewer",
        },
        headers=headers,
    )
    listed = client.get("/api/users", headers=headers)

    updated = client.patch(
        f"/api/users/{created.json()['id']}",
        json={"role": "user", "full_name": "Updated User"},
        headers=headers,
    )

    assert created.status_code == 201
    assert updated.status_code == 200
    assert updated.json()["role"] == "user"
    assert any(user["email"] == "new-user@example.com" for user in listed.json())


def test_non_admin_cannot_manage_users(client: TestClient):
    response = client.get("/api/users", headers=auth_headers(client, "manager"))

    assert response.status_code == 403


def test_create_company_requires_manager(client: TestClient):
    viewer_response = client.post(
        "/api/companies", json={"name": "MQ Europe"}, headers=auth_headers(client, "viewer")
    )
    manager_response = client.post(
        "/api/companies", json={"name": "MQ Europe"}, headers=auth_headers(client, "manager")
    )

    assert viewer_response.status_code == 403
    assert manager_response.status_code == 201


def test_list_companies_with_search_and_pagination(client: TestClient):
    headers = auth_headers(client, "manager")
    client.post("/api/companies", json={"name": "Alpha"}, headers=headers)
    client.post("/api/companies", json={"name": "Beta"}, headers=headers)

    response = client.get(
        "/api/companies",
        params={"q": "alp", "skip": 0, "limit": 10},
        headers=auth_headers(client, "viewer"),
    )

    assert response.status_code == 200
    assert [company["name"] for company in response.json()] == ["Alpha"]


def test_update_and_deactivate_company(client: TestClient):
    headers = auth_headers(client, "manager")
    company = client.post("/api/companies", json={"name": "Old"}, headers=headers).json()

    updated = client.patch(
        f"/api/companies/{company['id']}", json={"name": "New"}, headers=headers
    )
    deactivated = client.patch(f"/api/companies/{company['id']}/deactivate", headers=headers)

    assert updated.status_code == 200
    assert updated.json()["name"] == "New"
    assert deactivated.status_code == 200
    assert deactivated.json()["is_active"] is False


def test_create_contact_with_company_and_persist_consent(client: TestClient):
    headers = auth_headers(client, "manager")
    company = client.post("/api/companies", json={"name": "Empresa"}, headers=headers).json()

    response = client.post(
        "/api/contacts",
        json={
            "first_name": "Ana",
            "email": "ANA@example.com",
            "company_id": company["id"],
            "marketing_consent": "unsubscribed",
        },
        headers=headers,
    )

    assert response.status_code == 201
    assert response.json()["email"] == "ana@example.com"
    assert response.json()["marketing_consent"] == "unsubscribed"
    assert response.json()["company_id"] == company["id"]


def test_reject_contact_for_missing_company(client: TestClient):
    response = client.post(
        "/api/contacts",
        json={"first_name": "Ana", "email": "ana@example.com", "company_id": "missing"},
        headers=auth_headers(client, "manager"),
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Company not found"


def test_viewer_cannot_create_contact(client: TestClient):
    response = client.post(
        "/api/contacts",
        json={"first_name": "Luis", "email": "luis@example.com"},
        headers=auth_headers(client, "viewer"),
    )

    assert response.status_code == 403


def test_reject_duplicate_contact_email(client: TestClient):
    create_contact(client, "luis@example.com")

    duplicate = client.post(
        "/api/contacts",
        json={"first_name": "Luis", "email": "LUIS@example.com"},
        headers=auth_headers(client, "manager"),
    )

    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "A contact with this email already exists"


def test_reject_invalid_contact_email(client: TestClient):
    response = client.post(
        "/api/contacts",
        json={"first_name": "Luis", "email": "not-email"},
        headers=auth_headers(client, "manager"),
    )

    assert response.status_code == 422


def test_search_and_paginate_contacts(client: TestClient):
    create_contact(client, "ana@example.com")
    create_contact(client, "marta@example.com")

    response = client.get(
        "/api/contacts",
        params={"q": "mart", "skip": 0, "limit": 5},
        headers=auth_headers(client, "viewer"),
    )

    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["email"] == "marta@example.com"


def test_get_contact_detail(client: TestClient):
    contact = create_contact(client)

    response = client.get(f"/api/contacts/{contact['id']}", headers=auth_headers(client, "viewer"))

    assert response.status_code == 200
    assert response.json()["id"] == contact["id"]
    assert response.json()["notes"] == []
    assert response.json()["tasks"] == []


def test_missing_contact_returns_404(client: TestClient):
    response = client.get("/api/contacts/missing", headers=auth_headers(client, "viewer"))

    assert response.status_code == 404
    assert response.json()["detail"] == "Contact not found"


def test_update_and_deactivate_contact(client: TestClient):
    contact = create_contact(client)
    headers = auth_headers(client, "manager")

    updated = client.patch(
        f"/api/contacts/{contact['id']}",
        json={"first_name": "Ana María", "commercial_status": "qualified"},
        headers=headers,
    )
    deactivated = client.patch(f"/api/contacts/{contact['id']}/deactivate", headers=headers)

    assert updated.status_code == 200
    assert updated.json()["first_name"] == "Ana María"
    assert updated.json()["commercial_status"] == "qualified"
    assert deactivated.status_code == 200
    assert deactivated.json()["is_active"] is False


def test_user_can_create_note_and_task_for_contact(client: TestClient):
    contact = create_contact(client)
    headers = auth_headers(client, "user")

    note = client.post(
        f"/api/contacts/{contact['id']}/notes", json={"body": "Llamada"}, headers=headers
    )
    task = client.post(
        f"/api/contacts/{contact['id']}/tasks", json={"title": "Enviar propuesta"}, headers=headers
    )

    assert note.status_code == 201
    assert task.status_code == 201
    assert task.json()["status"] == "open"


def test_viewer_cannot_create_note_or_task(client: TestClient):
    contact = create_contact(client)
    headers = auth_headers(client, "viewer")

    note = client.post(
        f"/api/contacts/{contact['id']}/notes", json={"body": "No"}, headers=headers
    )
    task = client.post(
        f"/api/contacts/{contact['id']}/tasks", json={"title": "No"}, headers=headers
    )

    assert note.status_code == 403
    assert task.status_code == 403


def test_reject_note_and_task_for_missing_contact(client: TestClient):
    headers = auth_headers(client, "user")

    note = client.post("/api/contacts/missing/notes", json={"body": "Nota"}, headers=headers)
    task = client.post("/api/contacts/missing/tasks", json={"title": "Enviar"}, headers=headers)

    assert note.status_code == 404
    assert task.status_code == 404


def test_audit_log_records_login_and_crm_actions(client: TestClient):
    headers = auth_headers(client, "admin")
    create_contact(client, "audit@example.com")

    response = client.get("/api/audit-logs", headers=headers)

    assert response.status_code == 200
    actions = {entry["action"] for entry in response.json()}
    assert "login" in actions
    assert "create_contact" in actions


def test_change_current_user_password(client: TestClient):
    headers = auth_headers(client, "user")

    changed = client.post(
        "/api/auth/change-password",
        json={"current_password": "password123", "new_password": "new-password123"},
        headers=headers,
    )
    login_response = client.post(
        "/api/auth/login", json={"email": "user@example.com", "password": "new-password123"}
    )

    assert changed.status_code == 200
    assert login_response.status_code == 200


def test_password_reset_request_and_confirm(client: TestClient):
    requested = client.post(
        "/api/auth/password-reset/request", json={"email": "viewer@example.com"}
    )
    token = requested.json()["reset_token"]

    confirmed = client.post(
        "/api/auth/password-reset/confirm",
        json={"token": token, "new_password": "reset-password123"},
    )
    login_response = client.post(
        "/api/auth/login", json={"email": "viewer@example.com", "password": "reset-password123"}
    )

    assert requested.status_code == 200
    assert token
    assert confirmed.status_code == 200
    assert login_response.status_code == 200


def test_admin_can_deactivate_and_reactivate_user(client: TestClient):
    headers = auth_headers(client, "admin")
    user_id = client.get("/api/users", headers=headers).json()[0]["id"]

    deactivated = client.patch(f"/api/users/{user_id}/deactivate", headers=headers)
    reactivated = client.patch(f"/api/users/{user_id}/reactivate", headers=headers)

    assert deactivated.status_code == 200
    assert deactivated.json()["is_active"] is False
    assert reactivated.status_code == 200
    assert reactivated.json()["is_active"] is True


def test_admin_can_update_user_password(client: TestClient):
    headers = auth_headers(client, "admin")
    created = client.post(
        "/api/users",
        json={
            "email": "password-target@example.com",
            "full_name": "Password Target",
            "password": "password123",
            "role": "viewer",
        },
        headers=headers,
    )

    changed = client.patch(
        f"/api/users/{created.json()['id']}/password",
        json={"new_password": "admin-set-password123"},
        headers=headers,
    )
    login_response = client.post(
        "/api/auth/login",
        json={"email": "password-target@example.com", "password": "admin-set-password123"},
    )

    assert changed.status_code == 200
    assert login_response.status_code == 200


def test_audit_export_csv_and_json_requires_admin(client: TestClient):
    create_contact(client, "export@example.com")
    admin_headers = auth_headers(client, "admin")
    manager_headers = auth_headers(client, "manager")

    forbidden = client.get("/api/audit-logs/export", headers=manager_headers)
    csv_response = client.get("/api/audit-logs/export?format=csv", headers=admin_headers)
    json_response = client.get("/api/audit-logs/export?format=json", headers=admin_headers)

    assert forbidden.status_code == 403
    assert csv_response.status_code == 200
    assert "create_contact" in csv_response.text
    assert json_response.status_code == 200
    assert any(row["action"] == "create_contact" for row in json_response.json())
