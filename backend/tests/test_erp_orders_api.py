"""BoHub ERP Fase A PR 3 — API de pedidos: CRUD, Cola PEDIDOS, transiciones,
timeline y matriz de permisos por rol.
"""
from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.base import Base
from app.db.session import get_session
from app.erp.models import ErpException, ExceptionType, Order
from app.main import app
from app.models.crm import Company
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
def client(session_factory) -> Generator[TestClient, None, None]:
    def override() -> Generator[Session, None, None]:
        with session_factory() as s:
            yield s
    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _payload(**over) -> dict:
    base = {
        "order_number": "MAN-0001",
        "lines": [
            {"product_sku": "SKU-MBO-3050", "product_codart": "MBO3050",
             "description": "MBO 3050", "quantity": 1, "unit_price": 4500},
            {"product_sku": "SKU-ROT", "product_codart": "ROT01",
             "description": "Rotativo", "quantity": 2, "unit_price": 195},
        ],
    }
    base.update(over)
    return base


def _create(client, role="pedidos", **over) -> dict:
    r = client.post("/api/erp/orders", json=_payload(**over),
                    headers=auth_headers(client, role))
    assert r.status_code == 201, r.text
    return r.json()


def _fire(client, oid, domain, to, role="admin", **kw):
    return client.post(
        f"/api/erp/orders/{oid}/transitions",
        json={"domain": domain, "to_status": to, **kw},
        headers=auth_headers(client, role),
    )


# --- crear -------------------------------------------------------------------


def test_pedidos_creates_manual_order_with_computed_total(client):
    body = _create(client)
    assert body["external_source"] == "manual"
    assert body["total_amount"] == pytest.approx(4890.0)
    assert body["preparation_status"] == "pending_review"
    assert len(body["lines"]) == 2
    assert body["blockers"] == []  # todo mapeado, sin empresa


def test_duplicate_order_number_rejected_409(client):
    _create(client)
    r = client.post("/api/erp/orders", json=_payload(),
                    headers=auth_headers(client, "pedidos"))
    assert r.status_code == 409


def test_create_forbidden_for_sat_user_and_viewer(client):
    for role in ("sat", "user", "viewer"):
        r = client.post("/api/erp/orders", json=_payload(order_number=f"X-{role}"),
                        headers=auth_headers(client, role))
        assert r.status_code == 403, role


# --- bandeja + permisos de vista --------------------------------------------


def test_list_filters_by_preparation_status(client):
    _create(client)
    _create(client, order_number="MAN-0002")
    r = client.get("/api/erp/orders?preparation=pending_review",
                   headers=auth_headers(client, "user"))
    assert r.status_code == 200
    assert len(r.json()["items"]) == 2
    r2 = client.get("/api/erp/orders?preparation=packed",
                    headers=auth_headers(client, "user"))
    assert r2.json()["items"] == []


def test_viewer_cannot_access_erp(client):
    assert client.get("/api/erp/orders",
                      headers=auth_headers(client, "viewer")).status_code == 403


def test_sat_and_user_can_view_detail(client):
    oid = _create(client)["id"]
    for role in ("sat", "user", "manager"):
        r = client.get(f"/api/erp/orders/{oid}", headers=auth_headers(client, role))
        assert r.status_code == 200, role


# --- Cola PEDIDOS: bloqueos + aprobación ------------------------------------


def test_pending_approval_lists_blockers(client, session_factory):
    with session_factory() as s:
        company = Company(name="Sin Factusol SL")  # sin factusol_company_id
        s.add(company)
        s.commit()
        cid = company.id
    body = _create(client, order_number="MAN-B1", company_id=cid, lines=[
        {"product_sku": "SKU-NUEVO", "description": "Sin mapear",
         "quantity": 1, "unit_price": 100},
    ])
    with session_factory() as s:
        s.add(ErpException(type=ExceptionType.SAT_ISSUE, order_id=body["id"]))
        s.commit()
    r = client.get("/api/erp/orders/pending-approval",
                   headers=auth_headers(client, "pedidos"))
    assert r.status_code == 200
    item = next(i for i in r.json()["items"] if i["id"] == body["id"])
    codes = {b["code"] for b in item["blockers"]}
    assert codes == {"sku_unmapped", "company_missing_factusol", "open_exceptions"}


def test_approve_blocked_order_returns_409_with_blockers(client):
    body = _create(client, order_number="MAN-B2", lines=[
        {"product_sku": "SKU-NUEVO", "description": "Sin mapear",
         "quantity": 1, "unit_price": 100},
    ])
    r = client.post(f"/api/erp/orders/{body['id']}/approve",
                    headers=auth_headers(client, "pedidos"))
    assert r.status_code == 409
    assert any(
        b["code"] == "sku_unmapped" for b in r.json()["detail"]["blockers"]
    )


def test_approve_clean_order_sets_approved_and_moves_to_queue(
    client, session_factory
):
    body = _create(client)
    r = client.post(f"/api/erp/orders/{body['id']}/approve",
                    headers=auth_headers(client, "pedidos"))
    assert r.status_code == 200, r.text
    assert r.json()["preparation_status"] == "in_queue"
    assert r.json()["approved_at"] is not None
    with session_factory() as s:
        o = s.scalar(select(Order).where(Order.id == body["id"]))
        assert o.approved_by_user_id is not None


def test_approve_forbidden_for_sat_manager_and_user(client):
    oid = _create(client)["id"]
    for role in ("sat", "manager", "user"):
        r = client.post(f"/api/erp/orders/{oid}/approve",
                        headers=auth_headers(client, role))
        assert r.status_code == 403, role


# --- transiciones vía API ----------------------------------------------------


def test_transition_endpoint_maps_engine_errors_to_http(client):
    oid = _create(client)["id"]
    # guard_failed → 409 (empezar preparación sin pago, tras aprobar).
    client.post(f"/api/erp/orders/{oid}/approve",
                headers=auth_headers(client, "pedidos"))
    r_guard = _fire(client, oid, "preparation", "preparing", role="sat")
    assert r_guard.status_code == 409
    assert r_guard.json()["detail"]["code"] == "guard_failed"
    # role_forbidden → 403 (user intenta cobrar... user no está en el arco).
    r_role = _fire(client, oid, "payment", "paid", role="user")
    assert r_role.status_code == 403
    # evidencia faltante → 422 (reembolso sin motivo).
    _fire(client, oid, "payment", "paid", role="manager")
    r_ev = _fire(client, oid, "payment", "refunded", role="admin")
    assert r_ev.status_code == 422
    # dominio inválido → 400.
    assert _fire(client, oid, "nope", "x").status_code == 400
    # arco inexistente → 409.
    r_arc = _fire(client, oid, "transport", "delivered", role="admin")
    assert r_arc.status_code == 409
    assert r_arc.json()["detail"]["code"] == "invalid_transition"


def test_full_flow_via_api_and_available_transitions(client):
    oid = _create(client)["id"]
    _fire(client, oid, "payment", "paid", role="manager")
    client.post(f"/api/erp/orders/{oid}/approve",
                headers=auth_headers(client, "pedidos"))
    _fire(client, oid, "preparation", "preparing", role="sat")
    detail = client.get(f"/api/erp/orders/{oid}",
                        headers=auth_headers(client, "sat")).json()
    # SAT desde preparing ve packed y blocked como siguientes pasos.
    prep_next = {t["to_status"] for t in detail["available_transitions"]["preparation"]}
    assert prep_next == {"packed", "blocked"}
    r = _fire(client, oid, "preparation", "packed", role="sat")
    assert r.status_code == 200
    assert r.json()["preparation_status"] == "packed"
    # El historial acumula las 4 transiciones.
    assert len(r.json()["status_history"]) == 4


# --- timeline ----------------------------------------------------------------


def test_timeline_unifies_status_and_exceptions_sorted_desc(
    client, session_factory
):
    oid = _create(client)["id"]
    _fire(client, oid, "payment", "paid", role="manager")
    with session_factory() as s:
        s.add(ErpException(
            type=ExceptionType.STOCK_SHORTAGE, subtype="eta_set", order_id=oid,
            metadata_json='{"eta_date": "2026-08-15"}',
        ))
        s.commit()
    r = client.get(f"/api/erp/orders/{oid}/timeline",
                   headers=auth_headers(client, "user"))
    assert r.status_code == 200
    body = r.json()
    types = [e["type"] for e in body["items"]]
    assert "status" in types and "exception" in types
    ats = [e["at"] for e in body["items"]]
    assert ats == sorted(ats, reverse=True)  # descendente
    # Filtro por tipo.
    only_exc = client.get(
        f"/api/erp/orders/{oid}/timeline?types=exception",
        headers=auth_headers(client, "user"),
    ).json()
    assert {e["type"] for e in only_exc["items"]} == {"exception"}


def test_timeline_404_for_missing_order(client):
    r = client.get("/api/erp/orders/nope/timeline",
                   headers=auth_headers(client, "user"))
    assert r.status_code == 404
