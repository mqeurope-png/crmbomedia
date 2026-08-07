"""CRM-PERFIL — el perfil del comercial (rol ``user``) pasa a solo lectura
salvo la firma de email. El admin gana la capacidad de editar el perfil de
cualquier usuario y de resetear su contraseña. Se retira el auto-servicio
público de «olvidé contraseña».

Estos tests fijan el contrato de permisos: qué puede tocar el comercial de su
propio perfil, qué queda reservado al admin, que el reset genera una
contraseña válida devuelta UNA sola vez y queda registrado en auditoría, y que
los endpoints de propiedad de alias siguen siendo admin-only (regresión).
"""
from __future__ import annotations

import json
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.audit import Action
from app.core.passwords import validate_password_policy
from app.core.security import verify_password
from app.db.session import get_session
from app.main import app
from app.models.crm import AuditLog, Base, User
from tests._test_helpers import (
    DEFAULT_PASSWORD,
    auth_headers,
    seed_test_users,
)

STRONG_PASSWORD = "AdminSetPass123!"


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


def _user_id(session_factory: sessionmaker, email: str) -> str:
    with session_factory() as session:
        user = session.scalar(select(User).where(User.email == email))
        assert user is not None
        return user.id


# --------------------------------------------------------------------------
# Firma: editable por el propio comercial y por el admin (única excepción a la
# lectura-solo del perfil del comercial).
# --------------------------------------------------------------------------
def test_signature_editable_by_self_and_admin(client: TestClient) -> None:
    # El comercial crea y edita SU firma.
    user_headers = auth_headers(client, "user")
    created = client.post(
        "/api/email-signatures",
        json={
            "name": "Comercial",
            "html_content": "<p>Saludos</p>",
            "is_default": True,
            "sort_order": 0,
        },
        headers=user_headers,
    )
    assert created.status_code == 201, created.text
    sig_id = created.json()["id"]

    edited = client.put(
        f"/api/email-signatures/{sig_id}",
        json={
            "name": "Comercial",
            "html_content": "<p>Un saludo cordial</p>",
            "is_default": True,
            "sort_order": 0,
        },
        headers=user_headers,
    )
    assert edited.status_code == 200, edited.text
    assert "cordial" in edited.json()["html_content"]

    # El admin también gestiona su propia firma.
    admin_headers = auth_headers(client, "admin")
    admin_sig = client.post(
        "/api/email-signatures",
        json={
            "name": "Dirección",
            "html_content": "<p>Bart</p>",
            "is_default": True,
            "sort_order": 0,
        },
        headers=admin_headers,
    )
    assert admin_sig.status_code == 201, admin_sig.text


# --------------------------------------------------------------------------
# El resto de campos del perfil del comercial son de solo lectura: preferencias
# de envío, calendario y carpeta de plantillas por defecto → 403 requires_admin.
# --------------------------------------------------------------------------
def test_other_profile_fields_not_editable_by_sales(client: TestClient) -> None:
    headers = auth_headers(client, "user")

    prefs = client.put(
        "/api/users/me/preferences",
        json={"email_include_unsubscribe_default": True},
        headers=headers,
    )
    assert prefs.status_code == 403
    assert prefs.json()["detail"]["code"] == "requires_admin"

    calendar = client.patch(
        "/api/integrations/google/calendar",
        json={"calendar_id": "primary"},
        headers=headers,
    )
    assert calendar.status_code == 403
    assert calendar.json()["detail"]["code"] == "requires_admin"

    folder = client.put(
        "/api/users/me/default-template-folder",
        json={"folder_id": None},
        headers=headers,
    )
    assert folder.status_code == 403
    assert folder.json()["detail"]["code"] == "requires_admin"


def test_change_password_returns_403_for_sales(client: TestClient) -> None:
    denied = client.post(
        "/api/auth/change-password",
        json={
            "current_password": DEFAULT_PASSWORD,
            "new_password": STRONG_PASSWORD,
        },
        headers=auth_headers(client, "user"),
    )
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "requires_admin"


# --------------------------------------------------------------------------
# El admin puede editar el perfil de cualquier usuario.
# --------------------------------------------------------------------------
def test_admin_can_edit_any_users_profile(
    client: TestClient, session_factory: sessionmaker
) -> None:
    admin_headers = auth_headers(client, "admin")
    target_id = _user_id(session_factory, "user@example.com")

    updated = client.patch(
        f"/api/users/{target_id}",
        json={"full_name": "Comercial Renombrado", "role": "manager"},
        headers=admin_headers,
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["full_name"] == "Comercial Renombrado"
    assert body["role"] == "manager"


# --------------------------------------------------------------------------
# Reset de contraseña por admin: devuelve una contraseña nueva UNA sola vez,
# válida según la política, y deja de funcionar la anterior.
# --------------------------------------------------------------------------
def test_admin_reset_password_returns_new_password_once(
    client: TestClient, session_factory: sessionmaker
) -> None:
    admin_headers = auth_headers(client, "admin")
    target_id = _user_id(session_factory, "viewer@example.com")

    reset = client.post(
        f"/api/users/{target_id}/reset-password", headers=admin_headers
    )
    assert reset.status_code == 200, reset.text
    payload = reset.json()
    new_password = payload["password"]
    assert payload["message"]

    # La contraseña generada cumple la política (no lanza).
    validate_password_policy(new_password)

    # Persistida como hash (no en claro) y verificable.
    with session_factory() as session:
        user = session.get(User, target_id)
        assert user is not None
        assert user.password_hash != new_password
        assert verify_password(new_password, user.password_hash)

    # Login con la nueva contraseña funciona; con la vieja ya no.
    ok = client.post(
        "/api/auth/login",
        json={"email": "viewer@example.com", "password": new_password},
    )
    assert ok.status_code == 200, ok.text

    old = client.post(
        "/api/auth/login",
        json={"email": "viewer@example.com", "password": DEFAULT_PASSWORD},
    )
    assert old.status_code == 401


def test_admin_reset_password_logged_in_audit_logs(
    client: TestClient, session_factory: sessionmaker
) -> None:
    admin_headers = auth_headers(client, "admin")
    target_id = _user_id(session_factory, "viewer@example.com")

    reset = client.post(
        f"/api/users/{target_id}/reset-password", headers=admin_headers
    )
    assert reset.status_code == 200, reset.text

    with session_factory() as session:
        row = session.scalar(
            select(AuditLog)
            .where(AuditLog.action == Action.USER_PASSWORD_SET_BY_ADMIN)
            .where(AuditLog.target_id == target_id)
        )
        assert row is not None
        assert row.target_type == "user"
        metadata = json.loads(row.metadata_json or "{}")
        assert metadata.get("method") == "admin_reset_generated"
        assert metadata.get("target_email") == "viewer@example.com"


def test_reset_password_requires_admin(
    client: TestClient, session_factory: sessionmaker
) -> None:
    # Un comercial NO puede resetear la contraseña de nadie.
    target_id = _user_id(session_factory, "viewer@example.com")
    denied = client.post(
        f"/api/users/{target_id}/reset-password",
        headers=auth_headers(client, "user"),
    )
    assert denied.status_code == 403


# --------------------------------------------------------------------------
# Flujo público «olvidé contraseña» retirado: ambos endpoints responden 403 con
# code=password_reset_disabled y registran el intento.
# --------------------------------------------------------------------------
def test_forgot_password_endpoints_disabled(
    client: TestClient, session_factory: sessionmaker
) -> None:
    requested = client.post(
        "/api/auth/password-reset/request",
        json={"email": "viewer@example.com"},
    )
    assert requested.status_code == 403
    assert requested.json()["detail"]["code"] == "password_reset_disabled"

    confirmed = client.post(
        "/api/auth/password-reset/confirm",
        json={"token": "x" * 16, "new_password": "ResetPass123!Z"},
    )
    assert confirmed.status_code == 403
    assert confirmed.json()["detail"]["code"] == "password_reset_disabled"

    # El intento queda auditado para poder detectar abuso.
    with session_factory() as session:
        row = session.scalar(
            select(AuditLog).where(
                AuditLog.action == Action.AUTH_PASSWORD_RESET_REQUESTED
            )
        )
        assert row is not None


# --------------------------------------------------------------------------
# Barrido: todos los endpoints reservados al admin responden 403 requires_admin
# para el comercial.
# --------------------------------------------------------------------------
def test_admin_only_endpoints_403_for_sales_role(client: TestClient) -> None:
    headers = auth_headers(client, "user")
    cases = [
        ("post", "/api/auth/change-password", {
            "current_password": DEFAULT_PASSWORD,
            "new_password": STRONG_PASSWORD,
        }),
        ("put", "/api/users/me/preferences", {
            "email_include_unsubscribe_default": False,
        }),
        ("patch", "/api/integrations/google/calendar", {
            "calendar_id": "primary",
        }),
        ("put", "/api/users/me/default-template-folder", {"folder_id": None}),
    ]
    for method, path, body in cases:
        response = getattr(client, method)(path, json=body, headers=headers)
        assert response.status_code == 403, f"{method} {path} → {response.status_code}"
        assert response.json()["detail"]["code"] == "requires_admin", (
            f"{method} {path} sin code requires_admin"
        )


# --------------------------------------------------------------------------
# Regresión: la propiedad de alias entrante sigue siendo admin-only. El
# comercial solo puede LEER sus alias, no crear/editar/borrar.
# --------------------------------------------------------------------------
def test_alias_endpoints_still_admin_only(
    client: TestClient, session_factory: sessionmaker
) -> None:
    admin_headers = auth_headers(client, "admin")
    user_headers = auth_headers(client, "user")
    target_id = _user_id(session_factory, "user@example.com")

    # El comercial puede ver SUS alias (lista vacía).
    own = client.get(f"/api/users/{target_id}/aliases", headers=user_headers)
    assert own.status_code == 200
    assert own.json() == []

    # Pero no crear.
    created_denied = client.post(
        f"/api/users/{target_id}/aliases",
        json={"alias_email": "ventas@bomedia.net"},
        headers=user_headers,
    )
    assert created_denied.status_code == 403

    # El admin sí crea.
    created = client.post(
        f"/api/users/{target_id}/aliases",
        json={"alias_email": "ventas@bomedia.net"},
        headers=admin_headers,
    )
    assert created.status_code == 201, created.text
    alias_id = created.json()["id"]

    # El comercial no puede togglear ni borrar.
    patched_denied = client.patch(
        f"/api/users/{target_id}/aliases/{alias_id}",
        json={"active": False},
        headers=user_headers,
    )
    assert patched_denied.status_code == 403

    deleted_denied = client.delete(
        f"/api/users/{target_id}/aliases/{alias_id}",
        headers=user_headers,
    )
    assert deleted_denied.status_code == 403
