"""ERP-F6-fix1 — detección tolerante de la cabecera de la hoja de Drive.

La hoja real de Bart no usa los nombres canónicos: `Nº` frente a `Núm`,
mayúsculas cambiantes (`TRACKING`, `FACTURA`), tildes ausentes
(`Fecha entrada albaran`), `f` por Empresa, y una cabecera repetida a media
hoja con nombres distintos (`Albarán`, `FACTURA`). La sincronización debe
reconocerla, omitir columnas opcionales que falten, tratar la cabecera
repetida como estructura, y —si falta la columna clave— fallar diciendo qué
falta y enseñando la cabecera leída.
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
from app.erp.drive_sheets import DriveSyncError, sync_to_sheet
from app.erp.models import Order, OrderSource
from app.erp.seguimiento import (
    KEY_COLUMN,
    KEY_COLUMN_INDEX,
    SEGUIMIENTO_COLUMNS,
    is_structure_row,
    match_header_columns,
    normalize_header,
)
from app.models.crm import Company
from tests._test_helpers import seed_test_users

# --- cabeceras literales del fichero real de Bart ----------------------------------

BART_TOP_HEADER = [
    "f", "Fecha entrada albaran", "Cliente", "Vendedor", "OFI-TER-SAT",
    "Transport", "Preparado", "Recogido", "F Envio Factura", "Productos",
    "Proforma", "Albarán / Núm Pedido WEb", "Núm de Factura", "TRACKING",
    "NÚMERO DE SERIE", "WHITERIP", "ORDEN",
]
# La cabecera intermedia (tras el separador ^^^^): nombres realmente distintos
# y solo hasta la columna de factura.
BART_MIDDLE_HEADER = [
    "EMPRESA", "", "Cliente", "Vendedor", "OFI-TER-SAT", "Transport",
    "Preparado", "Recogido", "F Envio Factura", "Productos", "Proforma",
    "Albarán", "FACTURA",
]


# --- Parte A: normalización tolerante ----------------------------------------------


def test_header_matching_ignores_accents_and_case() -> None:
    # «Albarán / Núm Pedido WEb» casa con la columna de albarán/pedido web.
    cmap = match_header_columns(BART_TOP_HEADER)
    assert cmap[KEY_COLUMN_INDEX] == 11
    assert normalize_header("Albarán / Núm Pedido WEb") == normalize_header(KEY_COLUMN)
    # Tildes ausentes y mayúsculas no importan.
    assert normalize_header("F Envio Factura") == normalize_header("F Envío Factura")
    assert normalize_header("TRACKING") == normalize_header("Tracking")


def test_header_matching_treats_num_variants_as_equal() -> None:
    base = normalize_header("Albarán / Nº Pedido Web")
    for variant in ("Núm", "N°", "No.", "num", "numero", "Número"):
        got = normalize_header(f"Albarán / {variant} Pedido Web")
        assert got == base, (variant, got, base)
    # Y una «n» suelta también cuenta como «número».
    assert normalize_header("N de Serie") == normalize_header("Núm de Serie")


def test_header_matching_accepts_aliases() -> None:
    # EMPRESA y f → misma columna lógica (Empresa, índice 0).
    assert match_header_columns(["EMPRESA"])[0] == 0
    assert match_header_columns(["f"])[0] == 0
    # FACTURA y «Núm de Factura» → columna de factura.
    fac = SEGUIMIENTO_COLUMNS.index("Nº de Factura")
    assert match_header_columns(["FACTURA"]).get(fac) == 0
    assert match_header_columns(["Núm de Factura"]).get(fac) == 0
    # «Albarán» a secas → la columna clave.
    assert match_header_columns(["Albarán"]).get(KEY_COLUMN_INDEX) == 0


def test_real_bart_header_row_is_fully_recognised() -> None:
    # La cabecera literal de arriba, tal cual (tildes ausentes incluidas):
    # las 17 columnas lógicas deben reconocerse, cada una en su sitio.
    cmap = match_header_columns(BART_TOP_HEADER)
    assert cmap == {i: i for i in range(len(SEGUIMIENTO_COLUMNS))}


# --- infraestructura para las pruebas de sincronización ----------------------------


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


def _order(s: Session, number: str, *, cliente: str, tracking: str | None = None,
           serial: str | None = None) -> None:
    comp = Company(name=cliente)
    s.add(comp)
    s.flush()
    s.add(Order(
        order_number=number, external_source=OrderSource.MANUAL,
        company_id=comp.id, tracking_number=tracking, serial_number=serial,
        placed_at=datetime(2026, 9, 1, tzinfo=UTC),
    ))
    s.flush()


def _rows_for_sync(s: Session) -> list[dict]:
    return core.filter_rows(_rows(s), en_curso=True, sort="fecha", direction="asc")


class FakeSheet:
    def __init__(self, grid: list[list[str]]) -> None:
        self.grid = [list(r) for r in grid]

    def get_values(self) -> list[list[str]]:
        return [list(r) for r in self.grid]

    def update_cells(self, updates: list[tuple[int, int, str]]) -> None:
        for row, col, value in updates:
            r = self.grid[row - 1]
            while len(r) <= col:
                r.append("")
            r[col] = value

    def insert_rows_at(self, row: int, count: int) -> None:
        for _ in range(count):
            self.grid.insert(row - 1, [""] * len(SEGUIMIENTO_COLUMNS))

    def write_rows(self, start_row: int, rows: list[list[str]]) -> None:
        for i, values in enumerate(rows):
            self.grid[start_row - 1 + i] = list(values)


# --- Parte C: cabecera repetida a media hoja ---------------------------------------


def test_second_header_row_is_treated_as_structure_not_order(session_factory) -> None:
    assert is_structure_row(BART_MIDDLE_HEADER) is True
    assert is_structure_row(["^^^^ Aquí arriba pedidos en curso"]) is True
    assert is_structure_row(BART_TOP_HEADER) is True
    # Una fila de pedido real NO es estructura.
    assert is_structure_row(
        ["BO", "1/9/2026", "Cliente X", "WEB", "SAT", "UPS", "", "", "", "",
         "", "BOP-1", "", "", "", "", ""]
    ) is False

    hist = ["BO", "2/1/2020", "Historico SL", "WEB", "SAT", "UPS", "", "", "",
            "", "", "BOP-000111", "1-200999", "", "", "", ""]
    sheet = FakeSheet([
        BART_TOP_HEADER,
        ["^^^^ Aquí arriba pedidos en curso"],
        BART_MIDDLE_HEADER,
        hist,
    ])
    with session_factory() as s:
        _order(s, "BOP-900001", cliente="Nuevo SL")
        s.commit()
        summary = sync_to_sheet(s, sheet, _rows_for_sync(s))
    # El pedido nuevo entra bajo la cabecera SUPERIOR (sección en curso).
    assert summary["appended_rows"] == 1
    assert sheet.grid[1][11] == "BOP-900001"
    # La cabecera intermedia y el separador siguen intactos: no se
    # interpretaron como pedidos ni se sobrescribieron.
    assert sheet.grid[2][0].startswith("^^^^")
    assert sheet.grid[3] == BART_MIDDLE_HEADER
    assert sheet.grid[4] == hist
    # «Albarán» de la cabecera intermedia no generó un pedido fantasma.
    assert summary["updated_cells"] == 0


# --- Parte D: columna opcional ausente / columna requerida ausente -----------------


def test_missing_optional_column_syncs_rest_and_warns(session_factory) -> None:
    # Cabecera de Bart SIN la columna Tracking (opcional).
    header = [c for c in BART_TOP_HEADER if c != "TRACKING"]
    sheet = FakeSheet([header])
    with session_factory() as s:
        _order(s, "BOP-900010", cliente="Uno SL",
               tracking="1Z999", serial="FBAP12613200249")
        s.commit()
        summary = sync_to_sheet(s, sheet, _rows_for_sync(s))
    # Se sincroniza el resto y se avisa de la columna omitida.
    assert summary["omitted_columns"] == ["Tracking"]
    assert summary["appended_rows"] == 1
    # El nº de serie (columna presente) sí se escribe, en su sitio real.
    serie_col = match_header_columns(header)[SEGUIMIENTO_COLUMNS.index("Nº de Serie")]
    assert sheet.grid[1][serie_col] == "FBAP12613200249"
    # El tracking no aparece en ninguna parte (no hay columna donde ponerlo).
    assert not any("1Z999" in str(c) for c in sheet.grid[1])


def test_missing_required_column_error_lists_header_read(session_factory) -> None:
    # Cabecera SIN la columna clave (albarán / nº pedido web).
    header = [c for c in BART_TOP_HEADER if c != "Albarán / Núm Pedido WEb"]
    sheet = FakeSheet([header])
    with session_factory() as s:
        _order(s, "BOP-900020", cliente="Uno SL")
        s.commit()
        with pytest.raises(DriveSyncError) as exc:
            sync_to_sheet(s, sheet, _rows_for_sync(s))
    msg = str(exc.value)
    # Dice QUÉ columna falta…
    assert KEY_COLUMN in msg
    # …y ENSEÑA la cabecera leída, con índices.
    assert "[0]" in msg
    assert "«f»" in msg
    assert "No se ha escrito nada" in msg
    # Nada se escribió: la hoja sigue teniendo solo su cabecera.
    assert len(sheet.grid) == 1
