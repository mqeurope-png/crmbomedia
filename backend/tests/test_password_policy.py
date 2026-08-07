"""Tests for the password policy and the hardened password-reset flow."""
from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.passwords import (
    MIN_LENGTH,
    PasswordPolicyError,
    is_common_password,
    validate_password_policy,
)
from app.db.session import get_session
from app.main import app
from app.models.crm import Base
from tests._test_helpers import auth_headers as login
from tests._test_helpers import seed_test_users

VALID_PASSWORD = "ValidPass123!Strong"


@pytest.fixture()
def client() -> Generator[TestClient, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with testing_session() as seed:
        seed_test_users(seed)

    def override_session() -> Generator[Session, None, None]:
        with testing_session() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)


# -------- Pure policy unit tests ---------------------------------------------


def test_policy_accepts_compliant_password():
    validate_password_policy(VALID_PASSWORD)  # does not raise


def test_policy_rejects_short_password():
    with pytest.raises(PasswordPolicyError, match=str(MIN_LENGTH)):
        validate_password_policy("Abc123!")


def test_policy_rejects_missing_uppercase():
    with pytest.raises(PasswordPolicyError, match="mayúscula"):
        validate_password_policy("alllowercase123")


def test_policy_rejects_missing_lowercase():
    with pytest.raises(PasswordPolicyError, match="minúscula"):
        validate_password_policy("ALLUPPERCASE123")


def test_policy_rejects_missing_digit():
    with pytest.raises(PasswordPolicyError, match="número"):
        validate_password_policy("AllLettersNoDigits")


def test_policy_rejects_common_password_even_if_complex():
    # "Password1234" satisfies length + uppercase + lowercase + digit, but it is
    # in common_passwords.txt and must be rejected on the blocklist rule.
    with pytest.raises(PasswordPolicyError, match="listas públicas"):
        validate_password_policy("Password1234")


def test_is_common_password_helper_is_case_insensitive():
    assert is_common_password("password")
    assert is_common_password("Password")
    assert not is_common_password(VALID_PASSWORD)


# -------- Endpoint integration tests -----------------------------------------


def test_create_user_rejects_weak_password(client: TestClient):
    response = client.post(
        "/api/users",
        json={
            "email": "weak@example.com",
            "full_name": "Weak User",
            "password": "short1A",
            "role": "viewer",
        },
        headers=login(client, "admin"),
    )
    assert response.status_code == 422
    assert any(
        str(MIN_LENGTH) in str(err.get("msg", "")) for err in response.json()["detail"]
    )


def test_create_user_rejects_common_password(client: TestClient):
    response = client.post(
        "/api/users",
        json={
            "email": "common@example.com",
            "full_name": "Common Password",
            "password": "Password1234",
            "role": "viewer",
        },
        headers=login(client, "admin"),
    )
    assert response.status_code == 422
    body_text = response.text.lower()
    assert any(needle in body_text for needle in ("listas", "común", "comun", "comunes"))


def test_change_password_rejects_no_uppercase(client: TestClient):
    # CRM-PERFIL — change-password ahora es admin-only; la política sigue
    # aplicándose (422) al `new_password` débil.
    headers = login(client, "admin")
    response = client.post(
        "/api/auth/change-password",
        json={"current_password": "password123", "new_password": "alllowercase123"},
        headers=headers,
    )
    assert response.status_code == 422
    assert any(
        "mayúscula" in str(err.get("msg", "")) for err in response.json()["detail"]
    )


def test_admin_password_update_rejects_short(client: TestClient):
    headers = login(client, "admin")
    user_id = client.get("/api/users", headers=headers).json()[0]["id"]

    response = client.patch(
        f"/api/users/{user_id}/password",
        json={"new_password": "Aa1!short"},
        headers=headers,
    )
    assert response.status_code == 422


# -------- CRM-PERFIL: flujo público «olvidé contraseña» RETIRADO -------------


def test_password_reset_request_is_disabled(client: TestClient):
    response = client.post(
        "/api/auth/password-reset/request", json={"email": "viewer@example.com"}
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "password_reset_disabled"


def test_password_reset_confirm_is_disabled(client: TestClient):
    response = client.post(
        "/api/auth/password-reset/confirm",
        json={"token": "x" * 16, "new_password": VALID_PASSWORD},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "password_reset_disabled"


