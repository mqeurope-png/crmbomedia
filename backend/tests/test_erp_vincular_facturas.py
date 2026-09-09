"""ERP · enlazar a los pedidos las facturas creadas a mano en FACTUSOL.

Empareja por la REFERENCIA común (REFFAC = `PREFIJO-NNNNNN`), no por importe.
Escribe SOLO en BoHub (nunca en FACTUSOL). Un pedido con más de una factura por
la misma referencia es un conflicto: no se enlaza. Previsualización primero.
"""
from __future__ import annotations

import json
from collections.abc import Generator
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.base import Base
from app.db.session import get_session
from app.erp.models import InvoiceStatus, Order, OrderSource
from app.integrations.factusol.invoice_reconcile import reconcile_factusol_invoices
from app.main import app
from app.models.crm import Company, ExternalSystem
from app.models.integration_settings import (
    IntegrationAccount,
    IntegrationMode,
    IntegrationStatus,
)
from tests._test_helpers import auth_headers, seed_test_users

EJ = "2026"


@pytest.fixture()
def session_factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _record):  # noqa: ANN001
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as seed:
        seed_test_users(seed)
        seed.commit()
    yield factory
    Base.metadata.drop_all(engine)


@pytest.fixture()
def http(session_factory) -> Generator:
    from fastapi.testclient import TestClient

    def override():
        with session_factory() as s:
            yield s
    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _store(s: Session, slug: str, prefix: str) -> IntegrationAccount:
    a = IntegrationAccount(
        system=ExternalSystem.WOOCOMMERCE, account_id=slug, display_name=slug,
        enabled=True, mode=IntegrationMode.LIVE, status=IntegrationStatus.CONFIGURED,
        base_url=f"https://{slug}.example",
        metadata_json=json.dumps({"factusol_ref_prefix": prefix}),
    )
    s.add(a)
    s.flush()
    return a


def _order(s: Session, *, number: str, store: IntegrationAccount | None = None,
           invoice_number: str | None = None) -> Order:
    comp = Company(name="C")
    s.add(comp)
    s.flush()
    o = Order(
        external_source=OrderSource.WOOCOMMERCE, external_id=number.split("-")[-1],
        store_id=store.id if store else None, order_number=number, company_id=comp.id,
        factusol_invoice_number=invoice_number,
    )
    s.add(o)
    s.flush()
    return o


def _fac(reffac: str, tipfac: int, codfac: int, total: float = 100.0) -> dict:
    return {"REFFAC": reffac, "TIPFAC": tipfac, "CODFAC": codfac,
            "TOTFAC": total, "FECFAC": "2026-09-01", "CODCLI": "55555"}


class FakeFactusol:
    """Cliente FACTUSOL falso: solo LEE F_FAC. No tiene métodos de escritura —
    si el core intentara escribir, el test fallaría (AttributeError)."""

    def __init__(self, fac_rows: list[dict]) -> None:
        self.fac_rows = fac_rows
        self.calls: list[tuple] = []

    def load_table(self, tabla, *, filtro="1=1", ejercicio=None):
        self.calls.append((tabla, filtro))
        return list(self.fac_rows) if tabla == "F_FAC" else []


# --- Part C ------------------------------------------------------------------------


def test_links_manual_factusol_invoice_to_order(session_factory) -> None:
    with session_factory() as s:
        flux = _store(s, "fluxlasers", "FLE")
        _order(s, number="FLUXLA-5752", store=flux)
        s.commit()
        client = FakeFactusol([_fac("FLE-005752", 1, 260738)])
        summary = reconcile_factusol_invoices(s, client, EJ, dry_run=False)
        assert summary["linked"] == 1
        assert summary["to_link"][0]["numero"] == "1-260738"
        o = s.scalar(select(Order).where(Order.order_number == "FLUXLA-5752"))
        assert o.factusol_invoice_number == "260738"
        assert o.invoice_status.value == InvoiceStatus.INVOICED_BY_ERP.value


def test_match_by_order_reference_not_amount(session_factory) -> None:
    with session_factory() as s:
        flux = _store(s, "fluxlasers", "FLE")
        _order(s, number="FLUXLA-5770", store=flux)
        s.commit()
        # Misma importe (100) pero referencia distinta → NO debe casar; la de la
        # referencia correcta (aunque con otro importe) → SÍ.
        client = FakeFactusol([
            _fac("XXX-000000", 9, 999999, total=100.0),   # importe igual, ref mala
            _fac("FLE-005770", 1, 260739, total=250.0),   # ref buena
        ])
        summary = reconcile_factusol_invoices(s, client, EJ, dry_run=False)
        assert summary["linked"] == 1
        assert summary["to_link"][0]["codfac"] == "260739"
        o = s.scalar(select(Order).where(Order.order_number == "FLUXLA-5770"))
        assert o.factusol_invoice_number == "260739"


def test_ambiguous_or_duplicate_invoice_is_reported_not_linked(session_factory) -> None:
    with session_factory() as s:
        art = _store(s, "artisjet", "ART")
        _order(s, number="ARTISJ-9505", store=art)
        s.commit()
        # Facturado DOS veces con la misma referencia (2-526083 y 5-260069).
        client = FakeFactusol([
            _fac("ART-009505", 2, 526083),
            _fac("ART-009505", 5, 260069),
        ])
        summary = reconcile_factusol_invoices(s, client, EJ, dry_run=False)
        assert summary["linked"] == 0
        assert len(summary["conflicts"]) == 1
        conf = summary["conflicts"][0]
        assert conf["order_number"] == "ARTISJ-9505"
        assert {f["numero"] for f in conf["facturas"]} == {"2-526083", "5-260069"}
        # NO se enlazó ninguna: el pedido sigue sin número de factura.
        o = s.scalar(select(Order).where(Order.order_number == "ARTISJ-9505"))
        assert o.factusol_invoice_number is None


def test_reconcile_only_writes_bohub_not_factusol(session_factory) -> None:
    with session_factory() as s:
        flux = _store(s, "fluxlasers", "FLE")
        _order(s, number="FLUXLA-5771", store=flux)
        s.commit()
        client = FakeFactusol([_fac("FLE-005771", 1, 260740)])
        reconcile_factusol_invoices(s, client, EJ, dry_run=False)
        # El fake solo tiene load_table (lectura); si el core escribiera en
        # FACTUSOL, habría fallado. Confirmamos que solo se leyó F_FAC.
        assert all(c[0] == "F_FAC" for c in client.calls)
        # Y que BoHub sí quedó actualizado.
        o = s.scalar(select(Order).where(Order.order_number == "FLUXLA-5771"))
        assert o.factusol_invoice_number == "260740"


def test_reconcile_preview_before_apply(session_factory) -> None:
    with session_factory() as s:
        flux = _store(s, "fluxlasers", "FLE")
        _order(s, number="FLUXLA-5772", store=flux)
        s.commit()
        client = FakeFactusol([_fac("FLE-005772", 1, 260741)])
        preview = reconcile_factusol_invoices(s, client, EJ, dry_run=True)
        assert preview["preview"] is True
        assert preview["linked"] == 1
        # Previsualización: NO se ha escrito.
        o = s.scalar(select(Order).where(Order.order_number == "FLUXLA-5772"))
        assert o.factusol_invoice_number is None


def test_already_linked_orders_untouched(session_factory) -> None:
    with session_factory() as s:
        flux = _store(s, "fluxlasers", "FLE")
        # Ya facturado en BoHub → no es candidato.
        _order(s, number="FLUXLA-5773", store=flux, invoice_number="260742")
        s.commit()
        client = FakeFactusol([_fac("FLE-005773", 1, 999999)])   # otra factura distinta
        summary = reconcile_factusol_invoices(s, client, EJ, dry_run=False)
        assert summary["linked"] == 0
        o = s.scalar(select(Order).where(Order.order_number == "FLUXLA-5773"))
        assert o.factusol_invoice_number == "260742"   # intacto


# --- endpoint async (encola) -------------------------------------------------------


def test_reconcile_factusol_endpoint_enqueues(session_factory, http) -> None:
    with patch("app.integrations.factusol.jobs.enqueue_factusol_invoice_reconcile",
               return_value="job-fac-1") as enq:
        r = http.post("/api/erp/seguimiento/reconcile-factusol?dry_run=true",
                      headers=auth_headers(http, "pedidos"))
    assert r.status_code == 202, r.text
    assert r.json() == {"job_id": "job-fac-1", "status": "queued", "preview": True}
    enq.assert_called_once()


def test_reconcile_factusol_status_pending_without_redis(session_factory, http) -> None:
    r = http.get("/api/erp/seguimiento/reconcile-factusol-status/nope",
                 headers=auth_headers(http, "pedidos"))
    assert r.status_code == 200
    assert r.json() == {"status": "pending"}
