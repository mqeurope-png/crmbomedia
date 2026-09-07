"""ERP-F2 — ámbito por rol en el backend.

Cierra el agujero de permisos detectado en el discovery: como PEDIDOS/SAT
están bajos en la escalera lineal de roles, HOY pasan los guards
`require_viewer`/`require_user` y alcanzan todo el CRM (contactos, emails,
marketing, pipelines…). Estos tests exigen 403 para el perfil solo-ERP en la
gestión del CRM, y confirman que el dato de CLIENTE que el ERP necesita
(empresas) y los propios endpoints del ERP siguen funcionando.
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


#: Endpoints de GESTIÓN del CRM (GET de solo lectura) que un perfil solo-ERP
#: NO debe poder alcanzar. Cubren monolito (contactos/pipelines/segmentos/
#: tags/marketing) y routers independientes (emails/workflows).
CRM_GET_ENDPOINTS = [
    "/api/contacts",
    "/api/emails/gmail-templates",
    "/api/brevo/lists",
    "/api/pipelines",
    "/api/segments",
    "/api/tags",
    "/api/workflows",
    "/api/entities",
    "/api/dashboard/pipeline-summary",
    "/api/tasks",
]


@pytest.mark.parametrize("role", ["pedidos", "sat"])
def test_erp_only_user_gets_403_on_crm_endpoints(http, role) -> None:
    headers = auth_headers(http, role)
    for path in CRM_GET_ENDPOINTS:
        r = http.get(path, headers=headers)
        assert r.status_code == 403, f"{role} alcanzó {path}: {r.status_code}"


def test_erp_only_user_gets_403_on_crm_writes(http) -> None:
    # No basta con bloquear las lecturas: una escritura del CRM también 403.
    headers = auth_headers(http, "pedidos")
    r = http.post("/api/contacts", headers=headers, json={
        "first_name": "X", "last_name": "Y", "email": "x@example.com",
    })
    assert r.status_code == 403
    r2 = http.post("/api/tags", headers=headers, json={"name": "hot"})
    assert r2.status_code == 403


def test_erp_only_user_can_read_client_data_needed_by_erp(http) -> None:
    # El dato de CLIENTE (empresas) que el ERP necesita para el pedido y para
    # enviar la factura SÍ está disponible para el perfil de ERP.
    headers = auth_headers(http, "pedidos")
    r = http.get("/api/companies", headers=headers)
    assert r.status_code == 200, r.text
    # Y los propios endpoints del ERP (bandeja de pedidos) siguen abiertos.
    r_orders = http.get("/api/erp/orders", headers=headers)
    assert r_orders.status_code == 200, r_orders.text


def test_sat_can_read_client_data_and_sat_queue(http) -> None:
    headers = auth_headers(http, "sat")
    assert http.get("/api/companies", headers=headers).status_code == 200
    # La cola SAT es su pantalla de trabajo — accesible.
    assert http.get("/api/erp/sat/queue", headers=headers).status_code == 200


def test_crm_only_user_gets_403_on_erp_endpoints(http) -> None:
    # Simétrico: un VIEWER (rol de solo-CRM sin vista ERP) recibe 403 en el
    # ERP. USER/MANAGER sí pueden VER el ERP (están en ERP_VIEW_ROLES), así
    # que el rol que queda fuera por completo es viewer.
    headers = auth_headers(http, "viewer")
    assert http.get("/api/erp/orders", headers=headers).status_code == 403


def test_admin_can_access_both(http) -> None:
    headers = auth_headers(http, "admin")
    # CRM
    for path in ["/api/contacts", "/api/pipelines", "/api/workflows"]:
        assert http.get(path, headers=headers).status_code == 200, path
    # ERP
    assert http.get("/api/erp/orders", headers=headers).status_code == 200
    # y el dato de cliente
    assert http.get("/api/companies", headers=headers).status_code == 200


def test_crm_scope_guard_is_optional_bearer(http) -> None:
    # El guard de ámbito NO fuerza autenticación: sin token, un endpoint del
    # CRM responde 401 por su propio guard (no 403 del ámbito), y los endpoints
    # PÚBLICOS (pixel de tracking, adjunto CID) siguen abiertos. Esto es lo que
    # evita romper los públicos al colgar `require_crm_access` de su router.
    r = http.get("/api/workflows")  # sin cabecera Authorization
    assert r.status_code == 401


def test_self_account_endpoints_stay_open_for_erp_user(http) -> None:
    # El perfil de ERP debe poder gestionar su propia cuenta (getCurrentUser,
    # alias de envío para la factura) — no queda encerrado fuera de /api/auth
    # ni de sus preferencias/alias.
    headers = auth_headers(http, "pedidos")
    me = http.get("/api/auth/me", headers=headers)
    assert me.status_code == 200, me.text
    assert me.json()["role"] == "pedidos"
