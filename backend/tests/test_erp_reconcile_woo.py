"""ERP · WooCommerce — reconciliación en segundo plano + por listado de estados.

- El endpoint encola y responde al instante con un `job_id` (no bloquea → no 504).
- El endpoint de estado informa de en curso / terminado (+resumen) / error.
- El núcleo LISTA por estado (`cancelled`/`refunded`/`failed`/`trash`) y cruza con
  los activos, en vez de un `get_order` por pedido. Respeta la fecha de corte.
- Las reglas de #376 no cambian.
"""
from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.core.crypto import encrypt
from app.db.base import Base
from app.db.session import get_session
from app.erp import seguimiento as core
from app.erp.api.seguimiento import _rows
from app.erp.models import InvoiceStatus, Order, OrderSource, TransportStatus
from app.integrations.woocommerce.client import WooError
from app.integrations.woocommerce.reconcile import reconcile_open_order_statuses
from app.main import app
from app.models.crm import Company, ExternalSystem
from app.models.integration_settings import (
    IntegrationAccount,
    IntegrationMode,
    IntegrationStatus,
)
from tests._test_helpers import auth_headers, seed_test_users


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


def _store(s: Session, slug: str = "boprint") -> IntegrationAccount:
    a = IntegrationAccount(
        system=ExternalSystem.WOOCOMMERCE, account_id=slug, display_name=slug,
        enabled=True, mode=IntegrationMode.LIVE, status=IntegrationStatus.CONFIGURED,
        base_url=f"https://{slug}.example",
        consumer_key_encrypted=encrypt("ck"), consumer_secret_encrypted=encrypt("cs"),
        credential_status="configured",
    )
    s.add(a)
    s.flush()
    return a


def _order(
    s: Session, *, woo_id: str, number: str, woo_status: str | None,
    store: IntegrationAccount, tracking: str | None = None,
    delivered: bool = False, invoiced: bool = False, placed: str = "2026-09-01",
) -> Order:
    comp = Company(name="C")
    s.add(comp)
    s.flush()
    o = Order(
        external_source=OrderSource.WOOCOMMERCE, external_id=woo_id,
        store_id=store.id, order_number=number, company_id=comp.id,
        woo_status=woo_status, tracking_number=tracking,
        placed_at=datetime.fromisoformat(placed).replace(tzinfo=UTC),
    )
    if delivered:
        o.transport_status = TransportStatus.DELIVERED
    if invoiced:
        o.invoice_status = InvoiceStatus.GENERATED
        o.factusol_invoice_number = f"5-{woo_id}"
    s.add(o)
    s.flush()
    return o


def _rows_for(s: Session, **kw) -> list[dict]:
    return core.filter_rows(_rows(s), **kw)


def _numbers(rows: list[dict]) -> set[str]:
    return {r["order_number"] for r in rows}


class FakeWoo:
    """Cliente falso: responde a `list_orders(status=…)` con la lista
    configurada por estado; registra las llamadas. NO tiene `get_order` — si la
    reconciliación lo llamara, el test fallaría (AttributeError)."""

    def __init__(self, store, by_status: dict[str, list[dict]],
                 calls: list[tuple], raise_on: str | None = None) -> None:
        self.store = store
        self.by_status = by_status
        self.calls = calls
        self.raise_on = raise_on

    def list_orders(self, *, status="processing", since=None, per_page=50, page=1):
        self.calls.append(("list_orders", self.store.account_id, status, since, page))
        if self.raise_on and status == self.raise_on:
            raise WooError("store down", status=503)
        if page > 1:
            return []
        return list(self.by_status.get(status, []))


def _factory(by_status_by_store: dict[str, dict[str, list[dict]]], calls: list[tuple],
             raise_for: dict[str, str] | None = None):
    def factory(store):
        return FakeWoo(
            store, by_status_by_store.get(store.account_id, {}), calls,
            raise_on=(raise_for or {}).get(store.account_id),
        )
    return factory


# --- Parte A: async (endpoint encola + estado) -------------------------------------


def test_reconcile_enqueues_job_and_returns_id(session_factory, http) -> None:
    with patch("app.integrations.woocommerce.jobs.enqueue_woo_reconcile",
               return_value="job-rec-1") as enq:
        r = http.post("/api/erp/seguimiento/reconcile-woo?dry_run=true",
                      headers=auth_headers(http, "pedidos"))
    assert r.status_code == 202, r.text
    assert r.json() == {"job_id": "job-rec-1", "status": "queued", "preview": True}
    enq.assert_called_once()


def test_reconcile_status_endpoint_reports_progress_and_result(session_factory, http) -> None:
    summary = {"ok": True, "preview": True, "to_cancel": 2, "removed_total": 2}
    fake_job = MagicMock()
    fake_job.get_status.return_value = "finished"
    fake_job.result = summary
    with patch("redis.Redis.from_url", return_value=MagicMock()), \
         patch("rq.job.Job.fetch", return_value=fake_job):
        r = http.get("/api/erp/seguimiento/reconcile-woo-status/job-rec-1",
                     headers=auth_headers(http, "pedidos"))
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "finished", "result": summary}


def test_reconcile_job_failure_is_reported(session_factory, http) -> None:
    fake_job = MagicMock()
    fake_job.get_status.return_value = "failed"
    with patch("redis.Redis.from_url", return_value=MagicMock()), \
         patch("rq.job.Job.fetch", return_value=fake_job):
        r = http.get("/api/erp/seguimiento/reconcile-woo-status/job-x",
                     headers=auth_headers(http, "pedidos"))
    assert r.status_code == 200
    assert r.json()["status"] == "error"
    assert r.json().get("error")


def test_reconcile_status_pending_without_redis(session_factory, http) -> None:
    # Sin Redis (o job desconocido) → pending, no revienta.
    r = http.get("/api/erp/seguimiento/reconcile-woo-status/nope",
                 headers=auth_headers(http, "pedidos"))
    assert r.status_code == 200
    assert r.json() == {"status": "pending"}


def test_reconcile_store_down_is_reported_not_hang(session_factory) -> None:
    with session_factory() as s:
        st = _store(s)
        _order(s, woo_id="10", number="BOPRIN-10", woo_status="processing", store=st)
        s.commit()
        calls: list[tuple] = []
        summary = reconcile_open_order_statuses(
            s, dry_run=True,
            client_factory=_factory({}, calls, raise_for={"boprint": "cancelled"}),
        )
    # La tienda caída se reporta como error; la reconciliación NO cuelga.
    assert summary["ok"] is True
    assert any(e.get("status") == "cancelled" for e in summary["errors"])


# --- Parte B: eficiencia (listado, no pedido a pedido) -----------------------------


def test_reconcile_lists_by_hidden_status_not_per_order(session_factory) -> None:
    with session_factory() as s:
        st = _store(s)
        for i in range(5):
            _order(s, woo_id=str(20 + i), number=f"BOPRIN-{20 + i}",
                   woo_status="processing", store=st)
        s.commit()
        calls: list[tuple] = []
        by_store = {"boprint": {"cancelled": [{"id": 20, "status": "cancelled"}]}}
        summary = reconcile_open_order_statuses(
            s, dry_run=True, client_factory=_factory(by_store, calls),
        )
    # Solo llamadas de LISTADO (no un get_order por pedido).
    assert all(c[0] == "list_orders" for c in calls)
    requested = {c[2] for c in calls}
    assert {"cancelled", "refunded", "failed"} <= requested
    # 5 pedidos activos, 1 cancelado detectado → pocas llamadas, no 5 get_order.
    assert summary["to_cancel"] == 1
    assert summary["woo_calls"] == len(calls)
    assert summary["woo_calls"] <= 8   # ~1 por estado oculto (4), no 5 por pedido


def test_reconcile_respects_cutoff_date(session_factory) -> None:
    with session_factory() as s:
        st = _store(s)
        _order(s, woo_id="30", number="BOPRIN-30", woo_status="processing",
               store=st, placed="2026-07-15")
        _order(s, woo_id="31", number="BOPRIN-31", woo_status="processing",
               store=st, placed="2026-08-20")
        s.commit()
        calls: list[tuple] = []
        reconcile_open_order_statuses(s, dry_run=True, client_factory=_factory({}, calls))
    # `since` = fecha del pedido activo MÁS ANTIGUO (no todo el histórico).
    sinces = {c[3] for c in calls}
    assert sinces == {"2026-07-15"}


def test_reconcile_intersects_with_active_orders(session_factory) -> None:
    with session_factory() as s:
        st = _store(s)
        _order(s, woo_id="40", number="BOPRIN-40", woo_status="processing", store=st)
        s.commit()
        calls: list[tuple] = []
        # Woo devuelve un cancelado que BoHub NO tiene como activo (999) además
        # del 40 → el 999 no genera ruido.
        summary = reconcile_open_order_statuses(
            s, dry_run=False,
            client_factory=_factory({"boprint": {"cancelled": [
                {"id": 40, "status": "cancelled"},
                {"id": 999, "status": "cancelled"},
            ]}}, calls),
        )
    assert summary["to_cancel"] == 1
    assert summary["removed_total"] == 1
    assert "BOPRIN-40" not in _numbers(_rows_for(s, en_curso=True))


# --- Parte C: reglas de #376 sin cambios -------------------------------------------


def test_reconcile_rules_unchanged(session_factory) -> None:
    with session_factory() as s:
        st = _store(s)
        _order(s, woo_id="50", number="BOPRIN-50", woo_status="processing", store=st)
        _order(s, woo_id="51", number="BOPRIN-51", woo_status="processing",
               store=st, tracking="1Z")            # reembolso cumplido
        _order(s, woo_id="52", number="BOPRIN-52", woo_status="processing", store=st)  # se queda
        s.commit()
        calls: list[tuple] = []
        summary = reconcile_open_order_statuses(
            s, dry_run=False,
            client_factory=_factory({"boprint": {
                "cancelled": [{"id": 50, "status": "cancelled"}],
                "refunded": [{"id": 51, "status": "refunded"}],
            }}, calls),
        )
    assert summary["to_cancel"] == 1
    assert summary["to_refund_kept"] == 1
    assert summary["removed_total"] == 1
    live = _numbers(_rows_for(s, en_curso=True))
    assert "BOPRIN-50" not in live            # cancelado fuera
    assert "BOPRIN-52" in live                # pending/processing se queda
    row51 = next(r for r in _rows_for(s, en_curso=True) if r["order_number"] == "BOPRIN-51")
    assert row51["reembolsado"] is True       # reembolso cumplido, marcado


def test_reconcile_preview_counts_by_category(session_factory) -> None:
    with session_factory() as s:
        st = _store(s)
        _order(s, woo_id="60", number="BOPRIN-60", woo_status="processing", store=st)
        _order(s, woo_id="61", number="BOPRIN-61", woo_status="processing", store=st)
        s.commit()
        calls: list[tuple] = []
        preview = reconcile_open_order_statuses(
            s, dry_run=True,
            client_factory=_factory({"boprint": {
                "cancelled": [{"id": 60, "status": "cancelled"}],
                "failed": [{"id": 61, "status": "failed"}],
            }}, calls),
        )
        assert preview["preview"] is True
        assert preview["to_cancel"] == 1
        assert preview["to_fail"] == 1
        # Previsualización: NO se ha escrito nada.
        o = s.scalar(select(Order).where(Order.external_id == "60"))
        assert o.woo_status == "processing"
        assert "BOPRIN-60" in _numbers(_rows_for(s, en_curso=True))
