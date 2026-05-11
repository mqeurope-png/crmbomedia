"""Tests for TOTP 2FA + backup codes + the "limited admin" gate."""
from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.crypto import decrypt, encrypt
from app.core.security import hash_password
from app.db.session import get_session
from app.main import app
from app.models.crm import Base, User, UserRole

PASSWORD = "password123"
STRONG_PASSWORD = "StrongPass1234!"


class Stack:
    def __init__(self, client: TestClient, engine: Engine, session_factory: sessionmaker):
        self.client = client
        self.engine = engine
        self.session_factory = session_factory

    def admin_totp_secret(self) -> str:
        with self.session_factory() as session:
            user = (
                session.query(User).filter(User.email == "admin@example.com").one()
            )
            assert user.totp_secret_encrypted
            return decrypt(user.totp_secret_encrypted)

    def full_admin_headers(self) -> dict[str, str]:
        secret = self.admin_totp_secret()
        login = self.client.post(
            "/api/auth/login",
            json={"email": "admin@example.com", "password": PASSWORD},
        ).json()
        verify = self.client.post(
            "/api/auth/2fa/verify",
            json={
                "temp_token": login["access_token"],
                "code": pyotp.TOTP(secret).now(),
            },
        )
        assert verify.status_code == 200, verify.text
        return {"Authorization": f"Bearer {verify.json()['access_token']}"}


@pytest.fixture()
def stack() -> Generator[Stack, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with session_factory() as seed:
        seed.add(
            User(
                email="admin@example.com",
                full_name="Admin User",
                password_hash=hash_password(PASSWORD),
                role=UserRole.ADMIN,
                is_active=True,
                totp_secret_encrypted=encrypt(pyotp.random_base32()),
                totp_enabled=True,
                totp_confirmed_at=datetime.now(UTC),
            )
        )
        seed.add(
            User(
                email="admin-no-2fa@example.com",
                full_name="Admin Without 2FA",
                password_hash=hash_password(PASSWORD),
                role=UserRole.ADMIN,
                is_active=True,
            )
        )
        seed.add(
            User(
                email="viewer@example.com",
                full_name="Viewer User",
                password_hash=hash_password(PASSWORD),
                role=UserRole.VIEWER,
                is_active=True,
            )
        )
        seed.commit()

    def override_session() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as client:
        yield Stack(client, engine, session_factory)
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)


@pytest.fixture()
def client(stack: Stack) -> TestClient:
    return stack.client


def _login(client: TestClient, email: str, password: str = PASSWORD) -> dict:
    response = client.post(
        "/api/auth/login", json={"email": email, "password": password}
    )
    assert response.status_code == 200, response.text
    return response.json()


# ----- login flow -----------------------------------------------------------


def test_login_with_totp_returns_temp_token_and_requires_2fa(client: TestClient):
    body = _login(client, "admin@example.com")
    assert body["requires_2fa"] is True
    assert body["access_token"]
    assert body.get("limited") is False


def test_login_without_totp_for_admin_is_limited(client: TestClient):
    body = _login(client, "admin-no-2fa@example.com")
    assert body.get("requires_2fa") is False
    assert body["limited"] is True
    assert body["access_token"]


def test_login_without_totp_for_non_admin_is_unlimited(client: TestClient):
    body = _login(client, "viewer@example.com")
    assert body.get("requires_2fa") is False
    assert body.get("limited") is False


def test_2fa_verify_with_correct_code_returns_final_token(stack: Stack):
    secret = stack.admin_totp_secret()
    login_body = _login(stack.client, "admin@example.com")
    response = stack.client.post(
        "/api/auth/2fa/verify",
        json={"temp_token": login_body["access_token"], "code": pyotp.TOTP(secret).now()},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["access_token"]
    assert body.get("requires_2fa") is False
    assert body.get("limited") is False


def test_2fa_verify_with_wrong_code_fails(client: TestClient):
    login_body = _login(client, "admin@example.com")
    response = client.post(
        "/api/auth/2fa/verify",
        json={"temp_token": login_body["access_token"], "code": "000000"},
    )
    assert response.status_code == 401


def test_2fa_verify_rejects_full_access_token(stack: Stack):
    """A full access token must NOT be usable as a pre-2FA temp token."""
    secret = stack.admin_totp_secret()
    login_body = _login(stack.client, "admin@example.com")
    verify = stack.client.post(
        "/api/auth/2fa/verify",
        json={
            "temp_token": login_body["access_token"],
            "code": pyotp.TOTP(secret).now(),
        },
    )
    full_token = verify.json()["access_token"]
    response = stack.client.post(
        "/api/auth/2fa/verify",
        json={"temp_token": full_token, "code": pyotp.TOTP(secret).now()},
    )
    assert response.status_code == 401


# ----- backup codes ---------------------------------------------------------


def test_backup_code_works_once(stack: Stack):
    """End-to-end: fully-verified admin creates a user; that user enrolls
    2FA and gets backup codes. A backup code logs them in exactly once, a
    second use of the same code fails, but a different unused code still
    works."""
    fresh_email = "fresh@example.com"
    create = stack.client.post(
        "/api/users",
        json={
            "email": fresh_email,
            "full_name": "Fresh User",
            "password": STRONG_PASSWORD,
            "role": "viewer",
        },
        headers=stack.full_admin_headers(),
    )
    assert create.status_code == 201, create.text

    fresh_login = _login(stack.client, fresh_email, password=STRONG_PASSWORD)
    fresh_bearer = {"Authorization": f"Bearer {fresh_login['access_token']}"}

    setup = stack.client.post("/api/auth/2fa/setup", headers=fresh_bearer)
    assert setup.status_code == 200
    secret = setup.json()["secret"]
    confirm = stack.client.post(
        "/api/auth/2fa/confirm",
        json={"code": pyotp.TOTP(secret).now()},
        headers=fresh_bearer,
    )
    assert confirm.status_code == 200
    backup_codes = confirm.json()["backup_codes"]
    assert len(backup_codes) == 8

    # First use of backup code → success.
    second_login = _login(stack.client, fresh_email, password=STRONG_PASSWORD)
    first_use = stack.client.post(
        "/api/auth/2fa/verify",
        json={"temp_token": second_login["access_token"], "code": backup_codes[0]},
    )
    assert first_use.status_code == 200

    # Same backup code reused → 401.
    third_login = _login(stack.client, fresh_email, password=STRONG_PASSWORD)
    reused = stack.client.post(
        "/api/auth/2fa/verify",
        json={"temp_token": third_login["access_token"], "code": backup_codes[0]},
    )
    assert reused.status_code == 401

    # A different unused backup code still works.
    fourth_login = _login(stack.client, fresh_email, password=STRONG_PASSWORD)
    other = stack.client.post(
        "/api/auth/2fa/verify",
        json={"temp_token": fourth_login["access_token"], "code": backup_codes[1]},
    )
    assert other.status_code == 200


# ----- limited-admin gate ---------------------------------------------------


def test_admin_without_2fa_can_see_dashboard_but_not_users(client: TestClient):
    body = _login(client, "admin-no-2fa@example.com")
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    me = client.get("/api/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["requires_2fa_setup"] is True

    assert client.get("/api/users", headers=headers).status_code == 403
    assert client.get("/api/audit-logs", headers=headers).status_code == 403


def test_admin_without_2fa_can_run_setup_and_confirm(client: TestClient):
    body = _login(client, "admin-no-2fa@example.com")
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    setup = client.post("/api/auth/2fa/setup", headers=headers)
    assert setup.status_code == 200
    secret = setup.json()["secret"]
    confirm = client.post(
        "/api/auth/2fa/confirm",
        json={"code": pyotp.TOTP(secret).now()},
        headers=headers,
    )
    assert confirm.status_code == 200
    assert len(confirm.json()["backup_codes"]) == 8

    # Re-login and verify → full session → /api/users now accessible.
    login_again = _login(client, "admin-no-2fa@example.com")
    verify = client.post(
        "/api/auth/2fa/verify",
        json={
            "temp_token": login_again["access_token"],
            "code": pyotp.TOTP(secret).now(),
        },
    )
    assert verify.status_code == 200
    full_headers = {"Authorization": f"Bearer {verify.json()['access_token']}"}
    assert client.get("/api/users", headers=full_headers).status_code == 200


# ----- /auth/me -------------------------------------------------------------


def test_me_reports_totp_enabled_and_requires_setup(client: TestClient):
    no_2fa = _login(client, "admin-no-2fa@example.com")
    me = client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {no_2fa['access_token']}"}
    )
    body = me.json()
    assert body["totp_enabled"] is False
    assert body["requires_2fa_setup"] is True

    viewer = _login(client, "viewer@example.com")
    me = client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {viewer['access_token']}"}
    )
    body = me.json()
    assert body["totp_enabled"] is False
    assert body["requires_2fa_setup"] is False


# ----- disable flow ---------------------------------------------------------


def test_disable_requires_correct_password(stack: Stack):
    headers = stack.full_admin_headers()
    bad = stack.client.post(
        "/api/auth/2fa/disable",
        json={"password": "WrongPassword!"},
        headers=headers,
    )
    assert bad.status_code == 401

    good = stack.client.post(
        "/api/auth/2fa/disable", json={"password": PASSWORD}, headers=headers
    )
    assert good.status_code == 200

    # After disabling, the next login no longer requires 2FA, but the admin
    # JWT is `limited` until 2FA is re-enabled.
    body = _login(stack.client, "admin@example.com")
    assert body.get("requires_2fa") is False
    assert body["limited"] is True


def test_setup_when_already_enabled_returns_conflict(stack: Stack):
    headers = stack.full_admin_headers()
    response = stack.client.post("/api/auth/2fa/setup", headers=headers)
    assert response.status_code == 409


# ----- pre_2fa token cannot access protected endpoints ----------------------


def test_pre_2fa_token_cannot_reach_protected_endpoints(client: TestClient):
    body = _login(client, "admin@example.com")
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    assert client.get("/api/auth/me", headers=headers).status_code == 401
    assert client.get("/api/contacts", headers=headers).status_code == 401
