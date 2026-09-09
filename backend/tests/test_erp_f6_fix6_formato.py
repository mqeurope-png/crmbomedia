"""ERP-F6-fix6 — la sincronización solo escribe las celdas que cambian y jamás
manda formato, para dejar de reformatear el histórico de la hoja de Bart.

Antecedente (medido en producción, no deducido del código): la sincronización
«actual» reformateaba 7.849 celdas en 3.230 filas del histórico —sobre todo las
columnas de fecha—, y las celdas con un número incompatible (fechas escritas a
mano como `29082022`, o un `53` suelto) quedaban corrompidas al reinterpretarse
como fechas. El mecanismo: reenviar rangos completos hacía que Google Sheets
reinfiriera el formato de cada celda reenviada.

La corrección: TODA escritura pasa por `update_cells` como rangos de UNA celda
(un solo `values:batchUpdate`, `valueInputOption=RAW`), nunca una fila/columna
completa; ninguna llamada lleva `userEnteredFormat`/`repeatCell`; la inserción
de filas no hereda formato (`inheritFromBefore=False`); y no se añaden filas
vacías al final. Estos tests fijan ese contrato a nivel de PAYLOAD.
"""
from __future__ import annotations

import json
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
from app.erp.drive_sheets import GoogleSheetsClient, _col_a1, _row_is_blank, sync_to_sheet
from app.erp.models import Order, OrderSource
from app.erp.seguimiento import SEGUIMIENTO_COLUMNS
from app.models.crm import Company
from app.models.integration_settings import (
    ExternalSystem,
    IntegrationAccount,
    IntegrationMode,
)
from tests._test_helpers import auth_headers, seed_test_users

HEADER = list(SEGUIMIENTO_COLUMNS)
_IDX = {name: i for i, name in enumerate(SEGUIMIENTO_COLUMNS)}
SEP = ["^^^^  Aqui arriba pedidos que faltan entregar ."]
BAND = ["ARRIBA EN PROCESO"]
SECOND_HEADER = [
    "EMPRESA", "", "Cliente", "Vendedor", "OFI-TER-SAT", "Transport",
    "Preparado", "Recogido", "F Envio Factura", "Productos", "Proforma",
    "Albarán", "FACTURA",
]


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


# --- infraestructura --------------------------------------------------------------


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

    from app.db.session import get_session
    from app.main import app

    def override():
        with session_factory() as s:
            yield s
    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _store(s: Session, slug: str) -> str:
    acc = IntegrationAccount(system=ExternalSystem.WOOCOMMERCE, account_id=slug,
                             display_name=slug, mode=IntegrationMode.LIVE)
    s.add(acc)
    s.flush()
    return acc.id


def _store_series(http, mapping: dict[str, str]) -> None:
    r = http.patch("/api/erp/settings", json={"factusol_series_by_source": mapping},
                   headers=auth_headers(http, "admin"))
    assert r.status_code == 200, r.text


def _order(s: Session, number: str, *, cliente: str = "Cliente", store_id: str | None = None,
           tracking: str | None = None, serial: str | None = None) -> None:
    comp = Company(name=cliente)
    s.add(comp)
    s.flush()
    s.add(Order(
        order_number=number, external_source=OrderSource.WOOCOMMERCE,
        company_id=comp.id, store_id=store_id, tracking_number=tracking,
        serial_number=serial, placed_at=datetime(2026, 9, 1, tzinfo=UTC),
    ))
    s.flush()


def _rows_for_sync(s: Session) -> list[dict]:
    return core.filter_rows(_rows(s), en_curso=True, sort="fecha", direction="asc")


class RecordingSheet:
    """Hoja en memoria que además REGISTRA cada llamada del transporte con la
    misma forma que `GoogleSheetsClient.calls`, para poder afirmar sobre el
    payload exacto que recibiría la API (celdas enviadas, inserciones, etc.)."""

    def __init__(self, grid: list[list[str]]) -> None:
        self.grid = [list(r) for r in grid]
        self.calls: list[dict] = []
        self.cell_writes: list[tuple[int, int, str]] = []

    def reset_recorder(self) -> None:
        self.calls.clear()
        self.cell_writes.clear()

    def get_values(self) -> list[list[str]]:
        self.calls.append({"method": "GET", "api": "values.get"})
        return [list(r) for r in self.grid]

    def update_cells(self, updates: list[tuple[int, int, str]]) -> None:
        if not updates:
            return
        self.calls.append({
            "method": "POST", "api": "values:batchUpdate", "cells": len(updates),
            "ranges": [f"{_col_a1(col)}{row}" for row, col, _ in updates],
        })
        for row, col, value in updates:
            self.cell_writes.append((row, col, value))
            r = self.grid[row - 1]
            while len(r) <= col:
                r.append("")
            r[col] = value

    def insert_rows_at(self, row: int, count: int) -> None:
        self.calls.append({
            "method": "POST", "api": "batchUpdate:insertDimension",
            "range": f"ROWS {row}..{row + count - 1}", "inheritFromBefore": False,
        })
        for _ in range(count):
            self.grid.insert(row - 1, [""] * len(HEADER))

    def write_rows(self, start_row: int, rows: list[list[str]]) -> None:
        # La sincronización de fix6 NO debe llamar aquí (era el volcado de rango
        # completo que reformateaba). Se registra para poder AFIRMAR su ausencia.
        self.calls.append({"method": "PUT", "api": "values.update",
                           "start": start_row, "rows": len(rows)})
        for i, values in enumerate(rows):
            while len(self.grid) <= start_row - 1 + i:
                self.grid.append([""] * len(HEADER))
            self.grid[start_row - 1 + i] = list(values)


def _batch_cell_total(sheet: RecordingSheet) -> int:
    return sum(c.get("cells", 0) for c in sheet.calls if c["api"] == "values:batchUpdate")


def _used_full_range(sheet: RecordingSheet) -> bool:
    return any(c["api"] == "values.update" for c in sheet.calls)


# --- test 1: solo se envían las celdas que cambian --------------------------------


def test_only_changed_cells_are_sent(session_factory, http) -> None:
    """Hoja con 100 filas de histórico; cambian exactamente 3 celdas de UNA
    fila. El payload contiene esas 3 celdas y ninguna más — nunca la fila ni el
    rango completo, ni las otras 99 filas."""
    _store_series(http, {"boprint": "5"})
    # 100 filas de histórico que NO casan con ningún pedido de BoHub.
    noise = []
    for i in range(100):
        r = _sheet_row(Cliente=f"Historico {i}", Preparado="15/01/2020")
        r[_IDX["Albarán / Nº Pedido Web"]] = str(700000 + i)
        noise.append(r)
    # Una fila que SÍ casa (número + cliente), con Empresa/Tracking/Serie vacíos.
    target = _sheet_row(Cliente="Cliente Uno")
    target[_IDX["Albarán / Nº Pedido Web"]] = "99895"
    sheet = RecordingSheet(_real_sheet(existing=[*noise, target]))

    with session_factory() as s:
        store = _store(s, "boprint")
        _order(s, "BOPRIN-99895", cliente="Cliente Uno", store_id=store,
               tracking="1Z-TRACK", serial="FBAP-SERIE")
        s.commit()

        # 1ª sincronización: rellena las celdas vacías de la fila que casa.
        sync_to_sheet(s, sheet, _rows_for_sync(s))
        first = [(r, c) for r, c, _ in sheet.cell_writes]
        assert len(first) >= 3, first
        # Ninguna de las 100 filas de ruido se tocó.
        noise_rows = set(range(7, 7 + 100))  # 1-based, tras la estructura
        assert not (set(r for r, _ in first) & noise_rows)

        # Vaciamos 3 de esas celdas y volvemos a sincronizar.
        chosen = first[:3]
        for r, c in chosen:
            sheet.grid[r - 1][c] = ""
        sheet.reset_recorder()
        sync_to_sheet(s, sheet, _rows_for_sync(s))

    # Exactamente esas 3 celdas, ni una más; y NUNCA por rango completo.
    assert sorted((r, c) for r, c, _ in sheet.cell_writes) == sorted(chosen)
    assert _batch_cell_total(sheet) == 3
    assert not _used_full_range(sheet)


# --- test 2: ninguna llamada lleva formato ----------------------------------------


def _capture_client() -> tuple[GoogleSheetsClient, list[dict]]:
    captured: list[dict] = []
    client = GoogleSheetsClient({"client_email": "x", "private_key": "y"}, "sid")
    client._sheet_id, client._sheet_title = 0, "Hoja"  # sin descubrimiento remoto

    def fake_request(method, path, **kwargs):
        captured.append({"method": method, "path": path, "json": kwargs.get("json")})
        return {}

    client._request = fake_request  # type: ignore[method-assign]
    return client, captured


def test_payload_never_contains_format() -> None:
    client, captured = _capture_client()
    client.update_cells([(5, 10, "53"), (6, 2, "texto")])
    client.insert_rows_at(8, 2)
    assert captured, "no se registró ninguna llamada"
    for call in captured:
        blob = json.dumps(call["json"] or {})
        assert "userEnteredFormat" not in blob
        assert "repeatCell" not in blob
        assert "updateCells" not in blob
        assert "format" not in call["path"].lower()
    # El batchUpdate de valores solo lleva rango + valores, con RAW.
    batch = next(c for c in captured if "values:batchUpdate" in c["path"])
    assert batch["json"]["valueInputOption"] == "RAW"
    for item in batch["json"]["data"]:
        assert set(item.keys()) == {"range", "values"}


# --- test 3: la inserción de filas no hereda formato ------------------------------


def test_row_insertion_does_not_inherit_format() -> None:
    client, captured = _capture_client()
    client.insert_rows_at(12, 3)
    req = captured[0]["json"]["requests"][0]["insertDimension"]
    assert req["inheritFromBefore"] is False
    assert req["range"]["dimension"] == "ROWS"


# --- test 4: una celda numérica que no se toca conserva su formato ----------------


def test_untouched_numeric_cell_keeps_general_format(session_factory) -> None:
    """Un `53` suelto en una columna de fechas de una fila que casa: BoHub no
    tiene esa fecha, así que la celda NI se reescribe (su valor y su formato
    quedan intactos)."""
    row = _sheet_row(Cliente="Cliente Uno", Preparado="53")
    row[_IDX["Albarán / Nº Pedido Web"]] = "99895"
    sheet = RecordingSheet(_real_sheet(existing=[row]))
    prep_col = _IDX["Preparado"]
    with session_factory() as s:
        _order(s, "BOPRIN-99895", cliente="Cliente Uno")  # sin fecha «Preparado»
        s.commit()
        sync_to_sheet(s, sheet, _rows_for_sync(s))
    idx = next(i for i, r in enumerate(sheet.grid)
               if len(r) > _IDX["Albarán / Nº Pedido Web"]
               and r[_IDX["Albarán / Nº Pedido Web"]] == "99895")
    # Valor intacto…
    assert sheet.grid[idx][prep_col] == "53"
    # …y jamás se envió esa celda (por eso Sheets no reinfiere su formato).
    assert not any(c == prep_col and (r - 1) == idx for r, c, _ in sheet.cell_writes)


# --- test 5: una fecha escrita a mano como número sobrevive -----------------------


def test_handwritten_numeric_date_survives(session_factory) -> None:
    """Caso real de Bart: `29082022` (= 29/08/2022) escrito a mano en «Fecha
    entrada albarán». La sincronización no lo toca, así que sigue siendo
    `29082022` y no se reinterpreta como fecha (que daría `#VALUE!`)."""
    row = _sheet_row(Cliente="Cliente Uno")
    row[_IDX["Fecha entrada albarán"]] = "29082022"
    row[_IDX["Albarán / Nº Pedido Web"]] = "99887"
    sheet = RecordingSheet(_real_sheet(existing=[row]))
    fecha_col = _IDX["Fecha entrada albarán"]
    with session_factory() as s:
        _order(s, "BOPRIN-99887", cliente="Cliente Uno")
        s.commit()
        sync_to_sheet(s, sheet, _rows_for_sync(s))
    idx = next(i for i, r in enumerate(sheet.grid)
               if len(r) > _IDX["Albarán / Nº Pedido Web"]
               and r[_IDX["Albarán / Nº Pedido Web"]] == "99887")
    assert sheet.grid[idx][fecha_col] == "29082022"
    assert not any(c == fecha_col and (r - 1) == idx for r, c, _ in sheet.cell_writes)


# --- test 6: no se añaden filas vacías al final -----------------------------------


def test_no_trailing_empty_rows_appended(session_factory) -> None:
    """Un pedido nuevo se inserta como UNA fila con datos; no queda ninguna fila
    vacía al final ni se envía ningún rango más allá de la última fila con
    datos."""
    existing = _sheet_row(Cliente="Ya")
    existing[_IDX["Albarán / Nº Pedido Web"]] = "500000"
    sheet = RecordingSheet(_real_sheet(reserved=0, existing=[existing]))
    before_len = len(sheet.grid)
    with session_factory() as s:
        _order(s, "BOPRIN-9999", cliente="Nuevo")
        s.commit()
        summary = sync_to_sheet(s, sheet, _rows_for_sync(s))
    assert summary["appended_rows"] == 1
    # Exactamente una fila más, y la última no es una fila vacía.
    assert len(sheet.grid) == before_len + 1
    assert not _row_is_blank(sheet.grid[-1])
    # Nunca por rango completo, y ningún cell-write cae fuera de la rejilla.
    assert not _used_full_range(sheet)
    max_written = max((r for r, _, _ in sheet.cell_writes), default=0)
    assert max_written <= len(sheet.grid)


# --- test 7: la traza de la API queda registrada ----------------------------------


def test_api_trace_is_recorded_in_summary(session_factory) -> None:
    """El resumen incluye la traza compacta de llamadas a la API (método +
    endpoint + tamaño) para poder auditar qué se envió."""
    row = _sheet_row(Cliente="Cliente Uno")
    row[_IDX["Albarán / Nº Pedido Web"]] = "99895"

    captured: list[dict] = []
    client = GoogleSheetsClient({"client_email": "x", "private_key": "y"}, "sid")
    client._sheet_id, client._sheet_title = 0, "Hoja"
    grid = _real_sheet(existing=[row])

    def fake_request(method, path, **kwargs):
        captured.append({"method": method, "path": path})
        if method == "GET":
            return {"values": grid}
        return {}

    client._request = fake_request  # type: ignore[method-assign]
    with session_factory() as s:
        _order(s, "BOPRIN-99895", cliente="Cliente Uno", tracking="1Z-T")
        s.commit()
        summary = sync_to_sheet(s, client, _rows_for_sync(s))
    trace = summary["api_trace"]
    assert any(e["api"] == "values.get" for e in trace)
    assert any(e["api"] == "values:batchUpdate" for e in trace)
    # La traza no arrastra los rangos completos (solo una muestra) ni valores.
    for e in trace:
        assert "values" not in e
