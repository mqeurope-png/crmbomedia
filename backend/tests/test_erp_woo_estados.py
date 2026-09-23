"""ERP · WooCommerce — cancelado / reembolsado / fallido salen del seguimiento.

Cubre: la regla de visibilidad por estado (Parte A/D), el webhook de cambio de
estado (Parte B), la reconciliación de los ya importados (Parte C), el efecto
en Drive (Parte E) y que el estado NO se confunde con la exclusión manual de
F6-fix7 (Parte D).
"""
from __future__ import annotations

import json
from collections.abc import Generator
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.integrations.woocommerce.jobs as woo_jobs
import app.main  # noqa: F401
from app.core.crypto import encrypt
from app.db.base import Base
from app.db.session import get_session
from app.erp import seguimiento as core
from app.erp.api.seguimiento import _rows, build_drive_sync_rows
from app.erp.drive_sheets import sync_to_sheet
from app.erp.models import (
    IntegrationEvent,
    InvoiceStatus,
    Order,
    OrderSource,
    TransportStatus,
)
from app.integrations.woocommerce.mapper import import_woo_order
from app.main import app
from app.models.crm import Company, ExternalSystem
from app.models.integration_settings import (
    IntegrationAccount,
    IntegrationMode,
    IntegrationStatus,
)
from tests._test_helpers import auth_headers, seed_test_users

HEADER = list(core.SEGUIMIENTO_COLUMNS)
_IDX = {n: i for i, n in enumerate(core.SEGUIMIENTO_COLUMNS)}
KEY = _IDX["Albarán / Nº Pedido Web"]


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
    store: IntegrationAccount, cliente: str = "Cliente SL",
    tracking: str | None = None, delivered: bool = False, invoiced: bool = False,
) -> Order:
    comp = Company(name=cliente)
    s.add(comp)
    s.flush()
    o = Order(
        external_source=OrderSource.WOOCOMMERCE, external_id=woo_id,
        store_id=store.id, order_number=number, company_id=comp.id,
        woo_status=woo_status, tracking_number=tracking,
        placed_at=datetime(2026, 9, 1, tzinfo=UTC),
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


# --- Parte A/D: la regla de visibilidad --------------------------------------------


def test_cancelled_order_removed_from_seguimiento(session_factory) -> None:
    with session_factory() as s:
        st = _store(s)
        _order(s, woo_id="1", number="BOPRIN-1", woo_status="cancelled", store=st)
        s.commit()
        default = _rows_for(s, en_curso=True)
        assert "BOPRIN-1" not in _numbers(default)
        # Aparece en la vista de «ocultados por estado».
        ocultos = _rows_for(s, ver_ocultos_estado=True)
        assert _numbers(ocultos) == {"BOPRIN-1"}
        assert ocultos[0]["oculto_por_estado"] is True
        assert ocultos[0]["estado_woo_motivo"] == "cancelled"


def test_failed_order_removed_from_seguimiento(session_factory) -> None:
    with session_factory() as s:
        st = _store(s)
        _order(s, woo_id="2", number="BOPRIN-2", woo_status="failed", store=st)
        s.commit()
        assert "BOPRIN-2" not in _numbers(_rows_for(s, en_curso=True))
        assert _numbers(_rows_for(s, ver_ocultos_estado=True)) == {"BOPRIN-2"}


def test_pending_order_stays_in_seguimiento(session_factory) -> None:
    with session_factory() as s:
        st = _store(s)
        _order(s, woo_id="3", number="BOPRIN-3", woo_status="pending", store=st)
        s.commit()
        rows = _rows_for(s, en_curso=True)
        assert "BOPRIN-3" in _numbers(rows)
        row = next(r for r in rows if r["order_number"] == "BOPRIN-3")
        assert row["oculto_por_estado"] is False
        assert row["pendiente_escribir"] is True


def test_pedido_anulado_sale_del_seguimiento(session_factory) -> None:
    """El caso reportado: un pedido que se anula en BoHub seguía saliendo como
    «Pendiente» / «Por revisar». Seguimiento es la lista de pedidos VIVOS."""
    with session_factory() as s:
        st = _store(s)
        o = _order(s, woo_id="10", number="BOPRIN-10", woo_status="processing",
                   store=st)
        o.cancelled_at = datetime(2026, 9, 10, tzinfo=UTC)
        s.commit()
        assert "BOPRIN-10" not in _numbers(_rows_for(s, en_curso=True))
        ocultos = _rows_for(s, ver_ocultos_estado=True)
        row = next(r for r in ocultos if r["order_number"] == "BOPRIN-10")
        assert row["oculto_por_estado"] is True
        assert row["estado_woo_motivo"] == "anulado"


def test_la_anulacion_manda_sobre_el_estado_de_la_tienda(session_factory) -> None:
    """Anulado en BoHub sale aunque en la tienda siga «completed»."""
    with session_factory() as s:
        st = _store(s)
        o = _order(s, woo_id="11", number="BOPRIN-11", woo_status="completed",
                   store=st, invoiced=True)
        o.cancelled_at = datetime(2026, 9, 10, tzinfo=UTC)
        s.commit()
        assert "BOPRIN-11" not in _numbers(_rows_for(s, en_curso=True))


def test_un_pedido_sin_pagar_sigue_en_seguimiento(session_factory) -> None:
    """No se excluye por falta de cobro: un pedido vivo sin pagar es
    exactamente lo que hay que ver."""
    with session_factory() as s:
        st = _store(s)
        _order(s, woo_id="12", number="BOPRIN-12", woo_status="pending", store=st)
        s.commit()
        rows = _rows_for(s, en_curso=True)
        assert "BOPRIN-12" in _numbers(rows)
        row = next(r for r in rows if r["order_number"] == "BOPRIN-12")
        assert row["oculto_por_estado"] is False


def test_refunded_unfulfilled_order_removed(session_factory) -> None:
    with session_factory() as s:
        st = _store(s)
        _order(s, woo_id="4", number="BOPRIN-4", woo_status="refunded", store=st)
        s.commit()
        assert "BOPRIN-4" not in _numbers(_rows_for(s, en_curso=True))
        ocultos = _rows_for(s, ver_ocultos_estado=True)
        assert ocultos[0]["estado_woo_motivo"] == "refunded_sin_cumplir"


def test_refunded_fulfilled_order_sale_de_la_vista_pero_marcado(session_factory) -> None:
    """Un reembolso YA CUMPLIDO (enviado) se quedaba antes en la vista marcado
    «reembolsado». Ya no: el seguimiento es la lista de pedidos VIVOS y un
    reembolsado no lo está. Sigue a un clic, en «Ver ocultos por estado», y
    conserva la marca para distinguirlo del que nunca se cumplió."""
    with session_factory() as s:
        st = _store(s)
        _order(s, woo_id="5", number="BOPRIN-5", woo_status="refunded", store=st,
               tracking="1Z-ENVIADO")
        s.commit()
        assert "BOPRIN-5" not in _numbers(_rows_for(s, en_curso=True))
        ocultos = _rows_for(s, en_curso=True, ver_ocultos_estado=True)
        row = next(r for r in ocultos if r["order_number"] == "BOPRIN-5")
        assert row["oculto_por_estado"] is True
        assert row["reembolsado"] is True                  # cumplido
        assert row["estado_woo_motivo"] == "refunded"


def test_refunded_fulfilled_by_invoice_tambien_sale(session_factory) -> None:
    """Cumplido por FACTURA (no por envío): misma regla."""
    with session_factory() as s:
        st = _store(s)
        _order(s, woo_id="6", number="BOPRIN-6", woo_status="refunded", store=st,
               invoiced=True)
        s.commit()
        assert "BOPRIN-6" not in _numbers(_rows_for(s, en_curso=True))
        ocultos = _rows_for(s, en_curso=True, ver_ocultos_estado=True)
        row = next(r for r in ocultos if r["order_number"] == "BOPRIN-6")
        assert row["reembolsado"] is True


# --- Parte B: webhook de cambio de estado ------------------------------------------


def test_status_change_webhook_updates_order(session_factory) -> None:
    # El pedido entra como processing; llega order.updated con cancelled.
    with session_factory() as s:
        st = _store(s)
        payload = {"id": 700, "number": "700", "status": "processing",
                   "total": "10", "currency": "EUR",
                   "date_created": "2026-09-01T00:00:00Z",
                   "billing": {"email": "a@b.com"}, "line_items": [], "meta_data": [],
                   "_store_slug": st.account_id}
        import_woo_order(s, store=st, woo_order=payload)
        s.commit()
        ev = IntegrationEvent(
            system="woocommerce", account_id=st.account_id,
            external_event_id="wh-700-upd", event_type="order.updated",
            payload_json=json.dumps({"id": 700}),
        )
        s.add(ev)
        s.commit()
        event_id = ev.id

    fake = MagicMock()
    fake.get_order.return_value = {"id": 700, "number": "700", "status": "cancelled",
                                   "total": "10", "currency": "EUR",
                                   "billing": {"email": "a@b.com"}, "line_items": []}
    with patch.object(woo_jobs, "_session_factory", return_value=session_factory), \
         patch.object(woo_jobs, "WooHTTPClient", return_value=fake):
        woo_jobs.process_webhook_event(event_id)

    with session_factory() as s:
        o = s.scalar(select(Order).where(Order.external_id == "700"))
        assert o.woo_status == "cancelled"
        assert "BOPRIN-700" not in _numbers(_rows_for(s, en_curso=True))


def test_order_deleted_webhook_marks_trash(session_factory) -> None:
    with session_factory() as s:
        st = _store(s)
        _order(s, woo_id="800", number="BOPRIN-800", woo_status="processing", store=st)
        s.commit()
        ev = IntegrationEvent(
            system="woocommerce", account_id=st.account_id,
            external_event_id="wh-800-del", event_type="order.deleted",
            payload_json=json.dumps({"id": 800}),
        )
        s.add(ev)
        s.commit()
        event_id = ev.id
    with patch.object(woo_jobs, "_session_factory", return_value=session_factory), \
         patch.object(woo_jobs, "WooHTTPClient") as MockClient:
        res = woo_jobs.process_webhook_event(event_id)
    MockClient.assert_not_called()   # no re-consulta un pedido borrado
    assert res["matched"] is True
    with session_factory() as s:
        o = s.scalar(select(Order).where(Order.external_id == "800"))
        assert o.woo_status == "trash"
        assert "BOPRIN-800" not in _numbers(_rows_for(s, en_curso=True))


# --- Parte C: reconciliación — ver test_erp_reconcile_woo.py (async + listado).


# --- Parte E: Drive ----------------------------------------------------------------


class FakeSheet:
    def __init__(self, grid):
        self.grid = [list(r) for r in grid]

    def get_values(self):
        return [list(r) for r in self.grid]

    def update_cells(self, updates):
        for row, col, value in updates:
            r = self.grid[row - 1]
            while len(r) <= col:
                r.append("")
            r[col] = value

    def insert_rows_at(self, row, count):
        for _ in range(count):
            self.grid.insert(row - 1, [""] * len(HEADER))

    def write_rows(self, start_row, rows):  # pragma: no cover
        for i, values in enumerate(rows):
            self.grid[start_row - 1 + i] = list(values)


def test_cancelled_not_inserted_into_drive(session_factory) -> None:
    with session_factory() as s:
        st = _store(s)
        _order(s, woo_id="1200", number="BOPRIN-1200", woo_status="cancelled", store=st)
        _order(s, woo_id="1201", number="BOPRIN-1201", woo_status="processing", store=st)
        s.commit()
        sheet = FakeSheet([HEADER])
        summary = sync_to_sheet(s, sheet, build_drive_sync_rows(s))
    # Solo se inserta el activo; el cancelado no.
    assert summary["appended_rows"] == 1
    written = {r[KEY] for r in sheet.grid if KEY < len(r) and r[KEY]}
    assert "1201" in written
    assert "1200" not in written


def test_already_written_drive_row_is_not_touched_on_cancel(session_factory) -> None:
    with session_factory() as s:
        st = _store(s)
        o = _order(s, woo_id="1300", number="BOPRIN-1300", woo_status="processing", store=st)
        s.commit()
        sheet = FakeSheet([HEADER])
        sync_to_sheet(s, sheet, build_drive_sync_rows(s))   # se inserta
        before = [list(r) for r in sheet.grid]
        # Ahora se cancela.
        o = s.get(Order, o.id)
        o.woo_status = "cancelled"
        s.commit()
        # Nueva sincronización: only-insert + ya no es candidato → no toca nada.
        summary = sync_to_sheet(s, sheet, build_drive_sync_rows(s))
    assert summary["appended_rows"] == 0
    assert sheet.grid == before   # la fila ya escrita se queda intacta


# --- Parte D: separado de la exclusión manual --------------------------------------


def test_woo_status_is_separate_from_manual_exclusion(session_factory, http) -> None:
    with session_factory() as s:
        st = _store(s)
        cancelled = _order(s, woo_id="1400", number="BOPRIN-1400",
                           woo_status="cancelled", store=st)
        active = _order(s, woo_id="1401", number="BOPRIN-1401",
                        woo_status="processing", store=st)
        s.commit()
        cancelled_id, active_id = cancelled.id, active.id

    # «Reincluir» a mano el cancelado NO lo devuelve al seguimiento: sigue
    # oculto por estado (la exclusión manual y el estado son cosas distintas).
    r = http.post("/api/erp/seguimiento/include", json={"order_ids": [cancelled_id]},
                  headers=auth_headers(http, "pedidos"))
    assert r.status_code == 200
    with session_factory() as s:
        assert "BOPRIN-1400" not in _numbers(_rows_for(s, en_curso=True))
        o = s.get(Order, cancelled_id)
        assert o.woo_status == "cancelled"          # el estado no se tocó
        assert o.seguimiento_excluded_at is None

    # Excluir a mano un pedido activo NO le cambia el woo_status.
    r = http.post("/api/erp/seguimiento/exclude", json={"order_ids": [active_id]},
                  headers=auth_headers(http, "pedidos"))
    assert r.status_code == 200
    with session_factory() as s:
        o = s.get(Order, active_id)
        assert o.seguimiento_excluded_at is not None
        assert o.woo_status == "processing"         # el estado no se tocó
        # Excluido a mano → fuera; y aparece en «excluidos», no en «ocultos».
        assert _numbers(_rows_for(s, ver_excluidos=True)) == {"BOPRIN-1401"}
        assert "BOPRIN-1401" not in _numbers(_rows_for(s, ver_ocultos_estado=True))


# --- Parte A: auto-anular en la ingesta al reembolsar/cancelar ---------------


def _woo_payload(store, *, woo_id: int, number: str, status: str) -> dict:
    return {"id": woo_id, "number": number, "status": status, "total": "10",
            "currency": "EUR", "date_created": "2026-09-01T00:00:00Z",
            "billing": {"email": "a@b.com"}, "line_items": [], "meta_data": [],
            "_store_slug": store.account_id}


def test_import_refunded_autocancels_web_order(session_factory) -> None:
    """Un pedido web que pasa a `refunded` en Woo se auto-anula en BoHub y sale
    de las colas; el borrado del F_PCL se encola en factusol:writes."""
    with patch(
        "app.integrations.factusol.jobs.enqueue_autocancel_order_documents",
        return_value="job-x",
    ) as enq, session_factory() as s:
        st = _store(s)
        p = _woo_payload(st, woo_id=950, number="BOPRIN-950", status="processing")
        import_woo_order(s, store=st, woo_order=p)
        s.commit()
        import_woo_order(s, store=st, woo_order={**p, "status": "refunded"})
        s.commit()
        o = s.scalar(select(Order).where(Order.external_id == "950"))
        assert o.cancelled_at is not None
        assert o.woo_status == "refunded"
    enq.assert_called_once()


def test_import_still_processing_does_not_autocancel(session_factory) -> None:
    """Un reembolso PARCIAL deja el pedido en `processing`: NO se auto-anula."""
    with session_factory() as s:
        st = _store(s)
        p = _woo_payload(st, woo_id=951, number="BOPRIN-951", status="processing")
        import_woo_order(s, store=st, woo_order=p)
        s.commit()
        import_woo_order(s, store=st, woo_order={**p, "total": "8"})  # sigue processing
        s.commit()
        o = s.scalar(select(Order).where(Order.external_id == "951"))
        assert o.cancelled_at is None
