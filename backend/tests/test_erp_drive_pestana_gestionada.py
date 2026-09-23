"""Volcado del seguimiento (formato NUEVO) a su pestaña gestionada de Drive.

Dos contratos, y el primero es el importante:

1. La pestaña HISTÓRICA no se toca. El volcado reescribe su pestaña entera, así
   que apuntar por error a la histórica borraría el archivo de Bart (miles de
   filas con anotaciones a mano). Aquí se fija que se niega a hacerlo y que
   ninguna escritura la nombra.
2. Lo que se escribe es exactamente lo de la pantalla: las 17 columnas del
   rediseño 2026, ordenadas por Situación, con la celda Situación del color que
   les toca, y la pestaña de Incidencias como subconjunto exacto.

Sin red: el transporte de Sheets es un doble que registra lo que se le pide.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from app.erp.drive_historico import (
    SITUACION_HISTORICO,
    TIPO_PENDIENTE,
    import_historico,
    map_row,
    plan_import,
)
from app.erp.drive_managed import (
    DEFAULT_INCIDENCIAS_TAB,
    DEFAULT_MANAGED_TAB,
    SEPARATOR_PREFIX,
    build_incidencias_grid,
    build_pedidos_grid,
    is_separator,
    pedidos_format,
    push_managed_tabs,
    sheet_serial,
    static_block,
)
from app.erp.drive_sheets import DriveSyncError, _with_sheet_id
from app.erp.seguimiento import (
    INCIDENCIAS_COLUMNS,
    PEDIDOS_DATE_COLUMNS,
    SEGUIMIENTO_COLUMNS,
    SEGUIMIENTO_COLUMNS_V2,
    SITUACION_FILL,
)

HISTORICA = "Pedidos Bomedia 2020-2026"


class FakeTabs:
    """Doble del transporte: guarda pestañas, valores y formato pedidos."""

    def __init__(self, tabs: dict[str, list[list[Any]]] | None = None) -> None:
        self.tabs: dict[str, list[list[Any]]] = tabs or {HISTORICA: []}
        self.created: list[str] = []
        self.written: dict[str, list[list[Any]]] = {}
        self.formats: dict[str, list[dict[str, Any]]] = {}
        self.cleared: list[str] = []

    def tab_titles(self) -> list[str]:
        return list(self.tabs)

    def first_tab_title(self) -> str:
        return next(iter(self.tabs))

    def ensure_tab(self, title: str) -> int:
        if title not in self.tabs:
            self.tabs[title] = []
            self.created.append(title)
        return list(self.tabs).index(title)

    def replace_tab(self, title: str, rows: list[list[Any]]) -> None:
        self.cleared.append(title)
        self.tabs[title] = rows
        self.written[title] = rows

    def format_tab(self, title: str, requests: list[dict[str, Any]]) -> None:
        self.formats[title] = requests

    def tab_values(self, title: str) -> list[list[str]]:
        return [[str(c) for c in row] for row in self.tabs.get(title, [])]


def _row(situacion: str, numero: str, *, fecha: str | None = "2026-09-01",
         **extra: Any) -> dict[str, Any]:
    from app.erp.seguimiento import SITUACION_LABELS

    base = {
        "situacion": situacion,
        "situacion_label": SITUACION_LABELS[situacion],
        "order_number": numero, "fecha": fecha, "cliente": "Acme SL",
        "origen_label": "Web", "productos": "Cabezal", "importe": 121.0,
        "empresa_serie": "1 · Bomedia", "factura": "", "fecha_factura": None,
        "factura_enviada": None, "cobro_label": "—", "preparacion": "En cola",
        "envio": "Sin enviar", "tracking": "", "serie_whiterip": "",
        "nota_incidencia": "",
    }
    base.update(extra)
    return base


@pytest.fixture()
def session(db_session=None):
    """`series_config` solo necesita leer los ajustes; sin fila, devuelve {}."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session as S
    from sqlalchemy.pool import StaticPool

    import app.main  # noqa: F401
    from app.db.base import Base

    engine = create_engine("sqlite+pysqlite:///:memory:",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with S(engine) as s:
        yield s
    Base.metadata.drop_all(engine)


# --- la histórica no se toca ---------------------------------------------------


def test_no_escribe_en_la_pestana_historica(session):
    sheets = FakeTabs()
    push_managed_tabs(session, sheets, [_row("listo", "BOP-1")])
    assert HISTORICA not in sheets.written
    assert HISTORICA not in sheets.cleared
    assert HISTORICA not in sheets.formats
    # Y sigue existiendo, vacía como estaba.
    assert sheets.tabs[HISTORICA] == []


def test_se_niega_si_la_gestionada_es_la_historica(session, monkeypatch):
    """Si alguien configura como gestionada el título de la histórica, el
    volcado la borraría entera. Se niega en cada escritura, no solo al
    configurarla: en Drive se pueden renombrar pestañas."""
    monkeypatch.setattr(
        "app.erp.drive_managed.managed_tab_titles",
        lambda _s: (HISTORICA, DEFAULT_INCIDENCIAS_TAB),
    )
    sheets = FakeTabs()
    with pytest.raises(DriveSyncError) as exc:
        push_managed_tabs(session, sheets, [_row("listo", "BOP-1")])
    assert "HISTÓRICA" in str(exc.value)
    assert sheets.written == {}


# --- lo que se escribe ---------------------------------------------------------


def test_crea_las_pestanas_y_escribe_las_17_columnas(session):
    sheets = FakeTabs()
    resumen = push_managed_tabs(session, sheets, [_row("listo", "BOP-1")])
    assert sheets.created == [DEFAULT_MANAGED_TAB, DEFAULT_INCIDENCIAS_TAB]
    assert sheets.written[DEFAULT_MANAGED_TAB][0] == SEGUIMIENTO_COLUMNS_V2
    assert sheets.written[DEFAULT_INCIDENCIAS_TAB][0] == INCIDENCIAS_COLUMNS
    assert resumen["written"] is True
    assert resumen["tab"] == DEFAULT_MANAGED_TAB
    assert resumen["historic_tab"] == HISTORICA
    # No es el formato viejo.
    assert sheets.written[DEFAULT_MANAGED_TAB][0] != SEGUIMIENTO_COLUMNS


def test_ordena_la_zona_viva_por_fecha_del_pedido_desc(session):
    """La zona viva va por fecha del pedido, de más reciente a más antiguo (los
    sin fecha al final). La Situación es una columna más: se colorea y se
    reordena con el autofiltro, pero no es el criterio de orden."""
    rows = [
        _row("listo", "L-1", fecha="2026-09-01"),
        _row("incidencias", "I-1", fecha="2026-09-03"),
        _row("por_cobrar", "C-1", fecha=None),
        _row("por_revisar", "R-1", fecha="2026-09-02"),
    ]
    sheets = FakeTabs()
    push_managed_tabs(session, sheets, rows)
    escritas = sheets.written[DEFAULT_MANAGED_TAB][1:]
    assert [f[1] for f in escritas] == ["I-1", "R-1", "L-1", "C-1"]
    # El color de Situación va por índice de fila: el formato usa el MISMO
    # orden que el grid (la fila 1 es I-1, roja; la 3 es L-1, verde).
    fondos = {
        r["repeatCell"]["range"]["startRowIndex"]:
            r["repeatCell"]["cell"]["userEnteredFormat"]["backgroundColor"]
        for r in sheets.formats[DEFAULT_MANAGED_TAB]
        if r.get("repeatCell", {}).get("range", {}).get("startColumnIndex") == 0
        and r["repeatCell"]["range"]["startRowIndex"] > 0
    }
    rojo = int(SITUACION_FILL["incidencias"][0][0:2], 16) / 255
    verde = int(SITUACION_FILL["listo"][0][0:2], 16) / 255
    assert fondos[1]["red"] == pytest.approx(rojo)
    assert fondos[3]["red"] == pytest.approx(verde)


def test_congela_la_cabecera_y_pone_autofiltro_en_las_dos_pestanas(session):
    """La fila 1 (los 17 encabezados) queda congelada al hacer scroll y es la
    cabecera del autofiltro, que cubre SOLO la zona viva (cabecera + vivos),
    nunca el histórico de debajo del separador."""
    historico = [
        [f"{SEPARATOR_PREFIX} HISTÓRICO — no se actualiza {SEPARATOR_PREFIX}"],
        ["Histórico", "VIEJO-1", "1/1/2020", "Cliente viejo"],
    ]
    sheets = FakeTabs({HISTORICA: [], DEFAULT_MANAGED_TAB: [
        list(SEGUIMIENTO_COLUMNS_V2), *historico,
    ]})
    push_managed_tabs(session, sheets, [_row("listo", "L-1"), _row("listo", "L-2")])
    for tab, columnas, vivas in (
        (DEFAULT_MANAGED_TAB, len(SEGUIMIENTO_COLUMNS_V2), 2),
        (DEFAULT_INCIDENCIAS_TAB, len(INCIDENCIAS_COLUMNS), 0),
    ):
        reqs = sheets.formats[tab]
        frozen = [r for r in reqs if "updateSheetProperties" in r]
        assert frozen[0]["updateSheetProperties"]["properties"]["gridProperties"] == {
            "frozenRowCount": 1,
        }
        assert frozen[0]["updateSheetProperties"]["fields"] == "gridProperties.frozenRowCount"
        filtro = next(r for r in reqs if "setBasicFilter" in r)["setBasicFilter"]["filter"]["range"]
        assert filtro["startRowIndex"] == 0             # la cabecera es la del filtro
        assert filtro["endRowIndex"] == vivas + 1       # … y no llega al histórico
        assert filtro["endColumnIndex"] == columnas


def test_colorea_la_celda_situacion_con_los_tonos_del_diseno(session):
    # I-1 más reciente → primera fila viva (el orden es por fecha desc).
    rows = [_row("incidencias", "I-1", fecha="2026-09-02"),
            _row("listo", "L-1", fecha="2026-09-01")]
    sheets = FakeTabs()
    push_managed_tabs(session, sheets, rows)
    fondos = [
        r["repeatCell"]["cell"]["userEnteredFormat"]["backgroundColor"]
        for r in sheets.formats[DEFAULT_MANAGED_TAB]
        if r.get("repeatCell", {}).get("range", {}).get("startColumnIndex") == 0
        and r["repeatCell"]["range"]["startRowIndex"] > 0
    ]
    esperado_incidencia = SITUACION_FILL["incidencias"][0]
    assert fondos[0]["red"] == pytest.approx(int(esperado_incidencia[0:2], 16) / 255)
    assert len(fondos) == 2


def test_incidencias_es_el_subconjunto_exacto(session):
    # Mismo orden que la hoja (fecha desc): I-1 es más reciente que I-2.
    rows = [_row("incidencias", "I-1", fecha="2026-09-03"), _row("listo", "L-1"),
            _row("incidencias", "I-2", fecha="2026-09-02")]
    sheets = FakeTabs()
    resumen = push_managed_tabs(session, sheets, rows)
    inc = sheets.written[DEFAULT_INCIDENCIAS_TAB][1:]
    assert [f[0] for f in inc] == ["I-1", "I-2"]
    assert resumen["incidencias"] == 2


def test_es_idempotente(session):
    """Reejecutar deja la pestaña igual: se reescribe entera, no se añade."""
    rows = [_row("listo", "L-1"), _row("incidencias", "I-1")]
    sheets = FakeTabs()
    push_managed_tabs(session, sheets, rows)
    primera = list(sheets.written[DEFAULT_MANAGED_TAB])
    push_managed_tabs(session, sheets, rows)
    assert sheets.written[DEFAULT_MANAGED_TAB] == primera
    assert sheets.created == [DEFAULT_MANAGED_TAB, DEFAULT_INCIDENCIAS_TAB]


def test_dry_run_no_escribe_pero_resume(session):
    rows = [_row("incidencias", "I-1"), _row("listo", "L-1")]
    sheets = FakeTabs()
    resumen = push_managed_tabs(session, sheets, rows, dry_run=True)
    assert sheets.written == {} and sheets.created == []
    assert resumen["written"] is False and resumen["dry_run"] is True
    assert resumen["rows"] == 2 and resumen["incidencias"] == 1
    assert resumen["por_situacion"] == {"Incidencia": 1, "Listo": 1}


def test_sin_filas_escribe_solo_la_cabecera(session):
    sheets = FakeTabs()
    push_managed_tabs(session, sheets, [])
    assert sheets.written[DEFAULT_MANAGED_TAB] == [SEGUIMIENTO_COLUMNS_V2]


def test_el_grid_usa_la_misma_serializacion_que_el_excel():
    """No se duplica la serialización: la fila de Drive es la de
    `row_to_pedidos_values`."""
    from app.erp.drive_managed import dates_to_serial
    from app.erp.seguimiento import row_to_pedidos_values

    row = _row("por_enviar", "BOP-9")
    # Misma fila; en Drive las fechas van como serial (valor de fecha).
    assert build_pedidos_grid([row])[1] == dates_to_serial(
        [row_to_pedidos_values(row)], PEDIDOS_DATE_COLUMNS,
    )[0]
    assert build_incidencias_grid([_row("incidencias", "I-1")])[0] == INCIDENCIAS_COLUMNS


def test_el_formato_deja_el_hueco_del_sheet_id():
    """El constructor del formato no conoce el id de la pestaña (se crea sobre
    la marcha): lo deja a None y el cliente lo rellena."""
    reqs = pedidos_format([_row("listo", "L-1")])
    assert all("sheetId" in str(r) for r in reqs)
    resuelto = _with_sheet_id(reqs, 42)
    assert "'sheetId': None" not in str(resuelto)
    assert "42" in str(resuelto)


# --- zonas: viva arriba, estática debajo del separador -------------------------


def test_el_actualizar_preserva_el_bloque_historico(session):
    """Lo que de verdad da miedo: que «Actualizar» se lleve por delante los
    ~7.800 pedidos del histórico. La zona viva se regenera; del separador hacia
    abajo se preserva tal cual."""
    historico = [
        [f"{SEPARATOR_PREFIX} HISTÓRICO — no se actualiza {SEPARATOR_PREFIX}"],
        ["Histórico", "VIEJO-1", "1/1/2020", "Cliente viejo"],
        ["Histórico", "VIEJO-2", "2/1/2020", "Otro viejo"],
    ]
    sheets = FakeTabs({HISTORICA: [], DEFAULT_MANAGED_TAB: [
        list(SEGUIMIENTO_COLUMNS_V2), ["Listo", "L-0"], *historico,
    ]})
    push_managed_tabs(session, sheets, [_row("listo", "L-1", fecha="2026-09-01"),
                                        _row("incidencias", "I-1", fecha="2026-09-02")])
    escrito = sheets.written[DEFAULT_MANAGED_TAB]
    sep = next(i for i, r in enumerate(escrito) if is_separator(r))
    # Zona viva NUEVA (el L-0 de antes ya no está), por fecha desc, y el
    # histórico intacto, con su propio orden. Lo ÚNICO que cambia en el
    # histórico es que sus fechas legibles pasan a valor de fecha (serial),
    # para que la hoja ordene por fecha también ahí.
    assert [f[1] for f in escrito[1:sep]] == ["I-1", "L-1"]
    assert escrito[sep] == historico[0]
    assert escrito[sep + 1] == ["Histórico", "VIEJO-1", sheet_serial(date(2020, 1, 1)),
                                "Cliente viejo"]
    assert escrito[sep + 2] == ["Histórico", "VIEJO-2", sheet_serial(date(2020, 1, 2)),
                                "Otro viejo"]


def test_las_fechas_se_escriben_como_valor_de_fecha(session):
    """Las fechas van como serial de Sheets con `numberFormat` DD/MM/AAAA, no
    como texto: solo así la hoja ordena por fecha de verdad (como texto,
    «1/9/2026» va antes que «12/3/2026»). Vale para la zona viva y para el
    bloque estático; una fecha ROTA de origen se queda como texto, sin
    inventar nada."""
    historico = [
        [f"{SEPARATOR_PREFIX} HISTÓRICO {SEPARATOR_PREFIX}"],
        ["Histórico", "V-1", "27/07/202", "Cliente", "", "", "", "", "", "",
         "2023-01-17"],                                   # fecha ROTA + ISO
    ]
    sheets = FakeTabs({HISTORICA: [], DEFAULT_MANAGED_TAB: [
        list(SEGUIMIENTO_COLUMNS_V2), *historico,
    ]})
    push_managed_tabs(session, sheets, [_row(
        "listo", "L-1", fecha="2026-09-01", fecha_factura="2026-09-03",
        factura_enviada=None, recogido="2026-09-02",
    )])
    escrito = sheets.written[DEFAULT_MANAGED_TAB]
    viva = escrito[1]
    assert viva[2] == sheet_serial(date(2026, 9, 1))          # Fecha
    assert viva[9] == sheet_serial(date(2026, 9, 3))          # Fecha factura
    assert viva[10] == ""                                     # sin fecha
    assert viva[14] == sheet_serial(date(2026, 9, 2))         # Fecha recogido
    assert escrito[3][2] == "27/07/202"                       # rota: texto
    assert escrito[3][10] == sheet_serial(date(2023, 1, 17))  # ISO: fecha
    # Y el numberFormat de fecha cubre las 4 columnas, zona viva + estática.
    fmts = [r["repeatCell"] for r in sheets.formats[DEFAULT_MANAGED_TAB]
            if r.get("repeatCell", {}).get("cell", {}).get("userEnteredFormat", {})
            .get("numberFormat", {}).get("type") == "DATE"]
    assert sorted(f["range"]["startColumnIndex"] for f in fmts) == [2, 9, 10, 14]
    assert all(f["cell"]["userEnteredFormat"]["numberFormat"]["pattern"] == "dd/mm/yyyy"
               for f in fmts)
    assert all(f["range"]["startRowIndex"] == 1 and f["range"]["endRowIndex"] == len(escrito)
               for f in fmts)


def test_abre_el_hueco_de_fecha_recogido_en_un_historico_de_17_columnas(session):
    """La pestaña escrita con el formato de 17 columnas (#457) tiene el
    histórico sin «Fecha recogido»: al volcar con la cabecera nueva se abre el
    hueco en cada fila, para que Tracking, Nº serie y Nota no queden
    desplazadas. Con la cabecera nueva ya escrita, no se toca nada."""
    from app.erp.drive_managed import normalize_static_pedidos

    cabecera_17 = [c for c in SEGUIMIENTO_COLUMNS_V2 if c != "Fecha recogido"]
    fila_17 = ["Histórico", "V-1", "3/2/2026", "Roca", "WEB", "Cabezal", "", "BO",
               "1-260001", "", "", "", "", "UPS", "TRK1", "SN-7", "Orden: revisar"]
    sep = [f"{SEPARATOR_PREFIX} HISTÓRICO {SEPARATOR_PREFIX}"]
    migrado = normalize_static_pedidos([sep, fila_17], cabecera_17)
    assert migrado[1][14] == ""                      # el hueco nuevo
    assert migrado[1][15] == "TRK1"                  # Tracking, en su sitio
    assert migrado[1][16] == "SN-7"
    assert migrado[1][17] == "Orden: revisar"
    assert migrado[1][2] == sheet_serial(date(2026, 2, 3))
    assert len(migrado[1]) == len(SEGUIMIENTO_COLUMNS_V2)
    # Con la cabecera actual, idempotente (solo las fechas).
    otra = normalize_static_pedidos(migrado, list(SEGUIMIENTO_COLUMNS_V2))
    assert otra == migrado
    # Y de punta a punta: una pestaña vieja se realinea al volcar.
    sheets = FakeTabs({HISTORICA: [], DEFAULT_MANAGED_TAB: [cabecera_17, sep, fila_17]})
    push_managed_tabs(session, sheets, [_row("listo", "L-1")])
    escrito = sheets.written[DEFAULT_MANAGED_TAB]
    assert escrito[0] == SEGUIMIENTO_COLUMNS_V2
    assert escrito[-1][15] == "TRK1" and escrito[-1][14] == ""


def test_preserva_el_historico_aunque_cambie_el_numero_de_vivos(session):
    """Con más o menos vivos, el bloque estático sale exactamente igual: no hay
    aritmética de filas que pueda descuadrarlo."""
    historico = [[f"{SEPARATOR_PREFIX} HISTÓRICO {SEPARATOR_PREFIX}"],
                 ["Histórico", "VIEJO-1"]]
    sheets = FakeTabs({HISTORICA: [], DEFAULT_MANAGED_TAB: [
        list(SEGUIMIENTO_COLUMNS_V2), *historico,
    ]})
    push_managed_tabs(session, sheets, [_row("listo", f"L-{i}") for i in range(5)])
    tras_cinco = sheets.written[DEFAULT_MANAGED_TAB]
    assert tras_cinco[-len(historico):] == historico

    sheets.tabs[DEFAULT_MANAGED_TAB] = tras_cinco
    push_managed_tabs(session, sheets, [_row("listo", "L-0")])
    tras_uno = sheets.written[DEFAULT_MANAGED_TAB]
    assert tras_uno[-len(historico):] == historico
    assert len(tras_uno) == 1 + 1 + len(historico)


def test_no_duplica_el_separador_al_reejecutar(session):
    historico = [[f"{SEPARATOR_PREFIX} HISTÓRICO {SEPARATOR_PREFIX}"],
                 ["Histórico", "VIEJO-1"]]
    sheets = FakeTabs({HISTORICA: [], DEFAULT_MANAGED_TAB: [
        list(SEGUIMIENTO_COLUMNS_V2), *historico,
    ]})
    for _ in range(3):
        push_managed_tabs(session, sheets, [_row("listo", "L-1")])
        sheets.tabs[DEFAULT_MANAGED_TAB] = sheets.written[DEFAULT_MANAGED_TAB]
    escrito = sheets.written[DEFAULT_MANAGED_TAB]
    assert sum(1 for r in escrito if is_separator(r)) == 1


def test_sin_separador_no_se_inventa_zona_estatica(session):
    """Una pestaña escrita antes de que existieran las zonas es toda viva:
    adivinar dónde empezaría el histórico sería inventarse un bloque."""
    sheets = FakeTabs({HISTORICA: [], DEFAULT_MANAGED_TAB: [
        list(SEGUIMIENTO_COLUMNS_V2), ["Listo", "L-0"],
    ]})
    push_managed_tabs(session, sheets, [_row("listo", "L-1")])
    escrito = sheets.written[DEFAULT_MANAGED_TAB]
    assert escrito == [list(SEGUIMIENTO_COLUMNS_V2),
                       *[f for f in escrito[1:]]]
    assert not any(is_separator(r) for r in escrito)
    assert [f[1] for f in escrito[1:]] == ["L-1"]


def test_el_actualizar_preserva_los_pendientes_heredados(session):
    """Lo mismo en Incidencias: arriba las manuales, debajo los heredados."""
    heredados = [[f"{SEPARATOR_PREFIX} PENDIENTES HEREDADOS {SEPARATOR_PREFIX}"],
                 ["VIEJO-9", "Institut Les Vinyes", TIPO_PENDIENTE]]
    sheets = FakeTabs({HISTORICA: [], DEFAULT_INCIDENCIAS_TAB: [
        list(INCIDENCIAS_COLUMNS), *heredados,
    ]})
    push_managed_tabs(session, sheets, [_row("incidencias", "I-1")])
    escrito = sheets.written[DEFAULT_INCIDENCIAS_TAB]
    sep = next(i for i, r in enumerate(escrito) if is_separator(r))
    assert [f[0] for f in escrito[1:sep]] == ["I-1"]
    assert escrito[sep:] == heredados


def test_la_vista_previa_cuenta_lo_que_se_preserva(session):
    historico = [[f"{SEPARATOR_PREFIX} HISTÓRICO {SEPARATOR_PREFIX}"],
                 ["Histórico", "V-1"], ["Histórico", "V-2"]]
    sheets = FakeTabs({HISTORICA: [], DEFAULT_MANAGED_TAB: [
        list(SEGUIMIENTO_COLUMNS_V2), *historico,
    ]})
    resumen = push_managed_tabs(session, sheets, [_row("listo", "L-1")],
                                dry_run=True)
    assert resumen["historico_preservado"] == 2
    assert sheets.written == {}


def test_static_block_solo_desde_el_separador():
    values = [["Situación"], ["Listo", "L-1"],
              [f"{SEPARATOR_PREFIX} X"], ["Histórico", "V-1"]]
    assert static_block(values) == [[f"{SEPARATOR_PREFIX} X"], ["Histórico", "V-1"]]
    assert static_block([["Situación"], ["Listo", "L-1"]]) == []


# --- importación del histórico -------------------------------------------------


HEADER_VIEJA = list(SEGUIMIENTO_COLUMNS)
_IDX = {name: i for i, name in enumerate(SEGUIMIENTO_COLUMNS)}


def _vieja(**cells: str) -> list[str]:
    out = [""] * len(HEADER_VIEJA)
    for name, value in cells.items():
        out[_IDX[name]] = value
    return out


def _hoja_vieja() -> list[list[str]]:
    return [
        HEADER_VIEJA,
        _vieja(**{"Albarán / Nº Pedido Web": "99866", "Cliente": "Roca",
                  "Fecha entrada albarán": "3/2/2026", "Empresa": "BO",
                  "Nº de Factura": "1-260001", "Tracking": "TRK1",
                  "Nº de Serie": "SN-7", "WhiteRIP": "sí",
                  "Orden": "revisar con Marta", "Vendedor": "Bart"}),
        ["^^^^  Aqui arriba pedidos que faltan entregar ."],
        [],
        HEADER_VIEJA,                                   # cabecera repetida
        _vieja(**{"Albarán / Nº Pedido Web": "99867", "Cliente": "Duaner"}),
        _vieja(**{"Cliente": "Nota suelta sin pedido"}),  # dudosa
    ]


def test_plan_import_parte_la_hoja_en_el_marcador():
    """El «^^^^» separa dos mundos: encima, pedidos que faltan entregar (lista
    de trabajo, NO histórico); debajo, lo entregado."""
    plan = plan_import(_hoja_vieja())
    assert plan["pendientes"] == 1        # la de encima del «^^^^»
    assert plan["pendientes_rows"][0][0] == "99866"
    assert plan["pendientes_rows"][0][2] == TIPO_PENDIENTE
    assert plan["mapeadas"] == 2          # 1 pedido + 1 dudosa, de debajo
    assert plan["descartadas"] == 3       # «^^^^», vacía, cabecera repetida
    assert plan["dudosas"] == 1
    assert plan["dudosas_muestra"][0]["cliente"] == "Nota suelta sin pedido"
    assert "Albarán / Nº Pedido Web" in plan["columnas_reconocidas"]


def test_plan_import_sin_marcador_todo_es_historico():
    """Una hoja sin «^^^^» no tiene lista de pendientes: todo es archivo."""
    plan = plan_import([HEADER_VIEJA, _vieja(**{"Albarán / Nº Pedido Web": "1"})])
    assert plan["pendientes"] == 0 and plan["mapeadas"] == 1
    assert plan["marcador_fila"] is None


def test_map_pendiente_hereda_cliente_vendedor_y_productos():
    from app.erp.drive_historico import map_pendiente
    from app.erp.seguimiento import match_header_columns

    col_map = match_header_columns(HEADER_VIEJA)
    fila = map_pendiente(_hoja_vieja()[1], col_map)
    assert fila[0] == "99866"              # Nº pedido
    assert fila[1] == "Roca"               # Cliente
    assert fila[2] == TIPO_PENDIENTE       # Tipo
    assert "revisar con Marta" in fila[3]  # Motivo (productos + nota)
    assert fila[4] == "Bart"               # Asignado a (vendedor)
    assert fila[6] == "Abierta"            # Estado
    assert len(fila) == len(INCIDENCIAS_COLUMNS)


def test_import_historico_deja_los_pendientes_en_incidencias():
    sheets = FakeTabs({HISTORICA: _hoja_vieja()})
    import_historico(sheets, pedidos_tab=DEFAULT_MANAGED_TAB,
                     incidencias_tab=DEFAULT_INCIDENCIAS_TAB, dry_run=False)
    inc = sheets.written[DEFAULT_INCIDENCIAS_TAB]
    assert inc[0] == INCIDENCIAS_COLUMNS
    sep = next(i for i, r in enumerate(inc) if is_separator(r))
    assert [f[0] for f in inc[sep + 1:]] == ["99866"]
    assert inc[sep + 1][2] == TIPO_PENDIENTE


def test_map_row_reparte_a_columnas_y_conserva_lo_que_no_tiene_columna():
    """Vendedor → Origen, Transporte → Envío, Preparado → Preparación,
    Recogido → «Fecha recogido»; solo «Orden» (las notas a mano) y Proforma
    van a «Nota / Incidencia», que ya no los repite."""
    from app.erp.seguimiento import match_header_columns

    col_map = match_header_columns(HEADER_VIEJA)
    fila = map_row(_vieja(**{
        "Albarán / Nº Pedido Web": "99866", "Cliente": "Roca",
        "Fecha entrada albarán": "3/2/2026", "Vendedor": "WEB",
        "OFI-TER-SAT": "SAT", "Transport": "UPS", "Preparado": "16/01/2023",
        "Recogido": "17/01/2023", "Proforma": "296", "Orden": "revisar con Marta",
        "Nº de Serie": "SN-7", "WhiteRIP": "sí",
    }), col_map)
    assert fila[0] == SITUACION_HISTORICO
    assert fila[1] == "99866"                 # Nº pedido
    assert fila[2] == "3/2/2026"              # Fecha (texto: el volcado la pasa a fecha)
    assert fila[3] == "Roca"                  # Cliente
    assert fila[4] == "WEB · SAT"             # Origen = vendedor + canal
    assert fila[12] == "16/01/2023"           # Preparación ← Preparado
    assert fila[13] == "UPS"                  # Envío ← Transporte
    assert fila[14] == "17/01/2023"           # Fecha recogido ← Recogido
    assert fila[16] == "SN-7 · sí"            # Nº serie · WhiteRIP
    assert fila[17] == "Orden: revisar con Marta · Proforma: 296"
    assert "UPS" not in fila[17] and "WEB" not in fila[17]
    assert len(fila) == len(SEGUIMIENTO_COLUMNS_V2)


def test_import_historico_escribe_las_fechas_como_fecha():
    """Las fechas del histórico (entrada, envío de factura, recogido) van como
    valor de fecha con su `numberFormat`; una rota, como texto."""
    hoja = [
        HEADER_VIEJA,
        _vieja(**{"Albarán / Nº Pedido Web": "1", "Fecha entrada albarán": "3/2/2026",
                  "Recogido": "17/01/2023", "F Envío Factura": "27/07/202"}),
    ]
    sheets = FakeTabs({HISTORICA: hoja})
    import_historico(sheets, pedidos_tab=DEFAULT_MANAGED_TAB,
                     incidencias_tab=DEFAULT_INCIDENCIAS_TAB, dry_run=False)
    destino = sheets.written[DEFAULT_MANAGED_TAB]
    fila = destino[-1]
    assert fila[2] == sheet_serial(date(2026, 2, 3))
    assert fila[14] == sheet_serial(date(2023, 1, 17))
    assert fila[10] == "27/07/202"
    formatos = [r["repeatCell"]["cell"]["userEnteredFormat"]
                for r in sheets.formats[DEFAULT_MANAGED_TAB] if "repeatCell" in r]
    tipos = [f["numberFormat"]["type"] for f in formatos if "numberFormat" in f]
    assert tipos.count("DATE") == len(PEDIDOS_DATE_COLUMNS)


def test_import_historico_escribe_en_otra_pestana_y_no_toca_la_vieja():
    sheets = FakeTabs({HISTORICA: _hoja_vieja()})
    original = [list(r) for r in sheets.tabs[HISTORICA]]
    resumen = import_historico(sheets, pedidos_tab=DEFAULT_MANAGED_TAB,
                            incidencias_tab=DEFAULT_INCIDENCIAS_TAB,
                            dry_run=False)
    assert resumen["written"] is True
    assert resumen["origen"] == HISTORICA
    assert sheets.tabs[HISTORICA] == original      # intacta
    destino = sheets.written[DEFAULT_MANAGED_TAB]
    assert destino[0] == SEGUIMIENTO_COLUMNS_V2
    # El histórico va DEBAJO del separador, no mezclado con la zona viva.
    sep = next(i for i, r in enumerate(destino) if is_separator(r))
    assert all(f[0] == SITUACION_HISTORICO for f in destino[sep + 1:])


def test_import_historico_dry_run_no_escribe():
    sheets = FakeTabs({HISTORICA: _hoja_vieja()})
    resumen = import_historico(sheets, pedidos_tab=DEFAULT_MANAGED_TAB,
                            incidencias_tab=DEFAULT_INCIDENCIAS_TAB)
    assert resumen["written"] is False and sheets.written == {}
    assert resumen["mapeadas"] == 2 and resumen["pendientes"] == 1


def test_import_historico_se_niega_a_escribir_sobre_la_vieja():
    sheets = FakeTabs({HISTORICA: _hoja_vieja()})
    with pytest.raises(DriveSyncError):
        import_historico(sheets, pedidos_tab=HISTORICA,
                         incidencias_tab=DEFAULT_INCIDENCIAS_TAB,
                         dry_run=False)
    assert sheets.written == {}


def test_import_historico_sin_cabecera_reconocible_falla_claro():
    sheets = FakeTabs({HISTORICA: [["a", "b"], ["c", "d"]]})
    with pytest.raises(DriveSyncError) as exc:
        import_historico(sheets, pedidos_tab=DEFAULT_MANAGED_TAB,
                         incidencias_tab=DEFAULT_INCIDENCIAS_TAB)
    assert "cabecera" in str(exc.value)
