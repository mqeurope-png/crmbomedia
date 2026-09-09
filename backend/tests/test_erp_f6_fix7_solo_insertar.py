"""ERP-F6-fix7 (Parte B/B-bis/D) — la sincronización SOLO inserta.

- Un pedido que ya está en la hoja no se actualiza nunca (ni una celda).
- El emparejamiento sigue evitando duplicados.
- «Celda manual distinta» desaparece; «factura desconocida» es información.
- El bloque insertado va del más reciente (arriba) al más antiguo, de forma
  estable, y un pedido nuevo entra ENCIMA del lote anterior.
- No se escribe fuera del bloque insertado ni se añaden filas vacías al final.
"""
from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.base import Base
from app.erp import seguimiento as core
from app.erp.api.seguimiento import _rows
from app.erp.drive_sheets import GoogleSheetsClient, _row_is_blank, sync_to_sheet
from app.erp.models import Order, OrderSource
from app.erp.seguimiento import SEGUIMIENTO_COLUMNS
from app.models.crm import Company
from tests._test_helpers import seed_test_users

HEADER = list(SEGUIMIENTO_COLUMNS)
_IDX = {name: i for i, name in enumerate(SEGUIMIENTO_COLUMNS)}
KEY = _IDX["Albarán / Nº Pedido Web"]
SEP = ["^^^^  Aqui arriba pedidos que faltan entregar ."]
BAND = ["ARRIBA EN PROCESO"]
SECOND_HEADER = [
    "EMPRESA", "", "Cliente", "Vendedor", "OFI-TER-SAT", "Transport",
    "Preparado", "Recogido", "F Envio Factura", "Productos", "Proforma",
    "Albarán", "FACTURA",
]
# Fila del segundo encabezado (1-based) en `_real_sheet`: HEADER, 1 incidencia,
# SEP, [], BAND, SECOND_HEADER → fila 6.
SECOND_HEADER_ROW = 6


def _sheet_row(**cells: str) -> list[str]:
    out = [""] * len(HEADER)
    for name, value in cells.items():
        out[_IDX[name]] = value
    return out


def _real_sheet(reserved: int = 0, existing: list[list[str]] | None = None) -> list[list[str]]:
    grid = [HEADER, _sheet_row(Cliente="Incidencia"), SEP, [], BAND, SECOND_HEADER]
    grid += [[""] * len(HEADER) for _ in range(reserved)]
    grid += existing or []
    return grid


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


def _order(s: Session, number: str, *, cliente: str = "Cliente",
           placed: str = "2026-09-01", tracking: str | None = None) -> None:
    comp = Company(name=cliente)
    s.add(comp)
    s.flush()
    s.add(Order(
        order_number=number, external_source=OrderSource.WOOCOMMERCE,
        company_id=comp.id, tracking_number=tracking,
        placed_at=datetime.fromisoformat(placed).replace(tzinfo=UTC),
    ))
    s.flush()


def _rows_for_sync(s: Session) -> list[dict]:
    return core.filter_rows(_rows(s), en_curso=True, sort="fecha", direction="asc")


class RecordingSheet:
    def __init__(self, grid: list[list[str]]) -> None:
        self.grid = [list(r) for r in grid]
        self.calls: list[dict] = []
        self.cell_writes: list[tuple[int, int, str]] = []

    def get_values(self) -> list[list[str]]:
        return [list(r) for r in self.grid]

    def update_cells(self, updates: list[tuple[int, int, str]]) -> None:
        if not updates:
            return
        self.calls.append({"api": "values:batchUpdate", "cells": len(updates)})
        for row, col, value in updates:
            self.cell_writes.append((row, col, value))
            r = self.grid[row - 1]
            while len(r) <= col:
                r.append("")
            r[col] = value

    def insert_rows_at(self, row: int, count: int) -> None:
        self.calls.append({"api": "insertDimension", "row": row, "count": count})
        for _ in range(count):
            self.grid.insert(row - 1, [""] * len(HEADER))

    def write_rows(self, start_row: int, rows: list[list[str]]) -> None:
        self.calls.append({"api": "values.update", "rows": len(rows)})
        for i, values in enumerate(rows):
            while len(self.grid) <= start_row - 1 + i:
                self.grid.append([""] * len(HEADER))
            self.grid[start_row - 1 + i] = list(values)


def _key_at(sheet: RecordingSheet, idx: int) -> str:
    r = sheet.grid[idx]
    return r[KEY] if KEY < len(r) else ""


# --- Parte B: solo insertar --------------------------------------------------------


def test_existing_order_is_never_updated(session_factory) -> None:
    existing = _sheet_row(Cliente="Cliente Uno")  # celdas de fecha/empresa vacías
    existing[KEY] = "99895"
    sheet = RecordingSheet(_real_sheet(existing=[existing]))
    before = [list(r) for r in sheet.grid]
    with session_factory() as s:
        _order(s, "BOPRIN-99895", cliente="Cliente Uno", tracking="1Z-T")
        s.commit()
        summary = sync_to_sheet(s, sheet, _rows_for_sync(s))
    # Ninguna escritura para esa fila (ni para ninguna).
    assert sheet.cell_writes == []
    assert sheet.grid == before
    assert summary["appended_rows"] == 0
    assert summary["already_present"] == 1
    assert summary["updated_rows"] == 0


def test_existing_order_still_prevents_duplicate_insert(session_factory) -> None:
    existing = _sheet_row(Cliente="Cliente Uno")
    existing[KEY] = "99895"
    sheet = RecordingSheet(_real_sheet(reserved=3, existing=[existing]))
    with session_factory() as s:
        _order(s, "BOPRIN-99895", cliente="Cliente Uno")
        s.commit()
        sync_to_sheet(s, sheet, _rows_for_sync(s))
        # Segunda pasada: sigue sin duplicar.
        summary = sync_to_sheet(s, sheet, _rows_for_sync(s))
    assert summary["appended_rows"] == 0
    assert sum(1 for r in sheet.grid if (KEY < len(r) and r[KEY] == "99895")) == 1


def test_no_manual_cell_conflicts_reported(session_factory) -> None:
    existing = _sheet_row(Cliente="Cliente Uno", Tracking="A MANO")
    existing[KEY] = "99895"
    sheet = RecordingSheet(_real_sheet(existing=[existing]))
    with session_factory() as s:
        _order(s, "BOPRIN-99895", cliente="Cliente Uno", tracking="OTRO")
        s.commit()
        summary = sync_to_sheet(s, sheet, _rows_for_sync(s))
    assert not any(c.get("kind") == "manual_cell" for c in summary["conflicts"])
    assert sheet.grid[_find(sheet, "99895")][_IDX["Tracking"]] == "A MANO"


def test_sheet_invoice_unknown_is_informational_not_conflict(session_factory) -> None:
    existing = _sheet_row(Cliente="ARSA")
    existing[KEY] = "99887"
    existing[_IDX["Nº de Factura"]] = "260731"
    sheet = RecordingSheet(_real_sheet(existing=[existing]))
    with session_factory() as s:
        _order(s, "BOPRIN-99887", cliente="ARSA")   # BoHub no tiene esa factura
        s.commit()
        summary = sync_to_sheet(s, sheet, _rows_for_sync(s))
    assert len(summary["unknown_invoices"]) == 1
    assert summary["unknown_invoices"][0]["kind"] == "sheet_invoice_unknown"
    # No entra en «a revisar» ni en conflictos.
    assert summary["orders_to_review"] == 0
    assert summary["conflicts"] == []


def test_preview_separates_info_from_conflicts(session_factory) -> None:
    # Una factura desconocida (info) + una contradicción (a revisar) + un nuevo.
    known = _sheet_row(Cliente="ARSA")
    known[KEY] = "99887"
    known[_IDX["Nº de Factura"]] = "260731"
    contra = _sheet_row(Cliente="FLUXCLIENTE")
    contra[KEY] = "5742"
    contra[_IDX["Nº de Factura"]] = "111"
    sheet = RecordingSheet(_real_sheet(reserved=2, existing=[known, contra]))
    with session_factory() as s:
        _order(s, "BOPRIN-99887", cliente="ARSA")                     # info
        _order(s, "BOPRIN-5742", cliente="OTRO", placed="2026-09-02")  # contradicción
        _order(s, "ARTISJ-9999", cliente="NUEVO", placed="2026-09-03")  # añade
        s.commit()
        preview = sync_to_sheet(s, sheet, _rows_for_sync(s), dry_run=True)
    assert preview["appended_rows"] == 1
    assert len(preview["unknown_invoices"]) == 1
    review_kinds = {i["kind"] for g in preview["review_groups"] for i in g["items"]}
    assert "contradicted" in review_kinds
    assert "sheet_invoice_unknown" not in review_kinds   # la info NO va aquí


def test_no_write_touches_rows_outside_inserted_range(session_factory) -> None:
    hist = []
    for i in range(8000):
        r = _sheet_row(Cliente=f"H{i}")
        r[KEY] = str(600000 + i)
        hist.append(r)
    sheet = RecordingSheet(_real_sheet(reserved=0, existing=hist))
    with session_factory() as s:
        for i in range(3):
            _order(s, f"ARTISJ-9{i:03d}", cliente=f"Nuevo {i}", placed=f"2026-09-0{i+1}")
        s.commit()
        sync_to_sheet(s, sheet, _rows_for_sync(s))
    # Se insertan 3 filas justo bajo el 2º encabezado; ninguna escritura cae en
    # una fila del histórico (que quedó desplazado 3 hacia abajo).
    inserted = set(range(SECOND_HEADER_ROW + 1, SECOND_HEADER_ROW + 4))
    assert {r for r, _, _ in sheet.cell_writes} <= inserted
    assert not any(c["api"] == "values.update" for c in sheet.calls)


def test_no_trailing_empty_rows_appended(session_factory) -> None:
    existing = _sheet_row(Cliente="Ya")
    existing[KEY] = "500000"
    sheet = RecordingSheet(_real_sheet(reserved=0, existing=[existing]))
    before_len = len(sheet.grid)
    with session_factory() as s:
        _order(s, "ARTISJ-9999", cliente="Nuevo")
        s.commit()
        sync_to_sheet(s, sheet, _rows_for_sync(s))
    assert len(sheet.grid) == before_len + 1
    assert not _row_is_blank(sheet.grid[-1])
    # Ninguna escritura referencia una fila más allá de la última con datos.
    assert max(r for r, _, _ in sheet.cell_writes) <= len(sheet.grid)


def test_inserted_row_format_is_not_inherited() -> None:
    captured: list[dict] = []
    client = GoogleSheetsClient({"client_email": "x", "private_key": "y"}, "sid")
    client._sheet_id, client._sheet_title = 0, "Hoja"
    client._request = lambda m, p, **k: (captured.append({"json": k.get("json")}) or {})  # type: ignore[method-assign,assignment]
    client.insert_rows_at(10, 2)
    req = captured[0]["json"]["requests"][0]["insertDimension"]
    assert req["inheritFromBefore"] is False


# --- Parte B-bis: orden del bloque (más reciente arriba) ---------------------------


def test_inserted_block_is_newest_first(session_factory) -> None:
    sheet = RecordingSheet(_real_sheet(reserved=5))
    with session_factory() as s:
        _order(s, "ARTISJ-1", cliente="Vieja", placed="2026-08-05")
        _order(s, "ARTISJ-2", cliente="Media", placed="2026-08-20")
        _order(s, "ARTISJ-3", cliente="Nueva", placed="2026-09-09")
        s.commit()
        sync_to_sheet(s, sheet, _rows_for_sync(s))
    # Bajo el 2º encabezado (índice 0-based = SECOND_HEADER_ROW): lo más nuevo.
    assert _key_at(sheet, SECOND_HEADER_ROW) == "3"       # 9/9 arriba
    assert _key_at(sheet, SECOND_HEADER_ROW + 1) == "2"   # 20/8
    assert _key_at(sheet, SECOND_HEADER_ROW + 2) == "1"   # 5/8 abajo


def test_insertion_order_is_stable_on_date_ties(session_factory) -> None:
    # Números ≥4 dígitos (los reales) para que el emparejamiento reconozca las
    # filas ya escritas en la segunda pasada y no duplique.
    sheet = RecordingSheet(_real_sheet(reserved=5))
    with session_factory() as s:
        for n in ("ARTISJ-9030", "ARTISJ-9010", "ARTISJ-9020"):
            _order(s, n, cliente=n, placed="2026-09-01")  # misma fecha
        s.commit()
        sync_to_sheet(s, sheet, _rows_for_sync(s))
        first = [_key_at(sheet, SECOND_HEADER_ROW + i) for i in range(3)]
        # A igualdad de fecha, orden estable por referencia (asc).
        assert first == ["9010", "9020", "9030"]
        # Reejecutar (mismos pedidos): ya están → no se reordena ni se duplica.
        sync_to_sheet(s, sheet, _rows_for_sync(s))
        assert [_key_at(sheet, SECOND_HEADER_ROW + i) for i in range(3)] == first


def test_new_order_inserted_above_previous_batch(session_factory) -> None:
    sheet = RecordingSheet(_real_sheet(reserved=0))
    with session_factory() as s:
        _order(s, "ARTISJ-9001", cliente="Lote1", placed="2026-09-01")
        s.commit()
        sync_to_sheet(s, sheet, _rows_for_sync(s))
        # Llega un pedido MÁS NUEVO al día siguiente.
        _order(s, "ARTISJ-9002", cliente="Lote2", placed="2026-09-02")
        s.commit()
        sync_to_sheet(s, sheet, _rows_for_sync(s))
    # El nuevo (9002) queda ENCIMA del lote anterior (9001), que no se duplica.
    assert _key_at(sheet, SECOND_HEADER_ROW) == "9002"
    assert _key_at(sheet, SECOND_HEADER_ROW + 1) == "9001"
    assert sum(1 for r in sheet.grid if (KEY < len(r) and r[KEY] == "9001")) == 1


def test_handwritten_numeric_date_survives(session_factory) -> None:
    existing = _sheet_row(Cliente="Cliente Uno")
    existing[KEY] = "99887"
    existing[_IDX["Fecha entrada albarán"]] = "29082022"   # fecha a mano como nº
    sheet = RecordingSheet(_real_sheet(existing=[existing]))
    with session_factory() as s:
        _order(s, "BOPRIN-99887", cliente="Cliente Uno")
        s.commit()
        sync_to_sheet(s, sheet, _rows_for_sync(s))
    idx = _find(sheet, "99887")
    assert sheet.grid[idx][_IDX["Fecha entrada albarán"]] == "29082022"
    assert not any((r - 1) == idx for r, _, _ in sheet.cell_writes)


def _find(sheet: RecordingSheet, ref: str) -> int:
    for i, r in enumerate(sheet.grid):
        if KEY < len(r) and r[KEY] == ref:
            return i
    raise AssertionError(f"no está la fila {ref}")
