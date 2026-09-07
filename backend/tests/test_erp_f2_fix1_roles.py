"""ERP-F2-fix1 — los roles de ERP (pedidos/sat) ya se pueden ASIGNAR.

El backend acepta esos valores desde la Fase A, pero nunca se expusieron en la
UI, así que nadie los tenía. Aquí se verifica el guardado de punta a punta: un
admin los asigna, el usuario recién marcado entra en ámbito ERP (403 en el CRM,
200 en el ERP), y un admin no puede quitarse a sí mismo el admin (aún más
crítico ahora: demotarse a un rol de ERP lo encerraría en el modo ERP).
"""
from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401 — registra los modelos
from app.db.base import Base
from app.db.session import get_session
from app.main import app
from tests._test_helpers import auth_headers, seed_test_users

_PW = "NewUserPass123!"


@pytest.fixture()
def session_factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as seed:
        seed_test_users(seed)
    yield factory
    Base.metadata.drop_all(engine)


@pytest.fixture()
def http(session_factory) -> Generator[TestClient, None, None]:
    def override() -> Generator[Session, None, None]:
        with session_factory() as s:
            yield s
    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_admin_can_assign_erp_roles(http) -> None:
    admin = auth_headers(http, "admin")
    # Crear con «pedidos».
    created = http.post("/api/users", headers=admin, json={
        "email": "operativo@example.com", "full_name": "Operativo",
        "password": _PW, "role": "pedidos",
    })
    assert created.status_code == 201, created.text
    assert created.json()["role"] == "pedidos"
    uid = created.json()["id"]
    # Cambiar a «sat» persiste.
    upd = http.patch(f"/api/users/{uid}", headers=admin, json={"role": "sat"})
    assert upd.status_code == 200, upd.text
    assert upd.json()["role"] == "sat"
    # Y se ve reflejado en el listado.
    listed = http.get("/api/users", headers=admin).json()
    assert next(u for u in listed if u["id"] == uid)["role"] == "sat"


def test_erp_role_user_gets_erp_scope_after_assignment(http) -> None:
    admin = auth_headers(http, "admin")
    users = http.get("/api/users", headers=admin).json()
    target = next(u for u in users if u["email"] == "user@example.com")
    r = http.patch(f"/api/users/{target['id']}", headers=admin,
                   json={"role": "pedidos"})
    assert r.status_code == 200 and r.json()["role"] == "pedidos"
    # Ese usuario, al entrar, tiene ámbito ERP: 403 en el CRM, 200 en el ERP.
    hdr = auth_headers(http, "user")  # user@example.com, ahora pedidos
    assert http.get("/api/contacts", headers=hdr).status_code == 403
    assert http.get("/api/emails/gmail-templates", headers=hdr).status_code == 403
    assert http.get("/api/erp/orders", headers=hdr).status_code == 200
    # Y sigue leyendo el dato de cliente (empresas) que el ERP necesita.
    assert http.get("/api/companies", headers=hdr).status_code == 200


def test_admin_cannot_self_demote(http) -> None:
    admin = auth_headers(http, "admin")
    me = http.get("/api/auth/me", headers=admin).json()
    # Quitarse el admin (a cualquier rol) → 400.
    for role in ("pedidos", "sat", "user", "viewer"):
        r = http.patch(f"/api/users/{me['id']}", headers=admin,
                       json={"role": role})
        assert r.status_code == 400, f"{role}: {r.status_code}"
        assert r.json()["detail"]["code"] == "cannot_self_demote"
    # Cambiar SOLO el nombre (sin rol) sigue permitido.
    ok = http.patch(f"/api/users/{me['id']}", headers=admin,
                    json={"full_name": "Admin Renombrado"})
    assert ok.status_code == 200
    # Reafirmarse admin (mismo rol) no se bloquea.
    same = http.patch(f"/api/users/{me['id']}", headers=admin,
                      json={"role": "admin"})
    assert same.status_code == 200
    # Y cambiar el rol de OTRO usuario sí funciona.
    other = next(u for u in http.get("/api/users", headers=admin).json()
                 if u["email"] == "manager@example.com")
    r2 = http.patch(f"/api/users/{other['id']}", headers=admin,
                    json={"role": "pedidos"})
    assert r2.status_code == 200 and r2.json()["role"] == "pedidos"
