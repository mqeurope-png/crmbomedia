"""Importación del histórico de la hoja vieja al formato NUEVO (una vez).

La pestaña histórica de Bart tiene miles de filas en el formato de siempre (17
columnas: Empresa, Fecha entrada albarán, Cliente, …, Orden). Este módulo las
LEE, las mapea a las 17 columnas del rediseño 2026 y las deja en su propia
pestaña «Histórico (formato nuevo)». La hoja vieja **no se toca**: sigue siendo
el archivo en bruto por si hay que revisar algo del mapeo.

Dónde se apoya el mapeo (y por qué es fiable): las columnas reales de la hoja
están modeladas en `seguimiento.SEG_COLUMNS` —con sus alias, verificados contra
la hoja de producción en ERP-F6— y la posición de cada una se DESCUBRE en
caliente con `match_header_columns`, no se da por supuesta. Si Bart mueve o
renombra una columna, el mapeo la sigue encontrando por alias, y el dry-run
dice exactamente qué ha reconocido.

Qué se descarta (no son pedidos): la fila del bloque «^^^^ Aquí arriba pedidos
que faltan…», las cabeceras repetidas a media hoja, los separadores y las filas
vacías — lo decide `is_structure_row`, la misma función que ya usa la
sincronización para no pisarlas.

Qué se conserva: lo que no tiene hueco limpio en el formato nuevo (la columna
manual «Orden», el vendedor, el transportista, las fechas de preparado/recogido)
se acumula en «Nota / Incidencia» en vez de perderse.

La Situación NO se puede recalcular para el histórico (haría falta el pedido en
BoHub, y estas filas son de años atrás), así que va fija a «Histórico». Así
tampoco compite con los pedidos vivos si alguien mira las dos pestañas.
"""

from __future__ import annotations

import logging
from typing import Any

from app.erp.drive_sheets import DriveSyncError, ManagedTabTransport
from app.erp.seguimiento import (
    SEG_COLUMNS,
    SEGUIMIENTO_COLUMNS_V2,
    is_structure_row,
    match_header_columns,
)

logger = logging.getLogger(__name__)

#: Pestaña donde aterriza el histórico convertido. Propia y aparte de la
#: gestionada: el volcado periódico reordena por Situación y se lo llevaría por
#: delante si compartieran pestaña.
DEFAULT_HISTORICO_TAB = "Histórico (formato nuevo)"
HISTORICO_TAB_SETTING = "drive_historico_tab"

#: Situación fija del histórico: no se puede recalcular en vivo.
SITUACION_HISTORICO = "Histórico"

#: Mínimo de columnas reconocidas para fiarse de la cabecera encontrada.
_MIN_HEADER_MATCHES = 5

_IDX = {c.header: i for i, c in enumerate(SEG_COLUMNS)}


def historico_tab_title(session: Any) -> str:
    from app.integrations.factusol.service import series_config  # noqa: PLC0415

    cfg = series_config(session)
    return str(cfg.get(HISTORICO_TAB_SETTING) or "").strip() or DEFAULT_HISTORICO_TAB


def _cell(row: list[Any], col_map: dict[int, int], header: str) -> str:
    """Valor de una columna lógica en esta fila, o "" si la hoja no la trae."""
    sheet_col = col_map.get(_IDX[header])
    if sheet_col is None or sheet_col >= len(row):
        return ""
    return str(row[sheet_col] or "").strip()


def locate_header(values: list[list[Any]]) -> tuple[int, dict[int, int]]:
    """(índice de la fila de cabecera, mapa columna lógica → columna de hoja).

    Se queda con la PRIMERA fila que reconozca bastantes columnas; mirar solo
    la fila 0 fallaría si la hoja tiene un título encima."""
    best: tuple[int, dict[int, int]] | None = None
    for i, row in enumerate(values[:20]):
        mapping = match_header_columns(list(row))
        if len(mapping) >= _MIN_HEADER_MATCHES:
            best = (i, mapping)
            break
    if best is None:
        raise DriveSyncError(
            "no se reconoce la cabecera de la hoja histórica: ninguna de las "
            f"primeras 20 filas casa {_MIN_HEADER_MATCHES} columnas conocidas"
        )
    return best


def _nota(row: list[Any], col_map: dict[int, int]) -> str:
    """Lo que no tiene columna propia en el formato nuevo, junto y etiquetado,
    para no perder nada del histórico."""
    piezas: list[str] = []
    for etiqueta, header in (
        ("Orden", "Orden"),                 # la columna de notas a mano
        ("Vendedor", "Vendedor"),
        ("Transporte", "Transport"),
        ("Preparado", "Preparado"),
        ("Recogido", "Recogido"),
        ("Proforma", "Proforma"),
    ):
        valor = _cell(row, col_map, header)
        if valor:
            piezas.append(f"{etiqueta}: {valor}")
    return " · ".join(piezas)[:500]


def map_row(row: list[Any], col_map: dict[int, int]) -> list[Any]:
    """Una fila de la hoja vieja → los 17 valores del formato nuevo.

    Lo que no existe en el histórico (Importe, Cobro, Preparación, Envío,
    Factura enviada) se deja vacío: inventarlo sería peor que no tenerlo."""
    num_serie = _cell(row, col_map, "Nº de Serie")
    whiterip = _cell(row, col_map, "WhiteRIP")
    serie_whiterip = " · ".join(p for p in (num_serie, whiterip) if p)
    return [
        SITUACION_HISTORICO,                                  # Situación
        _cell(row, col_map, "Albarán / Nº Pedido Web"),       # Nº pedido
        _cell(row, col_map, "Fecha entrada albarán"),         # Fecha
        _cell(row, col_map, "Cliente"),                       # Cliente
        _cell(row, col_map, "OFI-TER-SAT"),                   # Origen
        _cell(row, col_map, "Productos")[:300],               # Productos
        "",                                                   # Importe
        _cell(row, col_map, "Empresa"),                       # Empresa (serie)
        _cell(row, col_map, "Nº de Factura"),                 # Factura
        "",                                                   # Fecha factura
        _cell(row, col_map, "F Envío Factura"),               # Factura enviada
        "",                                                   # Cobro
        "",                                                   # Preparación
        "",                                                   # Envío
        _cell(row, col_map, "Tracking"),                      # Tracking
        serie_whiterip,                                       # Nº serie · WhiteRIP
        _nota(row, col_map),                                  # Nota / Incidencia
    ]


def plan_import(values: list[list[Any]]) -> dict[str, Any]:
    """Lee la hoja vieja y planifica la conversión SIN escribir nada.

    Devuelve el recuento honesto que pide el dry-run: cuántas filas se mapean,
    cuántas se descartan por ser estructura (el bloque «^^^^», cabeceras
    repetidas, vacías) y cuántas quedan DUDOSAS — filas que no son estructura
    pero tampoco traen nº de pedido, que es lo que identifica un pedido. Las
    dudosas se importan igualmente (con su nota) para no perder información,
    pero se listan para que alguien las mire."""
    header_row, col_map = locate_header(values)
    reconocidas = [SEG_COLUMNS[c].header for c in sorted(col_map)]
    ausentes = [c.header for i, c in enumerate(SEG_COLUMNS) if i not in col_map]

    mapeadas: list[list[Any]] = []
    dudosas: list[dict[str, Any]] = []
    descartadas = 0
    for i, raw in enumerate(values[header_row + 1:], start=header_row + 2):
        row = list(raw)
        if is_structure_row(row):
            descartadas += 1
            continue
        mapped = map_row(row, col_map)
        if not mapped[1]:                    # sin nº de pedido
            dudosas.append({"fila": i, "cliente": mapped[3],
                            "nota": mapped[16][:120]})
        mapeadas.append(mapped)

    return {
        "header_row": header_row + 1,
        "columnas_reconocidas": reconocidas,
        "columnas_ausentes": ausentes,
        "filas_leidas": max(len(values) - header_row - 1, 0),
        "mapeadas": len(mapeadas),
        "descartadas": descartadas,
        "dudosas": len(dudosas),
        "dudosas_muestra": dudosas[:20],
        "muestra": mapeadas[:5],
        "rows": mapeadas,
    }


def import_historico(
    sheets: ManagedTabTransport,
    *,
    tab_title: str = DEFAULT_HISTORICO_TAB,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Convierte el histórico y lo deja en su pestaña. `dry_run` (por defecto)
    solo informa.

    Nunca escribe en la hoja vieja: se lee con `get_values` y se escribe en
    otra pestaña. Reejecutar reescribe la pestaña de histórico entera, así que
    es idempotente."""
    historic = sheets.first_tab_title()
    if tab_title.strip().casefold() == historic.strip().casefold():
        raise DriveSyncError(
            f"«{tab_title}» es la pestaña histórica en bruto: el histórico "
            "convertido va a una pestaña APARTE, para no tocar el original"
        )
    plan = plan_import(sheets.tab_values(historic))
    resumen = {k: v for k, v in plan.items() if k != "rows"}
    resumen.update({"tab": tab_title, "origen": historic,
                    "dry_run": dry_run, "written": False})
    if dry_run:
        return resumen

    sheets.ensure_tab(tab_title)
    sheets.replace_tab(
        tab_title, [list(SEGUIMIENTO_COLUMNS_V2), *plan["rows"]],
    )
    from app.erp.drive_managed import _header_format  # noqa: PLC0415

    sheets.format_tab(
        tab_title, _header_format(len(SEGUIMIENTO_COLUMNS_V2), [110] * 17),
    )
    resumen["written"] = True
    logger.info(
        "drive: histórico convertido a «%s» (%d filas mapeadas, %d descartadas, "
        "%d dudosas); «%s» sin tocar",
        tab_title, plan["mapeadas"], plan["descartadas"], plan["dudosas"],
        historic,
    )
    return resumen
