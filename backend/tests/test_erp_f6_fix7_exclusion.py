"""ERP-F6-fix7 (Parte C/D) — excluir pedidos del seguimiento.

- Excluido: no se lista, no se inserta, no se cuenta; su fila en la hoja se
  queda intacta. Reversible; registra quién/cuándo/motivo; no toca el pedido
  ni FACTUSOL.
- «Escrito en Drive» es distinto de «excluido»: escribirse marca el pedido como
  ya escrito (sale de «pendiente de escribir») pero NO lo saca de la vista.
"""
from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.base import Base
from app.db.session import get_session
from app.erp.api.seguimiento import _rows, build_drive_sync_rows
from app.erp.drive_sheets import sync_to_sheet
from app.erp.models import InvoiceStatus, Order, OrderSource
from app.erp.seguimiento import SEGUIMIENTO_COLUMNS
from app.main import app
from app.models.crm import Company
from tests._test_helpers import auth_headers, seed_test_users

HEADER = list(SEGUIMIENTO_COLUMNS)
_IDX = {name: i for i, name in enumerate(SEGUIMIENTO_COLUMNS)}
KEY = _IDX["Albarán / Nº Pedido Web"]


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


def _order(s: Session, number: str, *, cliente: str = "Cliente") -> str:
    comp = Company(name=cliente)
    s.add(comp)
    s.flush()
    o = Order(
        order_number=number, external_source=OrderSource.WOOCOMMERCE,
        company_id=comp.id, placed_at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    s.add(o)
    s.flush()
    return o.id


def _rows_for_sync(s: Session) -> list[dict]:
    return build_drive_sync_rows(s)


class FakeSheet:
    def __init__(self, grid: list[list[str]]) -> None:
        self.grid = [list(r) for r in grid]

    def get_values(self) -> list[list[str]]:
        return [list(r) for r in self.grid]

    def update_cells(self, updates) -> None:
        for row, col, value in updates:
            r = self.grid[row - 1]
            while len(r) <= col:
                r.append("")
            r[col] = value

    def insert_rows_at(self, row: int, count: int) -> None:
        for _ in range(count):
            self.grid.insert(row - 1, [""] * len(HEADER))

    def write_rows(self, start_row: int, rows) -> None:  # pragma: no cover
        for i, values in enumerate(rows):
            self.grid[start_row - 1 + i] = list(values)


def _bare_sheet() -> list[list[str]]:
    return [HEADER]


def _list(http, **params) -> dict:
    r = http.get("/api/erp/seguimiento", params=params, headers=auth_headers(http, "user"))
    assert r.status_code == 200, r.text
    return r.json()


# --- exclusión: lo básico ----------------------------------------------------------


def test_excluded_order_not_listed_not_written(session_factory, http) -> None:
    with session_factory() as s:
        oid = _order(s, "BOPRIN-70001", cliente="Excluir SL")
        s.commit()
    r = http.post("/api/erp/seguimiento/exclude", json={"order_ids": [oid]},
                  headers=auth_headers(http, "pedidos"))
    assert r.status_code == 200 and r.json()["excluded"] == 1
    # No se lista.
    assert _list(http)["total"] == 0
    # No se escribe: no está entre las filas candidatas del sync.
    with session_factory() as s:
        assert build_drive_sync_rows(s) == []
        sheet = FakeSheet(_bare_sheet())
        summary = sync_to_sheet(s, sheet, _rows_for_sync(s))
    assert summary["appended_rows"] == 0
    assert len(sheet.grid) == 1   # solo la cabecera


def test_excluded_order_row_in_sheet_is_left_untouched(session_factory, http) -> None:
    row = [""] * len(HEADER)
    row[KEY] = "70002"
    row[_IDX["Cliente"]] = "Excluir SL"
    with session_factory() as s:
        oid = _order(s, "BOPRIN-70002", cliente="Excluir SL")
        s.commit()
    http.post("/api/erp/seguimiento/exclude", json={"order_ids": [oid]},
              headers=auth_headers(http, "pedidos"))
    sheet = FakeSheet([HEADER, row])
    before = [list(r) for r in sheet.grid]
    with session_factory() as s:
        sync_to_sheet(s, sheet, _rows_for_sync(s))
    # La fila que había en la hoja se queda EXACTAMENTE igual (nada se borra).
    assert sheet.grid == before


def test_bulk_exclude_from_list(session_factory, http) -> None:
    with session_factory() as s:
        ids = [_order(s, f"BOPRIN-7010{i}", cliente=f"C{i}") for i in range(3)]
        s.commit()
    r = http.post("/api/erp/seguimiento/exclude",
                  json={"order_ids": ids, "reason": "migrados a mano"},
                  headers=auth_headers(http, "pedidos"))
    assert r.status_code == 200 and r.json()["excluded"] == 3
    assert _list(http)["total"] == 0
    # Se ven en la vista de excluidos.
    assert _list(http, ver_excluidos=True)["total"] == 3


def test_exclusion_is_reversible(session_factory, http) -> None:
    with session_factory() as s:
        oid = _order(s, "BOPRIN-70200", cliente="Vuelve SL")
        s.commit()
    http.post("/api/erp/seguimiento/exclude", json={"order_ids": [oid]},
              headers=auth_headers(http, "pedidos"))
    assert _list(http)["total"] == 0
    r = http.post("/api/erp/seguimiento/include", json={"order_ids": [oid]},
                  headers=auth_headers(http, "pedidos"))
    assert r.status_code == 200 and r.json()["included"] == 1
    assert _list(http)["total"] == 1
    with session_factory() as s:
        o = s.scalar(select(Order).where(Order.order_number == "BOPRIN-70200"))
        assert o.seguimiento_excluded_at is None
        assert o.seguimiento_excluded_by_user_id is None
        assert o.seguimiento_excluded_reason is None


def test_exclusion_records_user_and_timestamp(session_factory, http) -> None:
    with session_factory() as s:
        oid = _order(s, "BOPRIN-70300", cliente="Traza SL")
        s.commit()
    http.post("/api/erp/seguimiento/exclude",
              json={"order_ids": [oid], "reason": "duplicado antiguo"},
              headers=auth_headers(http, "pedidos"))
    with session_factory() as s:
        o = s.scalar(select(Order).where(Order.order_number == "BOPRIN-70300"))
        assert o.seguimiento_excluded_at is not None
        assert o.seguimiento_excluded_by_user_id is not None   # quién
        assert o.seguimiento_excluded_reason == "duplicado antiguo"


# --- «escrito en Drive» ≠ «excluido» ----------------------------------------------


def test_written_to_drive_marks_pending_not_excluded(session_factory, http) -> None:
    with session_factory() as s:
        _order(s, "BOPRIN-70400", cliente="Escrita SL")
        s.commit()
    # Antes de escribir: está pendiente de escribir.
    assert _list(http, pendiente_escribir=True)["total"] == 1
    # Se escribe en la hoja (inserción real).
    with session_factory() as s:
        sheet = FakeSheet(_bare_sheet())
        summary = sync_to_sheet(s, sheet, _rows_for_sync(s))
    assert summary["appended_rows"] == 1
    # Tras escribir: YA no está pendiente…
    assert _list(http, pendiente_escribir=True)["total"] == 0
    # …pero NO está excluido y sigue en la vista por defecto.
    assert _list(http)["total"] == 1
    assert _list(http, ver_excluidos=True)["total"] == 0
    with session_factory() as s:
        o = s.scalar(select(Order).where(Order.order_number == "BOPRIN-70400"))
        assert o.seguimiento_excluded_at is None


def test_default_view_still_shows_written_orders(session_factory, http) -> None:
    with session_factory() as s:
        _order(s, "BOPRIN-70500", cliente="Sigue SL")
        s.commit()
        sheet = FakeSheet(_bare_sheet())
        sync_to_sheet(s, sheet, _rows_for_sync(s))
    data = _list(http)
    assert data["total"] == 1
    assert data["items"][0]["order_number"] == "BOPRIN-70500"
    assert data["items"][0]["escrito_drive"] is True
    assert data["items"][0]["pendiente_escribir"] is False


def test_exclusion_does_not_touch_order_or_factusol(session_factory, http) -> None:
    with session_factory() as s:
        oid = _order(s, "BOPRIN-70600", cliente="Intacta SL")
        o = s.get(Order, oid)
        o.factusol_invoice_number = "5-260999"
        o.invoice_status = InvoiceStatus.GENERATED
        s.commit()
        snapshot = (o.factusol_invoice_number, o.invoice_status,
                    o.preparation_status, o.transport_status, o.payment_status)
    http.post("/api/erp/seguimiento/exclude", json={"order_ids": [oid]},
              headers=auth_headers(http, "pedidos"))
    with session_factory() as s:
        o = s.get(Order, oid)
        assert (o.factusol_invoice_number, o.invoice_status,
                o.preparation_status, o.transport_status,
                o.payment_status) == snapshot
        assert o.seguimiento_excluded_at is not None   # solo la exclusión cambió


def test_exclude_requires_edit_role(session_factory, http) -> None:
    with session_factory() as s:
        oid = _order(s, "BOPRIN-70700", cliente="Perm SL")
        s.commit()
    # Un usuario de solo lectura no puede excluir.
    r = http.post("/api/erp/seguimiento/exclude", json={"order_ids": [oid]},
                  headers=auth_headers(http, "user"))
    assert r.status_code in (401, 403)


def test_view_helpers_flag_written_and_pending(session_factory) -> None:
    # build_rows marca correctamente pendiente/escrito/excluido.
    with session_factory() as s:
        _order(s, "BOPRIN-70800", cliente="Flags SL")
        s.commit()
        rows = _rows(s)
        assert rows[0]["pendiente_escribir"] is True
        assert rows[0]["escrito_drive"] is False
        assert rows[0]["excluido"] is False
