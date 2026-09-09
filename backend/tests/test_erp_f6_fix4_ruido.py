"""ERP-F6-fix4 — 69 conflictos de los que solo ~12 eran reales, y BoHub
estampaba la fecha de importación como si fuera la del hecho.

- Fechas comparadas como fechas (no como texto).
- Fechas estampadas en la importación: ni se escriben ni se reportan.
- Productos: fuera de la comparación; solo se rellena si está vacía.
- Cliente: se escribe «Empresa (Persona)» y confirma por cualquiera de las dos.
- Contradicho vs sin confirmar (coincidencia probable).
- `STR` ≡ `ST`; mínimo 4 dígitos para casar; MANUAL fuera del match por número.
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
from app.erp.drive_sheets import _locate_existing, _sheet_row_meta, sync_to_sheet
from app.erp.models import (
    Order,
    OrderSource,
    OrderStatusHistory,
    StatusDomain,
)
from app.erp.seguimiento import (
    SEGUIMIENTO_COLUMNS,
    clients_match,
    compose_client,
    match_number,
    parse_sheet_date,
    same_day,
    split_client_parts,
)
from app.models.crm import Company, Contact
from tests._test_helpers import seed_test_users

HEADER = list(core.SEGUIMIENTO_COLUMNS)
_IDX = {name: i for i, name in enumerate(SEGUIMIENTO_COLUMNS)}


# --- Parte B: fechas como fechas ---------------------------------------------------


def test_date_comparison_ignores_format() -> None:
    assert same_day("10/08/2026", "10/8/2026")
    assert same_day("10/08/2026", "2026-08-10")
    assert same_day("10/8/2026", "10/08/26")
    # Serial de Excel de ese mismo día.
    serial = (parse_sheet_date("10/08/2026") - core._EXCEL_EPOCH).days  # type: ignore[operator]
    assert same_day("10/08/2026", str(serial))
    assert not same_day("", "10/8/2026")


def test_real_date_difference_is_still_a_conflict() -> None:
    assert not same_day("03/08/2026", "31/07/2026")
    assert same_day("31/07/2026", "31/7/2026")


# --- Parte C: productos -----------------------------------------------------------


def test_products_column_never_compared_config() -> None:
    from app.erp.drive_sheets import _FILL_ONLY_IF_EMPTY
    assert _IDX["Productos"] in _FILL_ONLY_IF_EMPTY
    assert _IDX["Cliente"] in _FILL_ONLY_IF_EMPTY


# --- Parte D: cliente -------------------------------------------------------------


def test_client_written_as_company_and_person() -> None:
    assert compose_client("Brick Print", "Michel Roos") == "Brick Print (Michel Roos)"
    assert compose_client("MGB-TECH", None) == "MGB-TECH"
    assert compose_client(None, "Claudia") == "Claudia"
    assert compose_client("Claudia", "Claudia") == "Claudia"   # no duplicar
    assert compose_client(None, None) is None
    assert split_client_parts("Brick Print (Michel Roos)") == ["Brick Print", "Michel Roos"]
    assert split_client_parts("SOCLOS") == ["SOCLOS"]


def test_client_confirms_on_either_company_or_person() -> None:
    # La celda de la hoja parte en «Empresa (Persona)»; confirma cualquiera.
    parts = split_client_parts("Brick Print (Michel Roos)")
    assert any(clients_match("BRICK PRINT", p) for p in parts)        # por empresa
    assert any(clients_match("Michel Roos", p) for p in parts)        # por persona
    # BoHub solo con la persona, la hoja con «Empresa (Persona)».
    parts = split_client_parts("MGB-TECH (Sara D'Haese)")
    assert any(clients_match("Sara D'Haese", p) for p in parts)


def test_client_comparison_ignores_legal_forms_and_accents() -> None:
    assert clients_match("Judith Gutierrez Martinez", "Judith Gutiérrez Martínez")
    assert clients_match("Onlyguay SC", "ONLYGUAY SC")
    assert clients_match("DOCUMENT MATERIEL SA", "DM Document Materiel SA")
    assert clients_match("BVBA MGB-TECH", "MGB-TECH")
    assert not clients_match("ALBERTO DE SOLA", "EVA MARIA CARREIRA")


# --- Parte E: contradicho vs sin confirmar ----------------------------------------


def test_short_numbers_do_not_match() -> None:
    assert match_number("MANUAL-000001") is None      # 1 dígito + MANUAL
    assert match_number("123") is None                # 3 dígitos
    assert match_number("1234") == "1234"
    assert match_number("BOPRIN-99866") == "99866"


def test_manual_references_excluded_from_number_matching() -> None:
    assert core.order_match_numbers({"order_number": "MANUAL-000001"}) == set()
    assert core.order_match_numbers({"order_number": "ARTISJ-9505"}) == {"9505"}


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


def _order(s: Session, number: str, *, company: str | None = None,
           person: str | None = None, factura: str | None = None,
           placed: str = "2026-07-24",
           source: OrderSource = OrderSource.WOOCOMMERCE) -> Order:
    company_id = None
    if company:
        c = Company(name=company)
        s.add(c)
        s.flush()
        company_id = c.id
    contact_id = None
    if person:
        parts = person.split(" ", 1)
        ct = Contact(first_name=parts[0], last_name=parts[1] if len(parts) > 1 else "")
        s.add(ct)
        s.flush()
        contact_id = ct.id
    o = Order(
        order_number=number, external_source=source, company_id=company_id,
        contact_id=contact_id, factusol_invoice_number=factura,
        placed_at=datetime.fromisoformat(placed).replace(tzinfo=UTC),
    )
    s.add(o)
    s.flush()
    return o


def _rows_for_sync(s: Session) -> list[dict]:
    return core.filter_rows(_rows(s), en_curso=False, sort="fecha", direction="asc")


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


# --- Parte A: fechas estampadas en la importación ---------------------------------


def test_import_stamped_dates_are_never_written(session_factory) -> None:
    # Caso real BOPRIN-99866: importado el 4/8, con las tres transiciones
    # estampadas ese día (dato falso). La hoja de Bart tiene las buenas.
    stamp = datetime(2026, 8, 4, 9, 0, tzinfo=UTC)
    with session_factory() as s:
        o = _order(s, "BOPRIN-99866", company="DUPLICODER, S.L.",
                   factura="1-260695", placed="2026-07-24")
        o.created_at = stamp   # el día de la importación
        for domain, to_status in (("preparation", "packed"),
                                  ("transport", "in_transit"),
                                  ("invoice", "generated")):
            s.add(OrderStatusHistory(
                order_id=o.id, domain=StatusDomain(domain),
                from_status=None, to_status=to_status, changed_at=stamp,
                reason="Procesado externamente",
            ))
        s.commit()
        # build_rows NO expone las fechas estampadas: quedan vacías.
        rows = _rows_for_sync(s)
    r = next(x for x in rows if x["order_number"] == "BOPRIN-99866")
    assert r["preparado"] is None
    assert r["recogido"] is None
    assert r["fecha_envio_factura"] is None

    # La hoja tiene las fechas buenas de Bart; el sync no las toca ni reporta.
    bart = _mk_sheet_row(albaran="99866", factura="260695", cliente="DUPLICODER")
    bart[_IDX["Empresa"]] = "BO"
    bart[_IDX["Preparado"]] = "27/07/2026"
    bart[_IDX["Recogido"]] = "27/07/2026"
    bart[_IDX["F Envío Factura"]] = "31/07/2026"
    sheet = FakeSheet([HEADER, bart])
    with session_factory() as s:
        summary = sync_to_sheet(s, sheet, _rows_for_sync(s))
    # Las tres celdas de fecha siguen intactas…
    assert sheet.grid[1][_IDX["Preparado"]] == "27/07/2026"
    assert sheet.grid[1][_IDX["Recogido"]] == "27/07/2026"
    assert sheet.grid[1][_IDX["F Envío Factura"]] == "31/07/2026"
    # …y NO se reporta ningún conflicto sobre ellas.
    date_cols = {"Preparado", "Recogido", "F Envío Factura"}
    assert not any(c.get("column") in date_cols for c in summary["conflicts"])


def test_products_never_touched_on_existing_row(session_factory) -> None:
    # ERP-F6-fix7: la fila existente NO se actualiza — ni la celda de Productos
    # con contenido ni la vacía. Y nunca hay conflicto de Productos.
    with session_factory() as s:
        o = _order(s, "BOPRIN-99870", company="Cliente SL", factura="1-260700")
        from app.erp.models import OrderLine
        s.add(OrderLine(order_id=o.id, position=0, product_sku="X",
                        description="Tinta UV", quantity=1, unit_price=1, line_total=1))
        s.commit()
    # 1) Celda de productos con el texto abreviado de Bart → no se toca.
    sheet = FakeSheet([HEADER, _mk_sheet_row(
        albaran="99870", factura="260700", cliente="Cliente SL",
    )])
    sheet.grid[1][_IDX["Productos"]] = "CMY, 250ML."
    with session_factory() as s:
        summary = sync_to_sheet(s, sheet, _rows_for_sync(s))
    assert sheet.grid[1][_IDX["Productos"]] == "CMY, 250ML."     # intacto
    assert not any(c.get("column") == "Productos" for c in summary["conflicts"])
    # 2) Celda de productos vacía → tampoco se rellena (no se toca la fila).
    sheet.grid[1][_IDX["Productos"]] = ""
    with session_factory() as s:
        sync_to_sheet(s, sheet, _rows_for_sync(s))
    assert sheet.grid[1][_IDX["Productos"]] == ""


# --- Parte E: contradicho vs probable ---------------------------------------------


def test_missing_data_is_probable_not_conflict(session_factory) -> None:
    # BOPRIN-99887: BoHub sin factura y sin más datos que confirmen; la hoja
    # tiene la factura 260731 → coincidencia PROBABLE, no conflicto.
    with session_factory() as s:
        _order(s, "BOPRIN-99887", company="ARSA", factura=None, placed="2026-08-05")
        s.commit()
        rows = _rows_for_sync(s)
        meta = _sheet_row_meta(
            [HEADER, _mk_sheet_row(albaran="99887", factura="260731")],
            1, {i: i for i in range(len(HEADER))},
        )
        rownum, status, info = _locate_existing(rows[0], meta, set())
    assert rownum is None
    assert status == "probable"
    assert 2 in info["rows"]


def test_contradicted_is_a_conflict(session_factory) -> None:
    with session_factory() as s:
        _order(s, "BOPRIN-5742", company="OTRO CLIENTE", placed="2026-09-02")
        s.commit()
        rows = _rows_for_sync(s)
        meta = _sheet_row_meta(
            [HEADER, _mk_sheet_row(albaran="5742", cliente="FLUXCLIENTE",
                                   fecha="01/08/2026")],
            1, {i: i for i in range(len(HEADER))},
        )
        _rownum, status, _info = _locate_existing(rows[0], meta, set())
    assert status == "contradicted"


def _mk_sheet_row(*, albaran: str = "", factura: str = "", cliente: str = "",
                  fecha: str = "") -> list[str]:
    out = [""] * len(HEADER)
    out[_IDX["Albarán / Nº Pedido Web"]] = albaran
    out[_IDX["Nº de Factura"]] = factura
    out[_IDX["Cliente"]] = cliente
    out[_IDX["Fecha entrada albarán"]] = fecha
    return out


# --- Parte F: STR ≡ ST ------------------------------------------------------------


def test_str_variant_accepted_and_not_rewritten(session_factory) -> None:
    with session_factory() as s:
        _order(s, "BOP-099894", company="Cliente SL", factura="5-260065")  # serie 5 → ST
        s.commit()
    sheet = FakeSheet([HEADER, _mk_sheet_row(albaran="99894", factura="260065",
                                             cliente="Cliente SL")])
    sheet.grid[1][_IDX["Empresa"]] = "STR"          # variante histórica de Bart
    with session_factory() as s:
        summary = sync_to_sheet(s, sheet, _rows_for_sync(s))
    # No se reescribe ni se reporta conflicto: STR ≡ ST.
    assert sheet.grid[1][_IDX["Empresa"]] == "STR"
    assert not any(c.get("column") == "Empresa" for c in summary["conflicts"])


# --- Parte G: agrupación por pedido -----------------------------------------------


def test_preview_groups_conflicts_by_order(session_factory) -> None:
    with session_factory() as s:
        # Contradicho (5742) + probable (99887) + uno que actualiza limpio.
        _order(s, "BOPRIN-5742", company="OTRO", placed="2026-09-02")
        _order(s, "BOPRIN-99887", company="ARSA", placed="2026-08-05")
        _order(s, "ARTISJ-9999", company="Nuevo", placed="2026-09-03")
        s.commit()
        sheet = FakeSheet([
            HEADER,
            _mk_sheet_row(albaran="5742", cliente="FLUXCLIENTE", fecha="01/08/2026"),
            _mk_sheet_row(albaran="99887", factura="260731"),
        ])
        preview = sync_to_sheet(s, sheet, _rows_for_sync(s), dry_run=True)
    # Un grupo por pedido a revisar, no una lista plana de celdas.
    kinds = {g["order_number"]: g["items"][0]["kind"] for g in preview["review_groups"]}
    assert kinds["BOPRIN-5742"] == "contradicted"
    assert kinds["BOPRIN-99887"] == "probable_match"
    assert preview["orders_to_review"] == 2
    assert preview["appended_rows"] == 1        # ARTISJ-9999
    # Nada escrito en la previsualización.
    assert len(sheet.grid) == 3
