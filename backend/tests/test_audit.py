"""Tests for the expanded audit log: action coverage, filtering, export
limits + the audit row written by the export endpoint itself."""
from __future__ import annotations

import json
from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.audit import Action
from app.db.session import get_session
from app.main import app
from app.models.crm import AuditLog, Base
from tests._test_helpers import (
    ADMIN_TOTP_SECRET,
    DEFAULT_PASSWORD,
    auth_headers,
    seed_test_users,
)

STRONG_PASSWORD = "StrongPass1234!"


@pytest.fixture()
def stack() -> Generator[tuple[TestClient, sessionmaker], None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with session_factory() as seed:
        seed_test_users(seed)

    def override_session() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as client:
        yield client, session_factory
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)


@pytest.fixture()
def client(stack: tuple[TestClient, sessionmaker]) -> TestClient:
    return stack[0]


def _actions_of(rows: list[dict]) -> set[str]:
    return {row["action"] for row in rows}


def _fetch_audits(client: TestClient, headers: dict[str, str], **params) -> list[dict]:
    response = client.get("/api/audit-logs", params=params, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Event coverage
# ---------------------------------------------------------------------------


def test_login_success_is_recorded_with_ip_and_user_agent(client: TestClient):
    response = client.post(
        "/api/auth/login",
        json={"email": "viewer@example.com", "password": DEFAULT_PASSWORD},
        headers={"User-Agent": "pytest/1.0", "X-Forwarded-For": "203.0.113.7"},
    )
    assert response.status_code == 200

    headers = auth_headers(client, "admin")
    rows = _fetch_audits(client, headers, action=Action.AUTH_LOGIN_SUCCESS)
    assert rows, "expected an auth.login_success audit row"
    found = next(r for r in rows if r.get("actor_email") == "viewer@example.com")
    assert found["target_type"] == "user"
    assert found["target_id"]
    assert found["ip_address"] == "203.0.113.7"  # XFF leftmost wins
    assert found["user_agent"] == "pytest/1.0"


def test_login_failed_is_recorded_with_reason(client: TestClient):
    client.post(
        "/api/auth/login",
        json={"email": "ghost@example.com", "password": "whatever"},
        headers={"User-Agent": "pytest/1.0", "X-Real-IP": "198.51.100.1"},
    )
    headers = auth_headers(client, "admin")
    rows = _fetch_audits(client, headers, action=Action.AUTH_LOGIN_FAILED)
    assert any(r["actor_email"] == "ghost@example.com" for r in rows)
    failed = next(r for r in rows if r["actor_email"] == "ghost@example.com")
    assert failed["ip_address"] == "198.51.100.1"
    assert failed["metadata"]["reason"] == "user_not_found"


def test_password_change_audits_with_actor(client: TestClient):
    headers = auth_headers(client, "user")
    response = client.post(
        "/api/auth/change-password",
        json={
            "current_password": DEFAULT_PASSWORD,
            "new_password": STRONG_PASSWORD,
        },
        headers=headers,
    )
    assert response.status_code == 200
    admin_headers = auth_headers(client, "admin")
    rows = _fetch_audits(client, admin_headers, action=Action.AUTH_PASSWORD_CHANGED)
    assert any(r["actor_email"] == "user@example.com" for r in rows)


def test_user_lifecycle_events_recorded(client: TestClient):
    headers = auth_headers(client, "admin")
    create = client.post(
        "/api/users",
        json={
            "email": "audit-target@example.com",
            "full_name": "Audit Target",
            "password": STRONG_PASSWORD,
            "role": "viewer",
        },
        headers=headers,
    )
    assert create.status_code == 201
    user_id = create.json()["id"]

    # Role change → both user.updated AND user.role_changed.
    update = client.patch(
        f"/api/users/{user_id}",
        json={"role": "manager"},
        headers=headers,
    )
    assert update.status_code == 200

    client.patch(f"/api/users/{user_id}/deactivate", headers=headers)
    client.patch(f"/api/users/{user_id}/reactivate", headers=headers)
    client.patch(
        f"/api/users/{user_id}/password",
        json={"new_password": "AdminSetPass123!"},
        headers=headers,
    )

    rows = _fetch_audits(client, headers, target_type="user", limit=100)
    seen = _actions_of(rows)
    assert Action.USER_CREATED in seen
    assert Action.USER_UPDATED in seen
    assert Action.USER_ROLE_CHANGED in seen
    assert Action.USER_DEACTIVATED in seen
    assert Action.USER_REACTIVATED in seen
    assert Action.USER_PASSWORD_SET_BY_ADMIN in seen

    role_change = next(r for r in rows if r["action"] == Action.USER_ROLE_CHANGED)
    assert role_change["metadata"]["from_role"] == "viewer"
    assert role_change["metadata"]["to_role"] == "manager"


def test_2fa_events_recorded(client: TestClient):
    """The admin is already enrolled, so the verify call produced by the
    auth_headers helper already covers the enabled+verified branch."""
    auth_headers(client, "admin")  # triggers AUTH_2FA_VERIFIED
    headers = auth_headers(client, "admin")
    rows = _fetch_audits(client, headers, action_prefix="auth.")
    seen = _actions_of(rows)
    assert Action.AUTH_LOGIN_SUCCESS in seen
    assert Action.AUTH_2FA_VERIFIED in seen


def test_integration_api_key_events_recorded_and_metadata_safe(
    client: TestClient, stack
):
    """Seed a `default` account for brevo (the multi-account schema does
    not auto-create rows) and round-trip the API key to confirm the
    `integration_account.api_key_*` audit rows are emitted without ever
    leaking the secret."""
    _, session_factory = stack
    with session_factory() as session:
        from app.models.crm import ExternalSystem
        from app.models.integration_settings import IntegrationAccount

        session.add(
            IntegrationAccount(
                system=ExternalSystem.BREVO,
                account_id="default",
                display_name="Brevo",
            )
        )
        session.commit()

    headers = auth_headers(client, "admin")
    secret_value = "must-not-leak-secret-xyz"
    client.put(
        "/api/integration-accounts/brevo/default/api-key",
        json={"api_key": secret_value},
        headers=headers,
    )
    client.delete("/api/integration-accounts/brevo/default/api-key", headers=headers)

    response = client.get(
        "/api/audit-logs",
        params={"action_prefix": "integration_account."},
        headers=headers,
    )
    assert response.status_code == 200
    rows = response.json()
    seen = _actions_of(rows)
    assert Action.INTEGRATION_ACCOUNT_API_KEY_SET in seen
    assert Action.INTEGRATION_ACCOUNT_API_KEY_DELETED in seen
    # The secret must NEVER appear in metadata/message/anywhere in the body.
    assert secret_value not in response.text


def test_forbidden_access_is_audited(client: TestClient, stack):
    """A non-admin hitting an admin endpoint produces an access.forbidden row.

    `auth_headers` lands a manager token; calling /api/users (admin-only)
    triggers the role gate which audits the denial before raising 403.
    """
    manager = auth_headers(client, "manager")
    response = client.get("/api/users", headers=manager)
    assert response.status_code == 403

    # Inspect the audit table directly via the test session factory rather
    # than via the API (admin endpoint that requires us to log in as admin).
    _, session_factory = stack
    with session_factory() as session:
        denied = (
            session.query(AuditLog)
            .filter(AuditLog.action == Action.ACCESS_FORBIDDEN)
            .all()
        )
    assert denied, "expected an access.forbidden audit row"
    last = denied[-1]
    assert last.target_type == "endpoint"
    assert last.target_id == "/api/users"
    assert last.actor_email == "manager@example.com"
    metadata = json.loads(last.metadata_json or "{}")
    assert metadata["required_role"] == "admin"
    assert metadata["actual_role"] == "manager"
    assert metadata["method"] == "GET"


# ---------------------------------------------------------------------------
# Filters + pagination
# ---------------------------------------------------------------------------


def test_filter_by_action_prefix_and_actor(client: TestClient):
    admin_headers = auth_headers(client, "admin")
    client.post("/api/companies", json={"name": "Pref"}, headers=auth_headers(client, "manager"))
    rows_auth = _fetch_audits(client, admin_headers, action_prefix="auth.")
    rows_company = _fetch_audits(client, admin_headers, target_type="company")

    assert all(r["action"].startswith("auth.") for r in rows_auth)
    assert all(r["target_type"] == "company" for r in rows_company)


def test_filter_by_date_range(client: TestClient):
    headers = auth_headers(client, "admin")
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    response = client.get(
        "/api/audit-logs",
        params={"from": future},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json() == []


def test_total_count_header_present(client: TestClient):
    headers = auth_headers(client, "admin")
    response = client.get("/api/audit-logs", headers=headers, params={"limit": 5})
    assert response.status_code == 200
    assert response.headers.get("x-total-count") is not None
    assert int(response.headers["x-total-count"]) >= len(response.json())


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def test_export_requires_admin(client: TestClient):
    manager = auth_headers(client, "manager")
    response = client.get("/api/audit-logs/export", headers=manager)
    assert response.status_code == 403


def test_export_csv_includes_new_columns(client: TestClient):
    headers = auth_headers(client, "admin")
    response = client.get(
        "/api/audit-logs/export", params={"format": "csv"}, headers=headers
    )
    assert response.status_code == 200
    header_line = response.text.splitlines()[0]
    for col in [
        "actor_email",
        "target_type",
        "target_id",
        "metadata",
        "ip_address",
        "user_agent",
    ]:
        assert col in header_line, f"missing CSV column: {col}"


def test_export_audits_itself(client: TestClient, stack):
    headers = auth_headers(client, "admin")
    client.get("/api/audit-logs/export", params={"format": "json"}, headers=headers)

    _, session_factory = stack
    with session_factory() as session:
        rows = (
            session.query(AuditLog)
            .filter(AuditLog.action == Action.AUDIT_EXPORTED)
            .all()
        )
    assert rows, "expected an audit.exported row written by the export endpoint"
    metadata = json.loads(rows[-1].metadata_json or "{}")
    assert metadata["format"] == "json"
    assert "rows" in metadata
    assert "filters" in metadata


def test_export_max_rows_overflow_returns_400(client: TestClient, stack, monkeypatch):
    """Stuff the audit table past the export cap to confirm overflow → 400."""
    import app.api.routes as routes_module

    monkeypatch.setattr(routes_module, "EXPORT_MAX_ROWS", 5)

    _, session_factory = stack
    with session_factory() as session:
        for i in range(10):
            session.add(
                AuditLog(
                    actor_email=f"bulk-{i}@example.com",
                    action="audit.bulk",
                    target_type="bulk",
                    target_id=str(i),
                )
            )
        session.commit()

    headers = auth_headers(client, "admin")
    response = client.get(
        "/api/audit-logs/export",
        params={"format": "json", "action": "audit.bulk"},
        headers=headers,
    )
    assert response.status_code == 400
    assert "Export exceeds" in response.text


def test_export_default_window_is_last_year(client: TestClient, stack):
    """Without `from`/`to`, the export should be capped to the last
    EXPORT_DEFAULT_WINDOW_DAYS — events older than the window are excluded."""
    _, session_factory = stack
    very_old = datetime.now(UTC) - timedelta(days=400)
    with session_factory() as session:
        session.add(
            AuditLog(
                actor_email="ancient@example.com",
                action="audit.ancient",
                target_type="bulk",
                target_id="old",
                created_at=very_old,
                updated_at=very_old,
            )
        )
        session.commit()

    headers = auth_headers(client, "admin")
    response = client.get(
        "/api/audit-logs/export", params={"format": "json"}, headers=headers
    )
    assert response.status_code == 200
    rows = json.loads(response.text)
    assert all(r["actor_email"] != "ancient@example.com" for r in rows)


def test_admin_only_for_listing(client: TestClient):
    response = client.get(
        "/api/audit-logs", headers=auth_headers(client, "manager")
    )
    assert response.status_code == 403


# Quiet ruff: ADMIN_TOTP_SECRET is imported in case future tests need it.
_ = ADMIN_TOTP_SECRET
_ = pyotp
