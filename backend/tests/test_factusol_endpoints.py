"""BoHub ERP Fase C PR C-2 — endpoints de emisión FACTUSOL + vinculación.

La cola RQ y el cliente FACTUSOL se mockean — sin Redis ni red.
"""
from __future__ import annotations

from collections.abc import Generator
from unittest.mock import patch

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
    ErpSettings,
    InvoiceStatus,
    Order,
    OrderLine,
)
from app.main import app
from app.models.crm import AuditLog, Company
from tests._test_helpers import auth_headers, seed_test_users


class _FakeFactusol:
    """Cliente FACTUSOL mínimo para el endpoint de estado: sirve F_FAC (por
    REFFAC) y F_ALB (por REFALB) según lo configurado."""

    def __init__(self, *, fac=None, alb=None):
        self.default_ejercicio = "2026"
        self._fac = fac or []
        self._alb = alb or []

    def load_table(self, tabla, *, filtro="1=1", ejercicio=None):
        if tabla == "F_FAC" and "REFFAC" in filtro:
            return list(self._fac)
        if tabla == "F_ALB":
            return list(self._alb)
        return []


def _enable_factusol_live(s: Session) -> None:
    s.add(ErpSettings(id=ERP_SETTINGS_SINGLETON_ID, factusol_live=True))
    s.commit()


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


def _order(s: Session, *, invoice_status=None, with_company=True, codfac=None) -> str:
    company_id = None
    if with_company:
        c = Company(name="Acme SL", tax_id="B12345678", factusol_company_id="55555")
        s.add(c)
        s.commit()
        company_id = c.id
    o = Order(order_number="MAN-7001", company_id=company_id, total_amount=200)
    if invoice_status is not None:
        o.invoice_status = invoice_status
    if codfac is not None:
        o.factusol_invoice_number = codfac
    s.add(o)
    s.flush()
    s.add(OrderLine(order_id=o.id, position=0, product_sku="SKU-1",
                    product_codart="ART1", description="Art 1",
                    quantity=1, unit_price=200, tax_rate=21, line_total=200))
    s.commit()
    return o.id


# --- emitir factura ---------------------------------------------------------


def test_emit_invoice_endpoint_returns_202_and_audits(client, session_factory):
    with session_factory() as s:
        oid = _order(s)
    with patch("app.integrations.factusol.jobs.enqueue_emit_invoice",
               return_value="job-abc") as enq:
        r = client.post(f"/api/erp/orders/{oid}/emit-factusol-invoice",
                        headers=auth_headers(client, "pedidos"))
    assert r.status_code == 202, r.text
    body = r.json()
    assert body == {"job_id": "job-abc", "order_id": oid, "status": "queued"}
    enq.assert_called_once()
    with session_factory() as s:
        audits = list(s.scalars(select(AuditLog).where(
            AuditLog.action == "erp.factusol_invoice_requested")))
        assert len(audits) == 1


def test_emit_invoice_rejects_already_invoiced_by_erp(client, session_factory):
    with session_factory() as s:
        oid = _order(s, invoice_status=InvoiceStatus.INVOICED_BY_ERP.value,
                     codfac="526067")
    r = client.post(f"/api/erp/orders/{oid}/emit-factusol-invoice",
                    headers=auth_headers(client, "pedidos"))
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "already_invoiced_by_erp"


def test_emit_invoice_rejects_already_invoiced_externally(client, session_factory):
    with session_factory() as s:
        oid = _order(s, invoice_status=InvoiceStatus.ALREADY_INVOICED_EXTERNALLY.value)
    r = client.post(f"/api/erp/orders/{oid}/emit-factusol-invoice",
                    headers=auth_headers(client, "pedidos"))
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "already_invoiced_externally"


def test_emit_invoice_forbidden_for_view_only_roles(client, session_factory):
    with session_factory() as s:
        oid = _order(s)
    for role in ("sat", "user", "viewer"):
        r = client.post(f"/api/erp/orders/{oid}/emit-factusol-invoice",
                        headers=auth_headers(client, role))
        assert r.status_code == 403, role


def test_emit_invoice_threads_options_body(client, session_factory):
    with session_factory() as s:
        oid = _order(s)
    with patch("app.integrations.factusol.jobs.enqueue_emit_invoice",
               return_value="job-opt") as enq:
        r = client.post(
            f"/api/erp/orders/{oid}/emit-factusol-invoice",
            headers=auth_headers(client, "pedidos"),
            json={"tipfac": "4", "serie": 2, "fopfac": "03",
                  "comfac": "Pago 30 días"},
        )
    assert r.status_code == 202, r.text
    options = enq.call_args.kwargs["options"]
    assert options["tipfac"] == "4" and options["serie"] == 2
    assert options["fopfac"] == "03" and options["comfac"] == "Pago 30 días"


# --- estado FACTUSOL en vivo (factura/albarán existente, C-2-fix2) -----------


def test_factusol_status_invoiced_when_codfac_set(client, session_factory):
    with session_factory() as s:
        oid = _order(s, invoice_status=InvoiceStatus.INVOICED_BY_ERP.value,
                     codfac="260695")
    r = client.get(f"/api/erp/orders/{oid}/factusol-status",
                   headers=auth_headers(client, "user"))
    assert r.status_code == 200
    assert r.json() == {"status": "invoiced", "codfac": "260695",
                        "auto_linked": False}


def test_factusol_status_unknown_when_live_off(client, session_factory):
    with session_factory() as s:
        oid = _order(s)
    r = client.get(f"/api/erp/orders/{oid}/factusol-status",
                   headers=auth_headers(client, "user"))
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "unknown" and body["reason"] == "factusol_live_off"


def test_factusol_status_auto_links_existing_factura(client, session_factory):
    with session_factory() as s:
        oid = _order(s)
        _enable_factusol_live(s)
    fake = _FakeFactusol(fac=[{"CODFAC": 260695, "REFFAC": "MAN-007001"}])
    with patch("app.integrations.factusol.client.FactusolClient.from_settings",
               return_value=fake):
        r = client.get(f"/api/erp/orders/{oid}/factusol-status",
                       headers=auth_headers(client, "pedidos"))
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "invoiced" and body["codfac"] == "260695"
    assert body["auto_linked"] is True
    # Persistido: el pedido queda facturado.
    with session_factory() as s:
        o = s.get(Order, oid)
        assert o.factusol_invoice_number == "260695"
        assert o.invoice_status == InvoiceStatus.INVOICED_BY_ERP


def test_factusol_status_reports_albaran(client, session_factory):
    with session_factory() as s:
        oid = _order(s)
        _enable_factusol_live(s)
    fake = _FakeFactusol(alb=[{"CODALB": 5001, "REFALB": "MAN-007001"}])
    with patch("app.integrations.factusol.client.FactusolClient.from_settings",
               return_value=fake):
        r = client.get(f"/api/erp/orders/{oid}/factusol-status",
                       headers=auth_headers(client, "user"))
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "albaran" and body["albaran_codigo"] == "5001"


def test_factusol_status_pending_when_nothing(client, session_factory):
    with session_factory() as s:
        oid = _order(s)
        _enable_factusol_live(s)
    with patch("app.integrations.factusol.client.FactusolClient.from_settings",
               return_value=_FakeFactusol()):
        r = client.get(f"/api/erp/orders/{oid}/factusol-status",
                       headers=auth_headers(client, "user"))
    assert r.status_code == 200
    assert r.json()["status"] == "pending"


def test_factusol_status_unknown_when_factusol_unreachable(client, session_factory):
    with session_factory() as s:
        oid = _order(s)
        _enable_factusol_live(s)
    with patch("app.integrations.factusol.client.FactusolClient.from_settings",
               side_effect=RuntimeError("no creds")):
        r = client.get(f"/api/erp/orders/{oid}/factusol-status",
                       headers=auth_headers(client, "user"))
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "unknown" and body["reason"] == "factusol_unreachable"


# --- estado de facturación --------------------------------------------------


def test_invoice_status_invoiced_when_codfac_set(client, session_factory):
    with session_factory() as s:
        oid = _order(s, invoice_status=InvoiceStatus.INVOICED_BY_ERP.value,
                     codfac="526067")
    r = client.get(f"/api/erp/orders/{oid}/factusol-invoice-status",
                   headers=auth_headers(client, "user"))
    assert r.status_code == 200
    assert r.json() == {"status": "invoiced", "codfac": "526067"}


def test_invoice_status_pending_when_not_yet_invoiced(client, session_factory):
    with session_factory() as s:
        oid = _order(s)
    r = client.get(f"/api/erp/orders/{oid}/factusol-invoice-status",
                   headers=auth_headers(client, "user"))
    assert r.status_code == 200
    assert r.json()["status"] == "pending"
