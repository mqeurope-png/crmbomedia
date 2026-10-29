# ruff: noqa: F811 — las fixtures de `test_erp_bank_drive` se importan por nombre
"""El export del banco, directo a la hoja de Drive (29/10/2026).

Bart baja de la web de Banc Sabadell un .xls con los movimientos de los
últimos días y, hasta ahora, copiaba a mano a su hoja los que faltaban. Ahora
lo sube tal cual: BoHub dice cuántos trae, cuántos ya están y cuáles faltan, y
al confirmar los añade arriba de la hoja (de más reciente a más antiguo, con
factura y pedido vacías) sin tocar ninguna fila que ya estuviera.

La hoja corta el concepto a 26 caracteres y el export lo trae entero: el
mismo movimiento se reconoce por fecha, importe y saldo (`mismo_movimiento`),
en el importador, al escribir la factura en la hoja y al añadir el export.

Datos FIGURANTES (los de `test_erp_bank_drive`) y Google en memoria.
"""
from __future__ import annotations

import io
from datetime import date, datetime
from typing import Any

import xlwt
from sqlalchemy import func, select

from app.erp.bank import drive
from app.erp.bank.parsing import (
    Identidades,
    interpretar,
    mismo_movimiento,
    read_statement,
)
from app.erp.models import BankMovement
from tests._test_helpers import auth_headers
from tests.test_erp_bank_drive import (  # noqa: F401 — fixtures
    COL_FACTURA,
    COL_PEDIDO,
    DATOS,
    PESTANA,
    STREAMTEC_CCC,
    _d,
    _elegir,
    _escribir,
    _mov,
    _sabadell,
    _traer,
    cuenta,
    hoja,
    http,
    session_factory,
)

#: Lo que trae la web de Sabadell hoy: tres que la hoja no tiene (uno de
#: ellos, del 18, más antiguo que los de arriba) y los seis de la hoja con el
#: concepto entero.
NUEVOS = [
    ["27/10/2026", "ABONO TRANSFERENCIA DE CRISTALERIA PUIG SL", "27/10/2026", 650.00,
     10492.11, "", "CRISTALERIA PUIG"],
    ["24/10/2026", "ABONO TRANSFERENCIA DE LLIBRERIA COSTA I FILLS", "24/10/2026", 1507.59,
     11349.70, "", "LLIBRERIA COSTA"],
    ["18/10/2026", "ABONO TRANSFERENCIA DE FERRETERIA VIDAL", "18/10/2026", 44.77,
     5328.10, "", "FERRETERIA VIDAL"],
]


def _de_la_hoja(f: list) -> list:
    """Un movimiento de la hoja tal como lo da el export: concepto entero."""
    f = [*f, None, None, None]
    return [f[0].strftime("%d/%m/%Y"), f[1], f[2].strftime("%d/%m/%Y"), f[3], f[4],
            "" if f[5] is None else f[5], f[6] or ""]


def _export(filas: list[list]) -> bytes:
    """Un .xls como el de la web de Sabadell: preámbulo de siete filas, una en
    blanco y la cabecera «F. Operativa | Concepto | …» en la novena."""
    libro = xlwt.Workbook()
    ws = libro.add_sheet("Movimientos")
    preambulo = [
        ["Consulta de movimientos"], [], ["Cuenta:", STREAMTEC_CCC], ["Divisa:", "EUR"],
        ["Fecha:", "29/10/2026"], ["Hora:", "02:02"], ["Titular:", "STREAMTEC S.L"], [],
        ["F. Operativa", "Concepto", "F. Valor", "Importe", "Saldo", "Referencia 1",
         "Referencia 2"],
    ]
    for r, fila in enumerate([*preambulo, *filas]):
        for c, v in enumerate(fila):
            ws.write(r, c, v)
    buf = io.BytesIO()
    libro.save(buf)
    return buf.getvalue()


def _hoja_cortada(hoja) -> None:
    """La hoja de Bart: el concepto, cortado a 26 caracteres."""
    hoja.grid = _sabadell([[f[0], f[1][:26], *f[2:]] for f in DATOS])


EXPORT = _export([NUEVOS[0], NUEVOS[1], *[_de_la_hoja(f) for f in DATOS[:4]], NUEVOS[2],
                  *[_de_la_hoja(f) for f in DATOS[4:]]])


def _anadir(http, cuenta: str, contenido: bytes = EXPORT, *, aplicar: bool = False,
            nombre: str = "29102026_0202_0001259030.xls") -> Any:
    return http.post(
        "/api/erp/bank/drive/anadir",
        files={"file": (nombre, contenido, "application/vnd.ms-excel")},
        data={"account_id": cuenta, "aplicar": str(aplicar).lower(), "run_match": "false"},
        headers=auth_headers(http, "admin"),
    )


def _cuantos(session_factory) -> int:
    with session_factory() as s:
        return s.scalar(select(func.count(BankMovement.id)))


# --- el fichero del banco ------------------------------------------------------------------


def test_el_xls_de_sabadell_se_lee_con_sus_cabeceras_como_las_de_la_hoja():
    parsed = read_statement(EXPORT, "29102026_0202_0001259030.xls")
    assert parsed.formato == "Sabadell"
    assert parsed.columns_row == 9
    assert parsed.header["numero_cuenta"].replace("-", "").endswith("0200051332")
    lectura = interpretar(parsed, parsed.mapping)
    assert len(lectura.movimientos) == 9 and not lectura.no_entendidas
    m = lectura.movimientos[0]
    assert (m["fecha_oper"], m["importe"], m["saldo"]) == (date(2026, 10, 27), 650.00, 10492.11)
    # «F. Operativa / F. Valor / Importe» son «FECHA OPER / FECHA VALOR / IMPORTE».
    assert parsed.mapping["fecha_oper"] == "F. Operativa"
    assert parsed.mapping["fecha_valor"] == "F. Valor"


def test_un_xls_se_reconoce_por_lo_que_es_no_por_su_nombre():
    parsed = read_statement(EXPORT, "extracto")          # sin extensión
    assert parsed.formato == "Sabadell"


# --- la identidad de un movimiento -----------------------------------------------------------


def _m(**kw: Any) -> dict[str, Any]:
    base = {"fecha_oper": date(2026, 10, 21), "importe": 2310.0, "saldo": 8001.81,
            "concepto": "ABONO TRANSFERENCIA DE IMP", "referencia1": None}
    return {**base, **kw}


def test_el_concepto_cortado_y_el_entero_son_el_mismo_movimiento():
    entero = _m(concepto="ABONO TRANSFERENCIA DE IMPRENTA SOLER")
    assert mismo_movimiento(_m(), entero)
    # Sin saldo, por el prefijo común del concepto.
    assert mismo_movimiento(_m(saldo=None), {**entero, "saldo": None})
    assert not mismo_movimiento(_m(saldo=None), _m(saldo=None, concepto="ABONO TRANSF. DE OTRO"))
    # Otro saldo, otro movimiento (dos iguales el mismo día).
    assert not mismo_movimiento(_m(), _m(saldo=10311.81))
    assert not mismo_movimiento(_m(), _m(importe=2310.01))


def test_cada_conocido_vale_para_uno_solo():
    ids = Identidades([("a", _m(saldo=None)), ("b", _m(saldo=None))])
    assert [ids.buscar(_m(saldo=None)) for _ in range(3)] == ["a", "b", None]


# --- vista previa y añadir -------------------------------------------------------------------


def test_la_vista_previa_cuenta_antes_y_no_toca_nada(http, session_factory, cuenta, hoja):
    _hoja_cortada(hoja)
    antes = [list(f) for f in hoja.grid]
    r = _anadir(http, cuenta)
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["total"], body["ya"], body["nuevos"], body["aplicado"]) == (9, 6, 3, False)
    assert [m["concepto"] for m in body["movimientos"]] == [f[1] for f in NUEVOS]
    assert hoja.grid == antes and _cuantos(session_factory) == 0


def test_al_confirmar_se_anaden_arriba_sin_tocar_ninguna_fila(
    http, session_factory, cuenta, hoja,
):
    _hoja_cortada(hoja)
    antes = [list(f) for f in hoja.grid]
    body = _anadir(http, cuenta, aplicar=True).json()
    assert (body["nuevos"], body["aplicado"]) == (3, True)
    # Debajo de la cabecera y los blancos, encima de la primera de datos (fila 10).
    fila, filas = hoja.insertadas
    assert fila == 10
    assert [f[1] for f in filas] == [f[1] for f in NUEVOS]       # 27, 24 y 18
    assert all(f[COL_FACTURA] is None and f[COL_PEDIDO] is None for f in filas)
    assert hoja.grid[9][0] == datetime(2026, 10, 27)            # la fecha, como fecha
    assert hoja.grid[9][3] == 650.00 and hoja.grid[9][4] == 10492.11
    # Las de antes, idénticas y en el mismo orden, tres filas más abajo.
    assert hoja.grid[:9] == antes[:9] and hoja.grid[12:] == antes[9:]
    assert hoja.escrituras == []                                # ni una celda suelta
    # Y entran en BoHub sin «Traer de Drive»: los seis de la hoja y los tres.
    assert body["importacion"]["imported"] == 9
    assert _cuantos(session_factory) == 9
    # Volver a subirlo no añade nada.
    again = _anadir(http, cuenta, aplicar=True).json()
    assert (again["ya"], again["nuevos"], again["aplicado"]) == (9, 0, False)
    assert len(hoja.grid) == len(antes) + 3


def test_sin_nada_nuevo_no_se_escribe(http, session_factory, cuenta, hoja):
    solo_hoja = _export([_de_la_hoja(f) for f in DATOS])
    antes = [list(f) for f in hoja.grid]
    body = _anadir(http, cuenta, solo_hoja, aplicar=True).json()
    assert (body["total"], body["ya"], body["nuevos"], body["aplicado"]) == (6, 6, 0, False)
    assert hoja.grid == antes and not hasattr(hoja, "insertadas")


def test_un_export_de_otra_cuenta_no_se_anade(http, cuenta, hoja):
    otra = _export([NUEVOS[0]]).replace(
        STREAMTEC_CCC.encode("latin-1"), b"0049-1500-05-1234567890")
    r = _anadir(http, cuenta, otra, aplicar=True)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "otra_cuenta"
    assert not hasattr(hoja, "insertadas")


def test_a_un_xlsx_de_drive_no_se_le_anade_nada(http, cuenta, hoja):
    hoja.formato = drive.FORMATO_XLSX
    r = _anadir(http, cuenta)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "xlsx"


# --- la misma identidad en el importador y al escribir ------------------------------------


def test_el_export_subido_a_mano_no_duplica_lo_que_vino_de_la_hoja(
    http, session_factory, cuenta, hoja,
):
    _hoja_cortada(hoja)
    assert _traer(http, cuenta)["imported"] == 6
    r = http.post(
        "/api/erp/bank/import",
        files={"file": ("29102026.xls", EXPORT, "application/vnd.ms-excel")},
        data={"account_id": cuenta, "run_match": "false"},
        headers=auth_headers(http, "admin"),
    )
    assert r.status_code == 201, r.text
    assert (r.json()["imported"], r.json()["duplicates"]) == (3, 6)
    assert _cuantos(session_factory) == 9


def test_la_factura_se_escribe_aunque_el_movimiento_entrara_con_el_concepto_entero(
    http, session_factory, cuenta, hoja,
):
    _hoja_cortada(hoja)
    r = http.post(
        "/api/erp/bank/import",
        files={"file": ("29102026.xls", EXPORT, "application/vnd.ms-excel")},
        data={"account_id": cuenta, "run_match": "false"},
        headers=auth_headers(http, "admin"),
    )
    assert r.status_code == 201, r.text
    soler = _mov(session_factory, "ABONO TRANSFERENCIA DE IMPRENTA SOLER")
    _elegir(http, soler.id, 260090, 2310.00)
    stats = _escribir(session_factory, cuenta)
    assert (stats["escritas"], stats["no_encontradas"]) == (1, 0)
    assert hoja.escrituras == [(f"'{PESTANA}'!H11", "5-260090")]
