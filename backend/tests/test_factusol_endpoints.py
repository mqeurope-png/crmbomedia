"""BoHub ERP Fase C PR C-2 — endpoints de emisión FACTUSOL + vinculación.

La cola RQ y el cliente FACTUSOL se mockean — sin Redis ni red.
"""
from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
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
            json={"serie": 2, "fopfac": "03", "comfac": "Pago 30 días"},
        )
    assert r.status_code == 202, r.text
    options = enq.call_args.kwargs["options"]
    assert options["serie"] == 2
    assert options["fopfac"] == "03" and options["comfac"] == "Pago 30 días"
    # ERP-E2-fix2: `tipfac` ya no existe — su default "1" sellaba TODAS las
    # facturas como serie 1 (Bomedia) y provocaba BDExisteRegistro.
    assert "tipfac" not in options


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


# --- Lote 2 · PR-2: «Vincular» albarán / factura a un pedido de BoHub --------
#
# FACTUSOL solo se LEE (la cabecera del documento, por `CODFAC=`/`CODALB=`);
# la escritura es exclusivamente en los campos del pedido.


class _FakeDocs:
    """Sirve F_FAC / F_ALB en memoria casando el filtro `COL=valor`."""

    def __init__(self, *, fac=None, alb=None):
        self.default_ejercicio = "2026"
        self.tables = {"F_FAC": list(fac or []), "F_ALB": list(alb or [])}
        self.calls: list[tuple[str, str]] = []

    def load_table(self, tabla, *, filtro="1=1", ejercicio=None):
        self.calls.append((tabla, filtro))
        rows = self.tables.get(tabla, [])
        predicate = filtro.split(" ORDER BY ")[0].strip()
        if predicate == "1=1":
            return rows
        column, _, raw = predicate.partition("=")
        return [r for r in rows if str(r.get(column.strip())) == raw.strip().strip("'")]


FAC_5_260066 = {"TIPFAC": "5", "CODFAC": 260066, "CLIFAC": 55555, "CNOFAC": "ACME SL",
                "REFFAC": "MAN-007001", "FECFAC": "2026-09-01T00:00:00", "TOTFAC": 200.0}
ALB_5_91 = {"TIPALB": "5", "CODALB": 91, "CLIALB": 55555, "CNOALB": "ACME SL",
            "REFALB": "MAN-007001", "FECALB": "2026-09-01T00:00:00", "TOTALB": 200.0}


def _patched(fake):
    return patch("app.integrations.factusol.client.FactusolClient.from_settings",
                 return_value=fake)


def _other_order(s: Session, number: str, *, company_id=None, **fields) -> str:
    o = Order(order_number=number, company_id=company_id, total_amount=50, **fields)
    s.add(o)
    s.commit()
    return o.id


def test_link_candidates_by_reffac_then_customer_then_search(client, session_factory):
    """Sugerencias: primero el pedido cuya referencia común es la REFFAC
    (fuerte), luego los de la misma empresa (débil) y, con `q`, por nº."""
    with session_factory() as s:
        oid = _order(s)  # MAN-7001 → ref MAN-007001, empresa Acme (CODCLI 55555)
        company_id = s.get(Order, oid).company_id
        same_co = _other_order(s, "MAN-7002", company_id=company_id)
        _other_order(s, "MAN-7003", company_id=company_id, cancelled_at=datetime.now(UTC))
        otro = _other_order(s, "ZZZ-9001")
    with _patched(_FakeDocs(fac=[FAC_5_260066])):
        r = client.get("/api/erp/factusol/documents/facturas/5/260066/link-candidates",
                       headers=auth_headers(client, "user"))
        r_q = client.get("/api/erp/factusol/documents/facturas/5/260066/link-candidates?q=zzz",
                         headers=auth_headers(client, "user"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["doc"]["numero"] == "5-260066" and body["doc"]["referencia"] == "MAN-007001"
    assert body["linked_orders"] == []
    assert [(c["id"], c["match"]) for c in body["candidates"]] == [
        (oid, "referencia"), (same_co, "cliente"),  # el anulado no se sugiere
    ]
    assert body["candidates"][0]["company_name"] == "Acme SL"
    assert body["candidates"][0]["current_link"] is None
    found = r_q.json()["candidates"]
    assert (otro, "busqueda") in [(c["id"], c["match"]) for c in found]


def test_link_candidates_rejects_presupuestos(client, session_factory):
    _ = session_factory
    with _patched(_FakeDocs()):
        r = client.get("/api/erp/factusol/documents/presupuestos/5/27/link-candidates",
                       headers=auth_headers(client, "user"))
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "link_not_supported"


def test_link_order_factura_writes_serie_and_number(client, session_factory):
    """Vincular una factura escribe serie + número en el pedido (vía
    `relink_order`), deja historial y auditoría, y el listado la enseña con
    su pedido. Nada se escribe en FACTUSOL (solo lecturas en el fake)."""
    with session_factory() as s:
        oid = _order(s)
    fake = _FakeDocs(fac=[FAC_5_260066])
    with _patched(fake):
        r = client.post("/api/erp/factusol/documents/facturas/5/260066/link-order",
                        json={"order_id": oid, "confirm": True},
                        headers=auth_headers(client, "pedidos"))
        assert r.status_code == 200, r.text
        listing = client.get("/api/erp/factusol/documents/facturas",
                             headers=auth_headers(client, "user"))
    body = r.json()
    assert body["status"] == "linked" and body["doc"]["numero"] == "5-260066"
    assert body["order"]["order_number"] == "MAN-7001" and body["previous"] is None
    with session_factory() as s:
        o = s.get(Order, oid)
        assert o.factusol_invoice_number == "260066"
        assert o.factusol_invoice_serie == 5
        assert o.invoice_status == InvoiceStatus.INVOICED_BY_ERP
        audit = s.scalars(select(AuditLog).where(
            AuditLog.action == "erp.factusol_document_link")).all()
        assert len(audit) == 1 and audit[0].target_id == "facturas:5-260066"
    row = listing.json()["items"][0]
    assert row["order"] == {"id": oid, "order_number": "MAN-7001"}
    assert listing.json()["unlinked_total"] == 0
    # Solo lecturas contra FACTUSOL.
    assert all(t in ("F_FAC", "F_LFA", "F_LAL", "F_LCO", "F_CLI") for t, _ in fake.calls)


def test_link_order_albaran_writes_albaran_number(client, session_factory):
    with session_factory() as s:
        oid = _order(s)
    with _patched(_FakeDocs(alb=[ALB_5_91])):
        r = client.post("/api/erp/factusol/documents/albaranes/5/91/link-order",
                        json={"order_id": oid, "confirm": True},
                        headers=auth_headers(client, "admin"))
    assert r.status_code == 200, r.text
    assert r.json()["doc"]["numero"] == "5-000091"
    with session_factory() as s:
        o = s.get(Order, oid)
        assert o.factusol_albaran_number == "5-000091"
        from app.erp.models import OrderStatusHistory  # noqa: PLC0415

        hist = s.scalars(select(OrderStatusHistory).where(
            OrderStatusHistory.order_id == oid)).all()
        assert any("5-000091" in (h.reason or "") for h in hist)


def test_link_order_requires_confirm_edit_role_and_existing_doc(client, session_factory):
    with session_factory() as s:
        oid = _order(s)
    with _patched(_FakeDocs(fac=[FAC_5_260066])):
        sin_confirm = client.post(
            "/api/erp/factusol/documents/facturas/5/260066/link-order",
            json={"order_id": oid, "confirm": False},
            headers=auth_headers(client, "pedidos"))
        solo_ver = client.post(
            "/api/erp/factusol/documents/facturas/5/260066/link-order",
            json={"order_id": oid, "confirm": True},
            headers=auth_headers(client, "user"))
        no_doc = client.post(
            "/api/erp/factusol/documents/facturas/5/999999/link-order",
            json={"order_id": oid, "confirm": True},
            headers=auth_headers(client, "pedidos"))
        no_order = client.post(
            "/api/erp/factusol/documents/facturas/5/260066/link-order",
            json={"order_id": "nope", "confirm": True},
            headers=auth_headers(client, "pedidos"))
        presupuesto = client.post(
            "/api/erp/factusol/documents/presupuestos/5/27/link-order",
            json={"order_id": oid, "confirm": True},
            headers=auth_headers(client, "pedidos"))
    assert sin_confirm.status_code == 400
    assert sin_confirm.json()["detail"]["code"] == "confirm_required"
    assert solo_ver.status_code == 403
    assert no_doc.status_code == 404
    assert no_order.status_code == 404
    assert presupuesto.status_code == 400
    with session_factory() as s:
        assert s.get(Order, oid).factusol_invoice_number is None


def test_link_order_conflicts_and_force(client, session_factory):
    """409 si el pedido ya tiene OTRA factura o si la factura ya apunta a
    otro pedido; `force` lo salta. Re-vincular la misma no es conflicto."""
    with session_factory() as s:
        oid = _order(s, codfac="111111")           # ya tiene otra factura
        otro = _other_order(s, "MAN-7002", factusol_invoice_number="260066",
                            factusol_invoice_serie=5)  # ya apunta a la 5-260066
        libre = _other_order(s, "MAN-7003")
    with _patched(_FakeDocs(fac=[FAC_5_260066])):
        h = auth_headers(client, "pedidos")
        r1 = client.post("/api/erp/factusol/documents/facturas/5/260066/link-order",
                         json={"order_id": oid, "confirm": True}, headers=h)
        r2 = client.post("/api/erp/factusol/documents/facturas/5/260066/link-order",
                         json={"order_id": libre, "confirm": True}, headers=h)
        r3 = client.post("/api/erp/factusol/documents/facturas/5/260066/link-order",
                         json={"order_id": otro, "confirm": True}, headers=h)
        r4 = client.post("/api/erp/factusol/documents/facturas/5/260066/link-order",
                         json={"order_id": oid, "confirm": True, "force": True}, headers=h)
    assert r1.status_code == 409 and r1.json()["detail"]["code"] == "order_already_linked"
    assert r1.json()["detail"]["current"] == "111111"
    assert r2.status_code == 409 and r2.json()["detail"]["code"] == "document_already_linked"
    assert r2.json()["detail"]["existing"]["order_number"] == "MAN-7002"
    assert r3.status_code == 200, r3.text  # el mismo pedido: idempotente
    assert r4.status_code == 200, r4.text
    assert r4.json()["previous"] == "111111"
    with session_factory() as s:
        o = s.get(Order, oid)
        assert (o.factusol_invoice_serie, o.factusol_invoice_number) == (5, "260066")
