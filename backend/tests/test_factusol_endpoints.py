"""BoHub ERP Fase C PR C-2 — endpoints de emisión FACTUSOL + vinculación.

La cola RQ y el cliente FACTUSOL se mockean — sin Redis ni red.
"""
from __future__ import annotations

from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.base import Base
from app.db.session import get_session
from app.erp.models import InvoiceStatus, Order, OrderLine
from app.main import app
from app.models.crm import AuditLog, Company
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


def test_emit_invoice_rejects_order_without_company(client, session_factory):
    with session_factory() as s:
        oid = _order(s, with_company=False)
    r = client.post(f"/api/erp/orders/{oid}/emit-factusol-invoice",
                    headers=auth_headers(client, "pedidos"))
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "no_company"


def test_emit_invoice_forbidden_for_view_only_roles(client, session_factory):
    with session_factory() as s:
        oid = _order(s)
    for role in ("sat", "user", "viewer"):
        r = client.post(f"/api/erp/orders/{oid}/emit-factusol-invoice",
                        headers=auth_headers(client, role))
        assert r.status_code == 403, role


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


# --- vincular empresa -------------------------------------------------------


def test_link_company_endpoint_returns_codcli(client, session_factory):
    with session_factory() as s:
        c = Company(name="Nueva SL", tax_id="B99999999")
        s.add(c)
        s.commit()
        cid = c.id
    fake_client = MagicMock()
    with patch("app.integrations.factusol.client.FactusolClient.from_settings",
               return_value=fake_client), \
         patch("app.integrations.factusol.service.ensure_customer_in_factusol",
               return_value=("77777", "existing_cif")):
        r = client.post(f"/api/companies/{cid}/link-factusol",
                        headers=auth_headers(client, "admin"))
    assert r.status_code == 200, r.text
    assert r.json() == {"company_id": cid, "factusol_codcli": "77777",
                        "matched_by": "existing_cif"}


def test_link_company_requires_admin(client, session_factory):
    with session_factory() as s:
        c = Company(name="X SL")
        s.add(c)
        s.commit()
        cid = c.id
    for role in ("pedidos", "sat", "user", "viewer"):
        r = client.post(f"/api/companies/{cid}/link-factusol",
                        headers=auth_headers(client, role))
        assert r.status_code == 403, role
