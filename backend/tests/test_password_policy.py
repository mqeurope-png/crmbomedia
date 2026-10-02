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
    policy_summary,
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
#
# La política es SOLO: mínimo 8 caracteres, una mayúscula y un número. Ni
# minúscula obligatoria ni lista de «contraseñas habituales».


def test_policy_accepts_compliant_password():
    validate_password_policy(VALID_PASSWORD)  # does not raise


@pytest.mark.parametrize("password", ["Abcdefg1", "PASSWORD1", "Password1", "ABCDEFG1"])
def test_policy_accepts_8_chars_with_uppercase_and_digit(password):
    """8 caracteres, mayúscula y número → válida. Sin minúsculas también, y
    «Password1» también (ya no hay lista de habituales)."""
    validate_password_policy(password)


def test_policy_rejects_short_password():
    with pytest.raises(PasswordPolicyError, match=str(MIN_LENGTH)):
        validate_password_policy("Abcdef1")                   # 7


def test_policy_rejects_missing_uppercase():
    with pytest.raises(PasswordPolicyError, match="mayúscula"):
        validate_password_policy("abcdefg1")


def test_policy_rejects_missing_digit():
    # 8 letras con mayúscula: pasa longitud y mayúscula, solo falta el número.
    with pytest.raises(PasswordPolicyError, match="número"):
        validate_password_policy("Abcdefgh")


def test_policy_summary_lists_exactly_the_three_rules():
    """Una sola fuente de verdad: los tres puntos que enseña el frontend."""
    assert policy_summary()["min_length"] == 8
    assert policy_summary()["rules"] == [
        "Mínimo 8 caracteres", "Al menos una letra mayúscula", "Al menos un número",
    ]


# -------- Endpoint integration tests -----------------------------------------


def test_create_user_rejects_weak_password(client: TestClient):
    response = client.post(
        "/api/users",
        json={
            "email": "weak@example.com",
            "full_name": "Weak User",
            "password": "Abcdef1",                               # 7
            "role": "viewer",
        },
        headers=login(client, "admin"),
    )
    assert response.status_code == 422
    assert any(
        str(MIN_LENGTH) in str(err.get("msg", "")) for err in response.json()["detail"]
    )


def test_create_user_accepts_8_chars_and_formerly_common_password(client: TestClient):
    """Alta por admin: «Password1» entra (8, mayúscula, número; sin lista de
    habituales) y ese usuario puede entrar con ella."""
    headers = login(client, "admin")
    response = client.post(
        "/api/users",
        json={"email": "ocho@example.com", "full_name": "Ocho Chars",
              "password": "Password1", "role": "viewer"},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    login_resp = client.post("/api/auth/login",
                             json={"email": "ocho@example.com", "password": "Password1"})
    assert login_resp.status_code == 200, login_resp.text


def test_change_password_rejects_no_uppercase(client: TestClient):
    # CRM-PERFIL — change-password ahora es admin-only; la política sigue
    # aplicándose (422) al `new_password` débil.
    headers = login(client, "admin")
    response = client.post(
        "/api/auth/change-password",
        json={"current_password": "password123", "new_password": "abcdefg1"},
        headers=headers,
    )
    assert response.status_code == 422
    assert any(
        "mayúscula" in str(err.get("msg", "")) for err in response.json()["detail"]
    )


def test_change_password_accepts_uppercase_only_letters(client: TestClient):
    """Cambio propio: «PASSWORD1» (sin minúscula) es válida ahora."""
    headers = login(client, "admin")
    response = client.post(
        "/api/auth/change-password",
        json={"current_password": "password123", "new_password": "PASSWORD1"},
        headers=headers,
    )
    assert response.status_code == 200, response.text


def test_admin_password_update_rejects_short_and_accepts_8(client: TestClient):
    """Mismo resultado en el cambio por admin: 7 → 422; 8 con mayúscula y número → 200."""
    headers = login(client, "admin")
    user_id = client.get("/api/users", headers=headers).json()[0]["id"]

    response = client.patch(
        f"/api/users/{user_id}/password",
        json={"new_password": "Abcdef1"},
        headers=headers,
    )
    assert response.status_code == 422
    response = client.patch(
        f"/api/users/{user_id}/password",
        json={"new_password": "Abcdefg1"},
        headers=headers,
    )
    assert response.status_code == 200, response.text


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


