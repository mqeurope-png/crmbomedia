"""BoHub ERP — roles y permisos (modelo de CAPACIDADES).

Cubre la autorización nueva por capacidad: el modelo (`app/erp/capabilities.py`),
su exposición en `/api/auth/me`, la asignación multi-rol en `/admin/users`, la
regla dura de los pedidos web para el Comercial, la Cola SAT (Comercial ve/sube
etiqueta pero no prepara; SAT/Pedidos preparan), el cobro bloqueado para
Comercial y SAT, y la atribución en la auditoría.
"""
from __future__ import annotations

import json
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401 — registra los modelos
from app.db.base import Base
from app.db.session import get_session
from app.erp.capabilities import (
    ALL_CAPS,
    ASSIGNABLE_ERP_ROLES,
    Cap,
    capabilities_for,
    effective_roles,
    has_capability,
)
from app.erp.models.orders import Order, OrderLine
from app.main import app
from app.models.crm import AuditLog, User, UserRole
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


def _mk_order(s: Session, *, number: str, source: str = "manual",
              prep: str = "pending_review", payment: str = "paid") -> str:
    o = Order(order_number=number, preparation_status=prep, payment_status=payment,
              transport_status="not_shipped", external_source=source)
    s.add(o)
    s.flush()
    s.add(OrderLine(order_id=o.id, product_sku="SKU-A", product_codart="A1",
                    description="Art A", quantity=1, unit_price=10, line_total=10))
    s.commit()
    return o.id


# --- modelo de capacidades (unidad) ------------------------------------------


class _U:
    """Usuario mínimo para los tests de unidad del modelo de capacidades."""

    def __init__(self, role: UserRole, erp_roles: list[str] | None = None):
        self.role = role
        self.erp_roles = json.dumps(erp_roles) if erp_roles else None


def test_admin_tiene_todas_las_capacidades():
    admin = _U(UserRole.ADMIN)
    assert capabilities_for(admin) == ALL_CAPS
    assert has_capability(admin, Cap.CONCILIACION)
    assert has_capability(admin, Cap.ROLES_ASSIGN)


def test_comercial_no_cobra_ni_ve_web_pero_trabaja_no_web():
    com = _U(UserRole.COMERCIAL)
    caps = capabilities_for(com)
    assert Cap.ORDERS_CREATE in caps and Cap.INVOICE_EMIT in caps
    assert Cap.SAT_SHIPPING in caps          # sube etiqueta
    assert Cap.COBRO_REGISTER not in caps    # NO cobra en FACTUSOL
    assert Cap.ORDERS_VIEW_WEB not in caps    # NO ve pedidos web
    assert Cap.SAT_PREPARE not in caps        # NO prepara/embala
    assert Cap.SEGUIMIENTO not in caps        # NO seguimiento
    assert Cap.CONCILIACION not in caps


def test_pedidos_cobra_y_ve_web_y_seguimiento_pero_no_concil():
    ped = _U(UserRole.PEDIDOS)
    caps = capabilities_for(ped)
    assert Cap.COBRO_REGISTER in caps
    assert Cap.ORDERS_VIEW_WEB in caps
    assert Cap.SAT_PREPARE in caps
    assert Cap.SEGUIMIENTO in caps
    assert Cap.CONCILIACION not in caps
    assert Cap.CONFIG not in caps


def test_sat_prepara_y_ve_web_pero_no_cobra_ni_factura():
    sat = _U(UserRole.SAT)
    caps = capabilities_for(sat)
    assert Cap.SAT_PREPARE in caps and Cap.SAT_SHIPPING in caps
    assert Cap.SAT_TRACKING in caps
    assert Cap.ORDERS_VIEW_WEB in caps        # ve pedidos web (solo lectura)
    assert Cap.EMAIL_SAT in caps
    assert Cap.COBRO_REGISTER not in caps
    assert Cap.INVOICE_EMIT not in caps
    assert Cap.ORDERS_CREATE not in caps


def test_viewer_no_tiene_capacidades_erp():
    assert capabilities_for(_U(UserRole.VIEWER)) == frozenset()


def test_multi_rol_es_union_de_capacidades():
    # Un comercial que además es SAT: prepara (SAT) y factura (comercial).
    both = _U(UserRole.COMERCIAL, erp_roles=["sat"])
    assert effective_roles(both) == {"comercial", "sat"}
    caps = capabilities_for(both)
    assert Cap.SAT_PREPARE in caps      # del rol sat
    assert Cap.INVOICE_EMIT in caps     # del rol comercial
    # Sigue sin cobrar: ninguno de los dos roles da COBRO_REGISTER.
    assert Cap.COBRO_REGISTER not in caps


# --- /api/auth/me ------------------------------------------------------------


def test_auth_me_expone_capacidades_y_erp_roles(http):
    r = http.get("/api/auth/me", headers=auth_headers(http, "comercial"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert Cap.INVOICE_EMIT in body["capabilities"]
    assert Cap.COBRO_REGISTER not in body["capabilities"]
    assert body["erp_roles"] == []


def test_auth_me_admin_trae_todas(http):
    r = http.get("/api/auth/me", headers=auth_headers(http, "admin"))
    assert set(r.json()["capabilities"]) == set(ALL_CAPS)


# --- asignar roles (solo admin) + multi-rol + auditoría ----------------------


def _user_id(session_factory, role: UserRole) -> str:
    with session_factory() as s:
        return s.scalar(select(User.id).where(User.role == role))


def test_admin_asigna_erp_roles_adicionales_con_auditoria(http, session_factory):
    uid = _user_id(session_factory, UserRole.COMERCIAL)
    r = http.patch(f"/api/users/{uid}", json={"erp_roles": ["sat"]},
                   headers=auth_headers(http, "admin"))
    assert r.status_code == 200, r.text
    assert r.json()["erp_roles"] == ["sat"]
    # Efecto: el /auth/me de ese usuario ahora suma las capacidades de SAT.
    me = http.get("/api/auth/me", headers=auth_headers(http, "comercial")).json()
    assert Cap.SAT_PREPARE in me["capabilities"]   # del rol sat añadido
    assert Cap.INVOICE_EMIT in me["capabilities"]  # del rol comercial principal
    # Auditoría: queda registrado quién dio el rol.
    with session_factory() as s:
        rows = s.scalars(select(AuditLog).where(
            AuditLog.action == "user.role_changed",
        )).all()
        assert any(
            json.loads(a.metadata_json or "{}").get("to_erp_roles") == ["sat"]
            for a in rows
        )


def test_erp_roles_rechaza_valores_no_asignables(http, session_factory):
    uid = _user_id(session_factory, UserRole.COMERCIAL)
    r = http.patch(f"/api/users/{uid}", json={"erp_roles": ["admin"]},
                   headers=auth_headers(http, "admin"))
    assert r.status_code == 422  # 'admin' no es un rol ERP asignable
    assert set(ASSIGNABLE_ERP_ROLES) == {"comercial", "pedidos", "sat"}


def test_no_admin_no_asigna_roles(http, session_factory):
    uid = _user_id(session_factory, UserRole.SAT)
    r = http.patch(f"/api/users/{uid}", json={"erp_roles": ["pedidos"]},
                   headers=auth_headers(http, "pedidos"))
    assert r.status_code == 403


# --- regla dura: el Comercial no ve ni toca pedidos web ----------------------


def test_comercial_no_ve_pedidos_web_en_la_bandeja(http, session_factory):
    with session_factory() as s:
        _mk_order(s, number="WEB-1", source="woocommerce")
        _mk_order(s, number="MAN-1", source="manual")
    r = http.get("/api/erp/orders", headers=auth_headers(http, "comercial"))
    assert r.status_code == 200, r.text
    nums = {o["order_number"] for o in r.json()["items"]}
    assert "MAN-1" in nums and "WEB-1" not in nums
    # Pedidos SÍ ve ambos.
    r2 = http.get("/api/erp/orders", headers=auth_headers(http, "pedidos"))
    nums2 = {o["order_number"] for o in r2.json()["items"]}
    assert {"MAN-1", "WEB-1"} <= nums2


def test_comercial_ficha_de_pedido_web_es_403(http, session_factory):
    with session_factory() as s:
        web = _mk_order(s, number="WEB-2", source="woocommerce")
        man = _mk_order(s, number="MAN-2", source="manual")
    com = auth_headers(http, "comercial")
    assert http.get(f"/api/erp/orders/{web}", headers=com).status_code == 403
    assert http.get(f"/api/erp/orders/{man}", headers=com).status_code == 200
    # SAT sí puede abrir la ficha web (solo lectura de cualquier pedido).
    assert http.get(f"/api/erp/orders/{web}",
                    headers=auth_headers(http, "sat")).status_code == 200


# --- cobro bloqueado para Comercial y SAT ------------------------------------


def test_registrar_cobro_bloqueado_para_comercial_y_sat(http):
    # El guard de capacidad (`erp.cobro.register`) corta ANTES de mirar la
    # factura: con un cuerpo válido, Comercial y SAT reciben 403; Pedidos pasa
    # el guard (y ya falla por la factura inexistente, no por permisos).
    payload = {"confirm": True, "cuenta": "6", "fecha": "2026-09-01"}
    for role in ("comercial", "sat"):
        r = http.post("/api/erp/factusol/documents/facturas/1/123/collection",
                      json=payload, headers=auth_headers(http, role))
        assert r.status_code == 403, f"{role} no debería cobrar: {r.text}"
    ped = http.post("/api/erp/factusol/documents/facturas/1/123/collection",
                    json=payload, headers=auth_headers(http, "pedidos"))
    assert ped.status_code != 403, ped.text  # pasa el guard de permisos


# --- Cola SAT: Comercial ve + sube etiqueta; no prepara ----------------------


def test_cola_sat_comercial_ve_pero_no_prepara(http, session_factory):
    with session_factory() as s:
        com_oid = _mk_order(s, number="SAT-C", prep="in_queue")
        sat_oid = _mk_order(s, number="SAT-S", prep="in_queue")
        ped_oid = _mk_order(s, number="SAT-P", prep="in_queue")
    com = auth_headers(http, "comercial")
    # Ver la cola: sí.
    assert http.get("/api/erp/sat/queue", headers=com).status_code == 200
    # Reportar excepción (trabajo de taller): no.
    r = http.post(f"/api/erp/orders/{com_oid}/report-exception",
                  json={"type": "sat_issue"}, headers=com)
    assert r.status_code == 403
    # SAT y Pedidos sí reportan (cada uno en su pedido, para no interferir).
    for role, oid in (("sat", sat_oid), ("pedidos", ped_oid)):
        rr = http.post(f"/api/erp/orders/{oid}/report-exception",
                       json={"type": "sat_issue"}, headers=auth_headers(http, role))
        assert rr.status_code in (201, 200), f"{role}: {rr.text}"


def test_cola_sat_enqueue_a_mano_es_de_oficina_no_de_sat(http, session_factory):
    with session_factory() as s:
        oid = _mk_order(s, number="ENQ-1", prep="packed")
    # SAT no añade a la cola a mano (es aprobar/gestión de oficina).
    assert http.post(f"/api/erp/orders/{oid}/sat-enqueue",
                     headers=auth_headers(http, "sat")).status_code == 403
    # Comercial (oficina) sí.
    assert http.post(f"/api/erp/orders/{oid}/sat-enqueue",
                     headers=auth_headers(http, "comercial")).status_code == 200


# --- auditoría con atribución -------------------------------------------------


def test_aprobar_pedido_deja_auditoria_con_actor(http, session_factory):
    with session_factory() as s:
        oid = _mk_order(s, number="AUD-1", prep="pending_review")
        actor_email = s.scalar(select(User.email).where(User.role == UserRole.COMERCIAL))
    r = http.post(f"/api/erp/orders/{oid}/approve", headers=auth_headers(http, "comercial"))
    assert r.status_code == 200, r.text
    with session_factory() as s:
        rows = s.scalars(select(AuditLog).where(
            AuditLog.target_type == "order", AuditLog.target_id == oid,
        )).all()
        assert rows, "la aprobación debe dejar rastro en la auditoría del pedido"
        assert any(a.actor_email == actor_email for a in rows)
