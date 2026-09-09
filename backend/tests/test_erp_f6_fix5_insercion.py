"""ERP-F6-fix5 — inserción en la sección correcta y Empresa desde la factura.

- Las filas nuevas van bajo el SEGUNDO encabezado (sección en curso),
  aprovechando las filas reservadas; nunca en la sección de arriba (incidencias).
- La Empresa de una fila existente no se sobrescribe con la serie de la tienda:
  manda la factura (de BoHub o de la hoja) o la que ya tuviera Bart.
- Números no reinterpretados como fechas (RAW); sin filas vacías al final.
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
from app.erp.drive_sheets import (
    DriveSyncError,
    GoogleSheetsClient,
    _row_is_blank,
    sync_to_sheet,
)
from app.erp.models import Order, OrderSource
from app.erp.seguimiento import SEGUIMIENTO_COLUMNS
from app.models.crm import Company
from app.models.integration_settings import (
    ExternalSystem,
    IntegrationAccount,
    IntegrationMode,
)
from tests._test_helpers import seed_test_users

HEADER = list(SEGUIMIENTO_COLUMNS)
_IDX = {name: i for i, name in enumerate(SEGUIMIENTO_COLUMNS)}
SEP = ["^^^^  Aqui arriba pedidos que faltan entregar ."]
BAND = ["ARRIBA EN PROCESO"]
# La segunda cabecera real de Bart (más corta, otras etiquetas).
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


def _blocked(ref: str) -> list[str]:
    """Fila de pedido con incidencia (sección de arriba, de Bart)."""
    r = _sheet_row(Cliente="Bloqueado", Transport="MRW")
    r[_IDX["Albarán / Nº Pedido Web"]] = ref
    return r


def _real_sheet(reserved: int = 9, existing: list[list[str]] | None = None) -> list[list[str]]:
    """Estructura real de Bart: cabecera, sección de incidencias, separador,
    banda, segunda cabecera, `reserved` filas vacías y pedidos en curso."""
    grid = [HEADER, _blocked("111111"), _blocked("222222"), SEP, [], BAND, SECOND_HEADER]
    grid += [[""] * len(HEADER) for _ in range(reserved)]
    grid += existing or []
    return grid


# --- fixtures ---------------------------------------------------------------------


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


def _store(s: Session, slug: str) -> str:
    acc = IntegrationAccount(system=ExternalSystem.WOOCOMMERCE, account_id=slug,
                             display_name=slug, mode=IntegrationMode.LIVE)
    s.add(acc)
    s.flush()
    return acc.id


def _order(s: Session, number: str, *, cliente: str = "Cliente", store_id: str | None = None,
           factura: str | None = None) -> None:
    comp = Company(name=cliente)
    s.add(comp)
    s.flush()
    s.add(Order(
        order_number=number, external_source=OrderSource.WOOCOMMERCE,
        company_id=comp.id, store_id=store_id, factusol_invoice_number=factura,
        placed_at=datetime(2026, 9, 1, tzinfo=UTC),
    ))
    s.flush()


def _store_series(http, mapping: dict[str, str]) -> None:
    from tests._test_helpers import auth_headers
    r = http.patch("/api/erp/settings", json={"factusol_series_by_source": mapping},
                   headers=auth_headers(http, "admin"))
    assert r.status_code == 200, r.text


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
            while len(self.grid) <= start_row - 1 + i:
                self.grid.append([""] * len(HEADER))
            self.grid[start_row - 1 + i] = list(values)


# --- Parte A: inserción ------------------------------------------------------------


def test_new_rows_inserted_below_second_header(session_factory) -> None:
    sheet = FakeSheet(_real_sheet(reserved=9))
    # Segunda cabecera en la fila 7 (1-based); reservadas 8–16.
    assert sheet.grid[6] == SECOND_HEADER
    with session_factory() as s:
        _order(s, "ARTISJ-9999", cliente="Nuevo")
        s.commit()
        sync_to_sheet(s, sheet, _rows_for_sync(s))
    # Entra en la primera reservada (fila 8, índice 7), no en la 2.
    assert sheet.grid[7][_IDX["Albarán / Nº Pedido Web"]] == "9999"
    # La sección de arriba (incidencias) intacta.
    assert sheet.grid[1] == _blocked("111111")
    assert sheet.grid[2] == _blocked("222222")


def test_reserved_empty_rows_are_filled_first(session_factory) -> None:
    sheet = FakeSheet(_real_sheet(reserved=9))
    before_len = len(sheet.grid)
    with session_factory() as s:
        for i in range(3):
            _order(s, f"ARTISJ-900{i}", cliente=f"C{i}")
        s.commit()
        sync_to_sheet(s, sheet, _rows_for_sync(s))
    # Se rellenan reservadas, NO se insertan filas nuevas → longitud igual.
    assert len(sheet.grid) == before_len
    filled = [sheet.grid[7 + i][_IDX["Albarán / Nº Pedido Web"]] for i in range(3)]
    assert set(filled) == {"9000", "9001", "9002"}


def test_rows_are_inserted_when_reserved_rows_exhausted(session_factory) -> None:
    first_existing = _sheet_row(Cliente="Ya estaba")
    first_existing[_IDX["Albarán / Nº Pedido Web"]] = "500000"
    sheet = FakeSheet(_real_sheet(reserved=9, existing=[first_existing]))
    before_len = len(sheet.grid)
    with session_factory() as s:
        for i in range(12):
            _order(s, f"ARTISJ-95{i:02d}", cliente=f"C{i}")
        s.commit()
        summary = sync_to_sheet(s, sheet, _rows_for_sync(s))
    assert summary["appended_rows"] == 12
    # 9 reservadas + 3 insertadas.
    assert len(sheet.grid) == before_len + 3
    # El primer pedido existente no se pisó: sigue ahí, desplazado 3 filas.
    assert first_existing in sheet.grid
    idx = sheet.grid.index(first_existing)
    assert sheet.grid[idx][_IDX["Cliente"]] == "Ya estaba"


def test_top_section_is_never_written(session_factory) -> None:
    sheet = FakeSheet(_real_sheet(reserved=9))
    top_before = [list(sheet.grid[i]) for i in range(1, 3)]  # filas de incidencia
    with session_factory() as s:
        # Un pedido cuyo número coincide con uno bloqueado de arriba.
        _order(s, "ARTISJ-111111", cliente="Nuevo")
        s.commit()
        sync_to_sheet(s, sheet, _rows_for_sync(s))
    # La sección de arriba no se toca ni se lee como en curso: el 111111 de
    # arriba no se actualiza; el pedido entra nuevo abajo.
    assert [list(sheet.grid[i]) for i in range(1, 3)] == top_before


def test_no_write_when_section_markers_not_found(session_factory) -> None:
    # Hay separador «^^^^» pero NO hay segundo encabezado debajo.
    sheet = FakeSheet([HEADER, _blocked("111111"), SEP, [], []])
    with session_factory() as s:
        _order(s, "ARTISJ-9999", cliente="Nuevo")
        s.commit()
        with pytest.raises(DriveSyncError):
            sync_to_sheet(s, sheet, _rows_for_sync(s))


def test_no_trailing_empty_rows_appended(session_factory) -> None:
    existing = _sheet_row(Cliente="Ya")
    existing[_IDX["Albarán / Nº Pedido Web"]] = "500000"
    sheet = FakeSheet(_real_sheet(reserved=0, existing=[existing]))
    before_len = len(sheet.grid)
    with session_factory() as s:
        _order(s, "ARTISJ-9999", cliente="Nuevo")
        s.commit()
        sync_to_sheet(s, sheet, _rows_for_sync(s))
    # Se inserta exactamente 1 fila; no hay basura vacía al final.
    assert len(sheet.grid) == before_len + 1
    assert not _row_is_blank(sheet.grid[-1])


# --- Parte B: Empresa desde la factura --------------------------------------------


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


def _existing_row(*, albaran: str, empresa: str = "", factura: str = "",
                  cliente: str = "Cliente") -> list[str]:
    r = _sheet_row(Empresa=empresa, Cliente=cliente)
    r[_IDX["Albarán / Nº Pedido Web"]] = albaran
    r[_IDX["Nº de Factura"]] = factura
    return r


def _find_row(sheet, ref: str) -> int:
    """Índice de la fila cuya columna clave vale `ref` (acceso seguro a filas
    de distinta longitud)."""
    key = _IDX["Albarán / Nº Pedido Web"]
    for i, r in enumerate(sheet.grid):
        if key < len(r) and r[key] == ref:
            return i
    raise AssertionError(f"no se encontró la fila {ref}")


def test_empresa_not_overwritten_when_sheet_has_invoice(session_factory, http) -> None:
    _store_series(http, {"boprint": "5"})   # tienda → ST
    row = _existing_row(albaran="99895", empresa="BO", factura="1-260737",
                        cliente="Cliente")
    sheet = FakeSheet(_real_sheet(reserved=0, existing=[row]))
    with session_factory() as s:
        store = _store(s, "boprint")
        _order(s, "BOPRIN-99895", cliente="Cliente", store_id=store)  # sin factura BoHub
        s.commit()
        summary = sync_to_sheet(s, sheet, _rows_for_sync(s))
    # La Empresa de Bart (BO) NO se toca, pese a que la tienda diría ST.
    idx = _find_row(sheet, "99895")
    assert sheet.grid[idx][_IDX["Empresa"]] == "BO"
    assert not any(c.get("column") == "Empresa" for c in summary["conflicts"])


def test_empresa_of_existing_row_never_filled(session_factory, http) -> None:
    # ERP-F6-fix7: la fila EXISTENTE no se actualiza — su Empresa vacía se queda
    # vacía aunque la hoja traiga una factura con serie explícita.
    _store_series(http, {"boprint": "5"})
    row = _existing_row(albaran="99895", empresa="", factura="1-260737")  # celda vacía
    sheet = FakeSheet(_real_sheet(reserved=0, existing=[row]))
    with session_factory() as s:
        store = _store(s, "boprint")
        _order(s, "BOPRIN-99895", cliente="Cliente", store_id=store)
        s.commit()
        sync_to_sheet(s, sheet, _rows_for_sync(s))
    idx = _find_row(sheet, "99895")
    assert sheet.grid[idx][_IDX["Empresa"]] == ""      # NO se rellena


def test_existing_row_empresa_not_resolved_against_factusol(session_factory) -> None:
    # ERP-F6-fix7: aunque la factura desnuda de la hoja se pudiera resolver, la
    # fila existente no se toca.
    row = _existing_row(albaran="99887", empresa="", factura="260731", cliente="ARSA")
    sheet = FakeSheet(_real_sheet(reserved=0, existing=[row]))
    with session_factory() as s:
        _order(s, "BOPRIN-99887", cliente="ARSA")   # sin factura BoHub, sin tienda
        s.commit()
        sync_to_sheet(s, sheet, _rows_for_sync(s),
                      invoice_serie_resolver=lambda cod: 1 if cod == "260731" else None)
    idx = _find_row(sheet, "99887")
    assert sheet.grid[idx][_IDX["Empresa"]] == ""     # NO se rellena


def test_ambiguous_bare_invoice_number_leaves_cell_untouched(session_factory, http) -> None:
    _store_series(http, {"boprint": "5"})
    row = _existing_row(albaran="99887", empresa="", factura="260731")
    sheet = FakeSheet(_real_sheet(reserved=0, existing=[row]))
    with session_factory() as s:
        store = _store(s, "boprint")
        _order(s, "BOPRIN-99887", cliente="ARSA", store_id=store)
        s.commit()
        sync_to_sheet(s, sheet, _rows_for_sync(s),
                      invoice_serie_resolver=lambda _cod: None)   # ambiguo
    idx = _find_row(sheet, "99887")
    # Fila existente → nunca se toca (ni con la serie de la tienda).
    assert sheet.grid[idx][_IDX["Empresa"]] == ""


def test_store_serie_not_applied_to_existing_row(session_factory, http) -> None:
    # ERP-F6-fix7: la serie de la tienda tampoco se escribe en una fila que ya
    # está en la hoja (solo se usa al INSERTAR filas nuevas).
    _store_series(http, {"boprint": "5"})
    row = _existing_row(albaran="99999", empresa="", factura="", cliente="Cliente")
    sheet = FakeSheet(_real_sheet(reserved=0, existing=[row]))
    with session_factory() as s:
        store = _store(s, "boprint")
        _order(s, "BOPRIN-99999", cliente="Cliente", store_id=store)  # sin factura ninguna
        s.commit()
        sync_to_sheet(s, sheet, _rows_for_sync(s))
    idx = _find_row(sheet, "99999")
    assert sheet.grid[idx][_IDX["Empresa"]] == ""     # NO se rellena


def test_sheet_invoice_unknown_to_bohub_is_informational(session_factory) -> None:
    # ERP-F6-fix7: «factura de la hoja que BoHub no conoce» es INFORMACIÓN, va
    # en `unknown_invoices` (sección aparte), no en «a revisar».
    row = _existing_row(albaran="99887", empresa="BO", factura="260731", cliente="ARSA")
    sheet = FakeSheet(_real_sheet(reserved=0, existing=[row]))
    with session_factory() as s:
        _order(s, "BOPRIN-99887", cliente="ARSA")   # BoHub no tiene la factura
        s.commit()
        summary = sync_to_sheet(s, sheet, _rows_for_sync(s))
    assert any(
        p["kind"] == "sheet_invoice_unknown" and "260731" in (p["detail"] or "")
        for p in summary["unknown_invoices"]
    )
    # No entra en «a revisar» ni en conflictos.
    assert summary["orders_to_review"] == 0
    assert summary["probable_matches"] == []


# --- Parte C: números no reinterpretados como fechas ------------------------------


def test_numbers_are_not_reinterpreted_as_dates() -> None:
    # GoogleSheetsClient escribe con valueInputOption=RAW (Sheets no reinterpreta).
    captured: list[dict] = []

    client = GoogleSheetsClient({"client_email": "x", "private_key": "y"}, "sid")
    client._sheet_id, client._sheet_title = 0, "Hoja"  # evita llamadas de descubrimiento

    def fake_request(method, path, **kwargs):
        captured.append({"method": method, "path": path, "json": kwargs.get("json")})
        return {}

    client._request = fake_request  # type: ignore[method-assign]
    client.update_cells([(5, 10, "53")])
    body = captured[0]["json"]
    assert body["valueInputOption"] == "RAW"
    assert body["data"][0]["values"] == [["53"]]

    captured.clear()
    client.write_rows(21, [["53", "521", "29082022"]])
    assert "valueInputOption=RAW" in captured[0]["path"]
    assert captured[0]["json"]["values"] == [["53", "521", "29082022"]]
