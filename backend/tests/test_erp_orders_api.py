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
from app.erp.models import (
    ERP_SETTINGS_SINGLETON_ID,
    ErpException,
    ErpSettings,
    ExceptionType,
    Order,
)
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


def _set_factusol_live(session_factory, live: bool) -> None:
    """B-2-fix4: activa/desactiva el gate FACTUSOL en el singleton settings."""
    with session_factory() as s:
        cfg = s.get(ErpSettings, ERP_SETTINGS_SINGLETON_ID)
        if cfg is None:
            cfg = ErpSettings(id=ERP_SETTINGS_SINGLETON_ID)
            s.add(cfg)
        cfg.factusol_live = live
        s.commit()


def test_pending_approval_splits_factusol_issues_as_warnings_when_not_live(
    client, session_factory
):
    """B-2-fix4: mientras FACTUSOL no esté live, sku_unmapped y
    company_missing_factusol son WARNINGS (no bloquean). Solo una excepción
    real abierta (sin code factusol-gated) bloquea la aprobación."""
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
    # La excepción SAT real (sin code) sigue bloqueando; las de FACTUSOL no.
    assert {b["code"] for b in item["blockers"]} == {"open_exceptions"}
    assert {w["code"] for w in item["warnings"]} == {
        "sku_unmapped", "company_missing_factusol",
    }


def test_pending_approval_factusol_issues_block_when_live(
    client, session_factory
):
    """Al activar factusol_live, los mismos issues pasan a BLOQUEANTES."""
    with session_factory() as s:
        company = Company(name="Sin Factusol SL")
        s.add(company)
        s.commit()
        cid = company.id
    body = _create(client, order_number="MAN-B1L", company_id=cid, lines=[
        {"product_sku": "SKU-NUEVO", "description": "Sin mapear",
         "quantity": 1, "unit_price": 100},
    ])
    _set_factusol_live(session_factory, True)
    r = client.get("/api/erp/orders/pending-approval",
                   headers=auth_headers(client, "pedidos"))
    item = next(i for i in r.json()["items"] if i["id"] == body["id"])
    assert {b["code"] for b in item["blockers"]} == {
        "sku_unmapped", "company_missing_factusol",
    }
    assert item["warnings"] == []


def test_approve_unmapped_sku_ok_when_not_live_but_409_when_live(
    client, session_factory
):
    """B-2-fix4: con FACTUSOL no live, un SKU sin mapear NO impide aprobar
    (es warning); al activar el gate, la misma aprobación devuelve 409."""
    body = _create(client, order_number="MAN-B2", lines=[
        {"product_sku": "SKU-NUEVO", "description": "Sin mapear",
         "quantity": 1, "unit_price": 100},
    ])
    ok = client.post(f"/api/erp/orders/{body['id']}/approve",
                     headers=auth_headers(client, "pedidos"))
    assert ok.status_code == 200, ok.text
    assert ok.json()["preparation_status"] == "in_queue"

    # Otro pedido idéntico, pero ahora con el gate activo → bloqueado.
    body2 = _create(client, order_number="MAN-B2L", lines=[
        {"product_sku": "SKU-NUEVO", "description": "Sin mapear",
         "quantity": 1, "unit_price": 100},
    ])
    _set_factusol_live(session_factory, True)
    r = client.post(f"/api/erp/orders/{body2['id']}/approve",
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


# --- procesado externamente (B-2-fix4) --------------------------------------


def test_mark_externally_processed_sets_terminal_states_and_stamp(
    client, session_factory
):
    body = _create(client, order_number="MAN-EXT1")
    r = client.post(
        f"/api/erp/orders/{body['id']}/mark-externally-processed",
        json={"note": "Gestionado en el Excel de siempre"},
        headers=auth_headers(client, "pedidos"),
    )
    assert r.status_code == 200, r.text
    detail = r.json()
    assert detail["preparation_status"] == "already_completed_externally"
    assert detail["transport_status"] == "already_shipped_externally"
    assert detail["invoice_status"] == "already_invoiced_externally"
    assert detail["payment_status"] == "paid"  # pending → paid al externalizar
    assert detail["externally_processed_at"] is not None
    assert detail["externally_processed_note"] == "Gestionado en el Excel de siempre"
    assert detail["externally_processed_by_user_id"] is not None
    # Historial: una fila por cada dominio cambiado (pago + 3 estados).
    with session_factory() as s:
        o = s.scalar(select(Order).where(Order.id == body["id"]))
        assert o.externally_processed_at is not None


def test_mark_externally_processed_is_idempotent(client):
    body = _create(client, order_number="MAN-EXT2")
    first = client.post(
        f"/api/erp/orders/{body['id']}/mark-externally-processed",
        json={}, headers=auth_headers(client, "pedidos"),
    ).json()
    second = client.post(
        f"/api/erp/orders/{body['id']}/mark-externally-processed",
        json={}, headers=auth_headers(client, "pedidos"),
    )
    assert second.status_code == 200
    # El timestamp no se re-estampa (no-op en la 2ª llamada).
    assert second.json()["externally_processed_at"] == first["externally_processed_at"]


def test_mark_externally_processed_forbidden_for_viewer_roles(client):
    oid = _create(client, order_number="MAN-EXT3")["id"]
    for role in ("sat", "user", "viewer"):
        r = client.post(
            f"/api/erp/orders/{oid}/mark-externally-processed",
            json={}, headers=auth_headers(client, role),
        )
        assert r.status_code == 403, role


def test_bulk_mark_externally_processed_by_ids(client):
    a = _create(client, order_number="MAN-BULK1")["id"]
    b = _create(client, order_number="MAN-BULK2")["id"]
    r = client.post(
        "/api/erp/orders/bulk-mark-externally-processed",
        json={"order_ids": [a, b], "note": "Migración inicial"},
        headers=auth_headers(client, "pedidos"),
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "marked": 2}
    # Re-run: ya externalizados → marked 0 (idempotente).
    again = client.post(
        "/api/erp/orders/bulk-mark-externally-processed",
        json={"order_ids": [a, b]},
        headers=auth_headers(client, "pedidos"),
    )
    assert again.json()["marked"] == 0


def test_bulk_mark_requires_ids_or_store(client):
    r = client.post(
        "/api/erp/orders/bulk-mark-externally-processed",
        json={}, headers=auth_headers(client, "pedidos"),
    )
    assert r.status_code == 400


def test_list_hides_externalized_by_default_and_shows_with_flag(client):
    oid = _create(client, order_number="MAN-EXT-HIDE")["id"]
    client.post(
        f"/api/erp/orders/{oid}/mark-externally-processed",
        json={}, headers=auth_headers(client, "pedidos"),
    )
    # Por defecto la bandeja no lo muestra.
    default = client.get("/api/erp/orders",
                         headers=auth_headers(client, "user")).json()
    assert oid not in {o["id"] for o in default["items"]}
    # Con show_external=true sí aparece.
    shown = client.get("/api/erp/orders?show_external=true",
                       headers=auth_headers(client, "user")).json()
    assert oid in {o["id"] for o in shown["items"]}
