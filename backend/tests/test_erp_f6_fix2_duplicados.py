"""ERP-F6-fix2 — la sincronización con Drive duplicaba pedidos que ya estaban.

Bart escribe el número de pedido DESNUDO en la hoja (`99866`); BoHub escribía
su referencia con prefijo (`BOPRIN-99866`) y, al buscar la suya, nunca la
encontraba y duplicaba. Ahora el pedido se identifica por el número,
confirmado con un segundo dato, se ACTUALIZA en vez de duplicar, y la
referencia se escribe desnuda. Además hay previsualización antes de escribir.
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
from app.erp import seguimiento as core
from app.erp.api.seguimiento import _rows
from app.erp.drive_sheets import sync_to_sheet
from app.erp.models import Order, OrderSource
from app.erp.seguimiento import (
    extract_order_number,
    normalize_client,
    numbers_match,
    reference_for_row,
)
from app.models.crm import Company
from tests._test_helpers import seed_test_users

HEADER = list(core.SEGUIMIENTO_COLUMNS)


# --- Parte A/B: unidades de identificación -----------------------------------------


def test_bare_number_matches_prefixed_reference() -> None:
    assert extract_order_number("BOPRIN-99866") == "99866"
    assert extract_order_number("99866") == "99866"
    assert extract_order_number("99866.0") == "99866"      # el .0 de Excel
    assert extract_order_number("ARTISJ-9460") == "9460"
    assert extract_order_number("5-260695") == "260695"    # serie de la factura
    assert extract_order_number("MANUAL-000001") == "1"    # sin ceros a la izq.
    assert extract_order_number("") is None
    assert extract_order_number("SIN NUMERO") is None
    assert numbers_match("BOPRIN-99866", "99866")
    assert numbers_match("BOPRIN-99866", "99866.0")


def test_number_match_is_exact_not_substring() -> None:
    assert not numbers_match("FLUXLA-5742", "15742")
    assert not numbers_match("FLUXLA-5742", "57420")
    assert not numbers_match("5742", "574")
    assert numbers_match("FLUXLA-5742", "5742")


def test_client_comparison_tolerates_legal_forms() -> None:
    assert normalize_client("DUPLICODER, S.L.") == normalize_client("DUPLICODER")
    assert normalize_client("Duplicoder SL") == normalize_client("DUPLICODER")
    assert normalize_client("Hirsch Armbänder GmbH") == normalize_client(
        "hirsch armbander gmbh")
    # No colapsa nombres distintos.
    assert normalize_client("DUPLICODER") != normalize_client("MULTICODER")


def test_reference_written_without_prefix() -> None:
    row = {"order_number": "BOPRIN-99866", "albaran_pedido": "BOPRIN-99866"}
    assert reference_for_row(row) == "99866"


def test_albaran_number_preferred_over_web_reference_when_available() -> None:
    row = {
        "order_number": "BOPRIN-99866", "albaran_pedido": "BOPRIN-99866",
        "albaran_number": "100197",
    }
    # Por defecto se prefiere el número de albarán (formato de las filas viejas).
    assert reference_for_row(row, prefer_albaran=True) == "100197"
    # Configurable: si no se prefiere, el número de pedido web.
    assert reference_for_row(row, prefer_albaran=False) == "99866"
    # Sin número de albarán, siempre el de pedido web.
    assert reference_for_row({"order_number": "BOPRIN-99866"}) == "99866"


# --- infraestructura para la sincronización ----------------------------------------


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


def _order(s: Session, number: str, *, cliente: str, source: OrderSource,
           factura: str | None = None, placed: str = "2026-07-24",
           tracking: str | None = None) -> None:
    comp = Company(name=cliente)
    s.add(comp)
    s.flush()
    s.add(Order(
        order_number=number, external_source=source, company_id=comp.id,
        factusol_invoice_number=factura, tracking_number=tracking,
        placed_at=datetime.fromisoformat(placed).replace(tzinfo=UTC),
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
            self.grid.insert(row - 1, [""] * len(HEADER))

    def write_rows(self, start_row: int, rows: list[list[str]]) -> None:
        for i, values in enumerate(rows):
            self.grid[start_row - 1 + i] = list(values)


def _dup_row(**over: str) -> list[str]:
    """La fila 87 real de DUPLICODER escrita por Bart hace meses."""
    row = ["BO", "24/07/2026", "DUPLICODER", "WEB", "SAT", "CTT EXP",
           "27/07/2026", "27/07/2026", "31/07/2026", "CMY, 250ML.", "ABP0168",
           "99866", "260695", "TRACK-BART", "", "", ""]
    for k, v in over.items():
        row[int(k[1:])] = v  # _11=... → índice 11
    return row


# --- Parte C: actualizar en lugar de duplicar --------------------------------------


def test_existing_order_is_updated_not_duplicated(session_factory) -> None:
    sheet = FakeSheet([HEADER, _dup_row()])
    with session_factory() as s:
        # Mismo pedido: DUPLICODER, 24/07, factura 260695, pero referencia
        # BOPRIN-99866 (con prefijo) — antes duplicaba.
        _order(s, "BOPRIN-99866", cliente="DUPLICODER, S.L.",
               source=OrderSource.WOOCOMMERCE, factura="5-260695")
        s.commit()
        summary = sync_to_sheet(s, sheet, _rows_for_sync(s))
    # NO se inserta otra fila: se reconoce y se actualiza la 87.
    assert summary["appended_rows"] == 0
    assert summary["updated_rows"] == 1
    assert len(sheet.grid) == 2                       # cabecera + la única fila
    assert sheet.grid[1][2] == "DUPLICODER"           # sigue siendo DUPLICODER


def test_match_requires_secondary_confirmation(session_factory) -> None:
    # La hoja tiene el pedido 5742 de FLUX (cliente FLUXCLIENTE, factura 111).
    sheet = FakeSheet([HEADER, [
        "ST", "1/8/2026", "FLUXCLIENTE", "WEB", "", "", "", "", "", "", "",
        "5742", "111", "", "", "", "",
    ]])
    with session_factory() as s:
        # BoHub tiene un 5742 DISTINTO (otra tienda): cliente y fecha distintos,
        # sin factura que confirme → coincidencia dudosa, NO se toca.
        _order(s, "BOPRIN-5742", cliente="OTRO CLIENTE",
               source=OrderSource.WOOCOMMERCE, placed="2026-09-02")
        s.commit()
        summary = sync_to_sheet(s, sheet, _rows_for_sync(s))
    assert summary["appended_rows"] == 0
    assert summary["updated_rows"] == 0
    assert len(summary["conflicts"]) == 1
    assert summary["conflicts"][0]["kind"] == "ambiguous_match"
    # La fila de la hoja no se ha modificado.
    assert sheet.grid[1][2] == "FLUXCLIENTE"
    assert len(sheet.grid) == 2


def test_update_never_overwrites_manual_cells(session_factory) -> None:
    # La fila de Bart ya tiene transportista, fechas y tracking propios.
    sheet = FakeSheet([HEADER, _dup_row()])
    with session_factory() as s:
        _order(s, "BOPRIN-99866", cliente="DUPLICODER, S.L.",
               source=OrderSource.WOOCOMMERCE, factura="5-260695",
               tracking="TRACK-BOHUB")   # BoHub tiene otro tracking
        s.commit()
        summary = sync_to_sheet(s, sheet, _rows_for_sync(s))
    # Transportista, fechas y tracking de Bart intactos.
    assert sheet.grid[1][5] == "CTT EXP"
    assert sheet.grid[1][6] == "27/07/2026"
    assert sheet.grid[1][13] == "TRACK-BART"
    # El tracking distinto de BoHub se registra como conflicto, no se pisa.
    assert any(
        c.get("kind") == "manual_cell" and c["column"] == "Tracking"
        for c in summary["conflicts"]
    )


def test_reference_stays_bare_number_on_update(session_factory) -> None:
    sheet = FakeSheet([HEADER, _dup_row()])
    with session_factory() as s:
        _order(s, "BOPRIN-99866", cliente="DUPLICODER, S.L.",
               source=OrderSource.WOOCOMMERCE, factura="5-260695")
        s.commit()
        sync_to_sheet(s, sheet, _rows_for_sync(s))
    # La celda de referencia sigue siendo el número desnudo, nunca BOPRIN-.
    assert sheet.grid[1][11] == "99866"


# --- Parte E: previsualización -----------------------------------------------------


def test_preview_reports_added_updated_and_conflicts_without_writing(
    session_factory,
) -> None:
    # Hoja: DUPLICODER (99866) ya está; un 5742 dudoso; y el separador/estruct.
    sheet = FakeSheet([
        HEADER,
        _dup_row(),
        ["ST", "1/8/2026", "FLUXCLIENTE", "WEB", "", "", "", "", "", "", "",
         "5742", "111", "", "", "", ""],
    ])
    before = [list(r) for r in sheet.grid]
    with session_factory() as s:
        _order(s, "BOPRIN-99866", cliente="DUPLICODER, S.L.",
               source=OrderSource.WOOCOMMERCE, factura="5-260695")   # actualiza
        _order(s, "BOPRIN-5742", cliente="OTRO", source=OrderSource.WOOCOMMERCE,
               placed="2026-09-02")                                  # conflicto
        _order(s, "ARTISJ-9999", cliente="NUEVO", source=OrderSource.WOOCOMMERCE,
               placed="2026-09-03")                                  # añade
        s.commit()
        preview = sync_to_sheet(s, sheet, _rows_for_sync(s), dry_run=True)
    assert preview["preview"] is True
    assert preview["appended_rows"] == 1
    assert preview["updated_rows"] == 1
    # El 5742 dudoso sale como conflicto de coincidencia; puede haber además
    # conflictos de celda manual (BoHub no pisa lo que Bart puso a mano).
    ambiguous = [c for c in preview["conflicts"] if c["kind"] == "ambiguous_match"]
    assert len(ambiguous) == 1
    assert ambiguous[0]["order_number"] == "BOPRIN-5742"
    # NADA se ha escrito ni añadido en la previsualización.
    assert sheet.grid == before
    # Y no se ha guardado ninguna foto de sincronización.
    with session_factory() as s:
        from app.erp.models import ErpDriveSyncRow
        assert s.scalar(select(ErpDriveSyncRow)) is None

    # Al confirmar (sin dry_run) sí escribe: añade 1, actualiza 1.
    with session_factory() as s:
        summary = sync_to_sheet(s, sheet, _rows_for_sync(s))
    assert summary["appended_rows"] == 1
    assert summary["updated_rows"] == 1
    # Reejecutar sin cambios: ni añade ni actualiza celdas (idempotente).
    with session_factory() as s:
        again = sync_to_sheet(s, sheet, _rows_for_sync(s))
    assert again["appended_rows"] == 0
    assert again["updated_cells"] == 0
