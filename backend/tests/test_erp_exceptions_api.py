"""BoHub ERP Fase A PR 6 — bandeja de excepciones + settings ERP."""
from __future__ import annotations

import json
from collections.abc import Generator
from datetime import UTC, datetime, timedelta

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
from app.models.crm import User, UserRole
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


def _order(s: Session, number="MAN-0001") -> str:
    o = Order(order_number=number)
    s.add(o)
    s.flush()
    return o.id


def _exc(s: Session, oid: str, *, type_=ExceptionType.SAT_ISSUE, subtype=None,
         metadata=None, assigned=None) -> str:
    e = ErpException(
        type=type_, subtype=subtype, order_id=oid,
        assigned_to_user_id=assigned,
        metadata_json=json.dumps(metadata) if metadata else None,
    )
    s.add(e)
    s.flush()
    return e.id


# --- listado + filtros -------------------------------------------------------


def test_list_filters_by_type_status_and_assigned(client, session_factory):
    with session_factory() as s:
        oid = _order(s)
        uid = s.scalar(select(User.id).where(User.role == UserRole.PEDIDOS))
        _exc(s, oid, type_=ExceptionType.SAT_ISSUE)
        _exc(s, oid, type_=ExceptionType.CARRIER_INCIDENT, assigned=uid)
        _exc(s, oid, type_=ExceptionType.MATERIAL_DEFECTIVE)
        s.commit()
    r = client.get("/api/erp/exceptions?type=sat_issue",
                   headers=auth_headers(client, "pedidos"))
    assert r.status_code == 200
    assert {e["type"] for e in r.json()["items"]} == {"sat_issue"}
    # assigned=me para el user pedidos.
    mine = client.get("/api/erp/exceptions?assigned=me",
                      headers=auth_headers(client, "pedidos"))
    assert len(mine.json()["items"]) == 1
    assert mine.json()["items"][0]["type"] == "carrier_incident"


def test_view_allowed_broadly_but_edit_restricted(client, session_factory):
    with session_factory() as s:
        eid = _exc(s, _order(s))
        s.commit()
    # SAT/user pueden VER.
    assert client.get("/api/erp/exceptions",
                      headers=auth_headers(client, "sat")).status_code == 200
    # viewer no.
    assert client.get("/api/erp/exceptions",
                      headers=auth_headers(client, "viewer")).status_code == 403
    # SAT NO puede resolver (edit = admin/pedidos).
    r = client.post(f"/api/erp/exceptions/{eid}/resolve",
                    json={"resolution_note": "x"}, headers=auth_headers(client, "sat"))
    assert r.status_code == 403


# --- chip de alerta ETA ------------------------------------------------------


def test_eta_overdue_flag_true_when_eta_past_and_open(client, session_factory):
    past = (datetime.now(UTC) - timedelta(days=2)).date().isoformat()
    future = (datetime.now(UTC) + timedelta(days=5)).date().isoformat()
    with session_factory() as s:
        oid = _order(s)
        _exc(s, oid, type_=ExceptionType.STOCK_SHORTAGE, subtype="eta_set",
             metadata={"eta_date": past})
        _exc(s, oid, type_=ExceptionType.STOCK_SHORTAGE, subtype="eta_set",
             metadata={"eta_date": future})
        s.commit()
    items = client.get("/api/erp/exceptions",
                       headers=auth_headers(client, "pedidos")).json()["items"]
    by_eta = {e["eta_date"]: e["eta_overdue"] for e in items}
    assert by_eta[past] is True
    assert by_eta[future] is False


def test_eta_overdue_false_once_resolved(client, session_factory):
    past = (datetime.now(UTC) - timedelta(days=1)).date().isoformat()
    with session_factory() as s:
        oid = _order(s)
        eid = _exc(s, oid, type_=ExceptionType.STOCK_SHORTAGE, subtype="eta_set",
                   metadata={"eta_date": past})
        s.commit()
    client.post(f"/api/erp/exceptions/{eid}/resolve",
                json={"resolution_note": "repuesto recibido"},
                headers=auth_headers(client, "pedidos"))
    items = client.get("/api/erp/exceptions",
                       headers=auth_headers(client, "pedidos")).json()["items"]
    assert items[0]["eta_overdue"] is False


# --- acciones ----------------------------------------------------------------


def test_assign_resolve_and_status_actions(client, session_factory):
    with session_factory() as s:
        oid = _order(s)
        eid = _exc(s, oid)
        uid = s.scalar(select(User.id).where(User.role == UserRole.ADMIN))
        s.commit()
    # asignar
    a = client.post(f"/api/erp/exceptions/{eid}/assign",
                    json={"assigned_to_user_id": uid},
                    headers=auth_headers(client, "pedidos"))
    assert a.status_code == 200 and a.json()["assigned_to_user_id"] == uid
    # marcar como vista (in_progress)
    st = client.post(f"/api/erp/exceptions/{eid}/status",
                     json={"status": "in_progress"},
                     headers=auth_headers(client, "pedidos"))
    assert st.json()["status"] == "in_progress"
    # status=resolved por /status → 400 (usa /resolve)
    bad = client.post(f"/api/erp/exceptions/{eid}/status",
                      json={"status": "resolved"},
                      headers=auth_headers(client, "pedidos"))
    assert bad.status_code == 400
    # resolver con nota
    r = client.post(f"/api/erp/exceptions/{eid}/resolve",
                    json={"resolution_note": "hablado con proveedor"},
                    headers=auth_headers(client, "admin"))
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "resolved"
    assert body["resolution_note"] == "hablado con proveedor"
    assert body["resolved_at"] is not None
    with session_factory() as s:
        e = s.get(ErpException, eid)
        assert e.resolved_by_user_id is not None


def test_assign_rejects_unknown_user(client, session_factory):
    with session_factory() as s:
        eid = _exc(s, _order(s))
        s.commit()
    r = client.post(f"/api/erp/exceptions/{eid}/assign",
                    json={"assigned_to_user_id": "ghost"},
                    headers=auth_headers(client, "pedidos"))
    assert r.status_code == 400


# --- settings ----------------------------------------------------------------


def test_settings_get_creates_defaults_and_patch_admin_only(client):
    g = client.get("/api/erp/settings", headers=auth_headers(client, "pedidos"))
    assert g.status_code == 200
    assert g.json()["default_invoice_mode"] == "manual"
    # patch admin
    p = client.patch("/api/erp/settings",
                     json={"default_invoice_mode": "auto_under_max",
                           "auto_invoice_max_amount_eur": 500,
                           "factusol_default_ejercicio": "2026"},
                     headers=auth_headers(client, "admin"))
    assert p.status_code == 200
    assert p.json()["default_invoice_mode"] == "auto_under_max"
    assert p.json()["auto_invoice_max_amount_eur"] == 500
    # persiste
    g2 = client.get("/api/erp/settings", headers=auth_headers(client, "admin"))
    assert g2.json()["factusol_default_ejercicio"] == "2026"


def test_settings_patch_forbidden_for_non_admin(client):
    for role in ("pedidos", "manager", "sat", "user"):
        r = client.patch("/api/erp/settings",
                         json={"default_invoice_mode": "auto"},
                         headers=auth_headers(client, role))
        assert r.status_code == 403, role


def test_settings_patch_rejects_bad_invoice_mode(client):
    r = client.patch("/api/erp/settings", json={"default_invoice_mode": "nope"},
                     headers=auth_headers(client, "admin"))
    assert r.status_code == 400


def test_settings_factusol_live_defaults_false_and_toggles(client):
    """B-2-fix4: el gate FACTUSOL arranca en false y es editable por ADMIN."""
    g = client.get("/api/erp/settings", headers=auth_headers(client, "pedidos"))
    assert g.json()["factusol_live"] is False
    p = client.patch("/api/erp/settings", json={"factusol_live": True},
                     headers=auth_headers(client, "admin"))
    assert p.status_code == 200
    assert p.json()["factusol_live"] is True
    # persiste
    g2 = client.get("/api/erp/settings", headers=auth_headers(client, "admin"))
    assert g2.json()["factusol_live"] is True
