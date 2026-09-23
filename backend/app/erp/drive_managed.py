"""Volcado del seguimiento (formato NUEVO) a su pestaña gestionada de Drive.

La hoja de Drive de Bart tiene dos mundos y no se mezclan:

- La pestaña **histórica** (la primera del documento) es suya: miles de filas
  con anotaciones a mano. La sincronización de siempre (`drive_sheets`) solo
  INSERTA ahí y jamás reescribe ni formatea. Este módulo **no la toca**, y lo
  comprueba antes de escribir (`_guard_not_historic`).
- La pestaña **gestionada** («Seguimiento (app)», configurable) es de la app:
  se reescribe entera en cada actualización con las 17 columnas del rediseño
  2026, ordenada por Situación y con la celda Situación coloreada — la misma
  forma que la pantalla y que «Descargar Excel», porque comparte la
  serialización (`row_to_pedidos_values`, `incidencia_values`).

Reescribir entera es lo correcto AQUÍ y sería un desastre en la histórica: esta
pestaña no tiene nada que conservar, así que el volcado es idempotente por
construcción (mismo contenido dentro → misma pestaña fuera, sin duplicados).

Cada pestaña gestionada tiene DOS ZONAS separadas por una fila marcador:

    cabecera
    zona VIVA        ← se regenera en cada «Actualizar»
    ──── separador ────
    zona ESTÁTICA    ← se preserva tal cual (histórico / pendientes heredados)

La escritura es leer-preservar-reescribir: antes de escribir se lee la pestaña,
se rescata lo que hay del separador hacia abajo y se vuelve a poner debajo de la
zona viva nueva. Se hace así, y no insertando/borrando filas por encima del
separador, porque no hay aritmética de filas que pueda descuadrarse: cambie como
cambie el número de vivos, el bloque estático sale exactamente igual que entró.
Y es idempotente: reejecutar no duplica el separador ni el bloque.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.erp.drive_sheets import DriveSyncError, ManagedTabTransport
from app.erp.seguimiento import (
    INCIDENCIAS_COLUMNS,
    SEGUIMIENTO_COLUMNS_V2,
    SITUACION_FILL,
    incidencia_rows,
    incidencia_values,
    row_to_pedidos_values,
    sort_by_situacion,
)

logger = logging.getLogger(__name__)

#: Título por defecto de la pestaña que gestiona la app.
DEFAULT_MANAGED_TAB = "Seguimiento (app)"
#: Título por defecto de la pestaña de incidencias (subconjunto).
DEFAULT_INCIDENCIAS_TAB = "Incidencias (app)"
#: Clave de `factusol_series_json` donde se configura el título (sin migración:
#: ese blob es el cajón de ajustes del ERP).
MANAGED_TAB_SETTING = "drive_managed_tab"
INCIDENCIAS_TAB_SETTING = "drive_incidencias_tab"

#: Anchos de columna de la pestaña «Pedidos» (píxeles ≈ los del Excel local).
_PEDIDOS_WIDTHS_PX = [96, 116, 82, 210, 76, 240, 88, 130, 102, 88,
                      96, 88, 96, 96, 116, 150, 220]
_INCIDENCIAS_WIDTHS_PX = [116, 210, 170, 280, 130, 82, 92]

#: Gris de la cabecera (el mismo `F2F4F7` del Excel).
_HEADER_BG = (0xF2, 0xF4, 0xF7)
#: Gris del separador entre la zona viva y la estática.
_SEPARATOR_BG = (0xE4, 0xE7, 0xEC)

#: Fila que separa la zona viva de la estática. Se reconoce por el prefijo, no
#: por el texto entero: así se puede retocar la leyenda sin romper las hojas ya
#: escritas (y sin que un «Actualizar» se coma el bloque de abajo).
SEPARATOR_PREFIX = "────"
SEPARATOR_PEDIDOS = f"{SEPARATOR_PREFIX} HISTÓRICO — no se actualiza {SEPARATOR_PREFIX}"
SEPARATOR_INCIDENCIAS = (
    f"{SEPARATOR_PREFIX} PENDIENTES HEREDADOS (hoja vieja) — no se actualiza "
    f"{SEPARATOR_PREFIX}"
)


def is_separator(row: list[Any]) -> bool:
    """¿Esta fila es el separador de zonas?"""
    return str((row or [""])[0] or "").strip().startswith(SEPARATOR_PREFIX)


def static_block(values: list[list[Any]]) -> list[list[Any]]:
    """Lo que hay del separador hacia abajo (separador incluido), o [] si la
    pestaña aún no tiene zona estática.

    Sin separador NO se preserva nada a propósito: una pestaña escrita antes de
    que existieran las zonas es toda zona viva, y adivinar dónde empezaría el
    histórico sería inventarse un bloque."""
    for i, row in enumerate(values):
        if is_separator(list(row)):
            return [list(r) for r in values[i:]]
    return []


def compose(
    header: list[str], live: list[list[Any]], static: list[list[Any]],
) -> list[list[Any]]:
    """Cabecera + zona viva + zona estática (que ya trae su separador)."""
    return [list(header), *live, *static]


def _separator_row(text: str, columns: int) -> list[Any]:
    return [text] + [""] * (columns - 1)


def managed_tab_titles(session: Session) -> tuple[str, str]:
    """(pestaña de pedidos, pestaña de incidencias) configuradas."""
    from app.integrations.factusol.service import series_config  # noqa: PLC0415

    cfg = series_config(session)
    pedidos = str(cfg.get(MANAGED_TAB_SETTING) or "").strip() or DEFAULT_MANAGED_TAB
    incidencias = (
        str(cfg.get(INCIDENCIAS_TAB_SETTING) or "").strip() or DEFAULT_INCIDENCIAS_TAB
    )
    return pedidos, incidencias


def _rgb(hex_color: tuple[int, int, int]) -> dict[str, float]:
    r, g, b = hex_color
    return {"red": r / 255, "green": g / 255, "blue": b / 255}


def _hex_rgb(value: str) -> dict[str, float]:
    return _rgb((int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)))


def _guard_not_historic(sheets: ManagedTabTransport, title: str) -> None:
    """La pestaña gestionada NO puede ser la histórica.

    Es la única protección que separa «reescribir entera» de «destruir el
    archivo de Bart», así que se comprueba en cada escritura y no una sola vez
    al configurar: alguien podría renombrar pestañas en Drive."""
    historic = sheets.first_tab_title()
    if title.strip().casefold() == historic.strip().casefold():
        raise DriveSyncError(
            f"la pestaña gestionada no puede ser «{historic}», que es la "
            "HISTÓRICA: se reescribe entera en cada actualización y se "
            "perdería el archivo. Cambia el título en Configuración ERP."
        )


def live_pedidos_rows(rows: list[dict[str, Any]]) -> list[list[Any]]:
    """Una fila por pedido vivo, ordenadas por Situación. Misma serialización
    que la pantalla y que «Descargar Excel»."""
    return [row_to_pedidos_values(row) for row in sort_by_situacion(rows)]


def live_incidencias_rows(rows: list[dict[str, Any]]) -> list[list[Any]]:
    """El subconjunto EXACTO de Situación=Incidencia (ahora, solo lo reportado
    a mano: ver `workflow.order_alerts`)."""
    return [
        incidencia_values(row) for row in incidencia_rows(sort_by_situacion(rows))
    ]


def build_pedidos_grid(
    rows: list[dict[str, Any]], static: list[list[Any]] | None = None,
) -> list[list[Any]]:
    return compose(SEGUIMIENTO_COLUMNS_V2, live_pedidos_rows(rows), static or [])


def build_incidencias_grid(
    rows: list[dict[str, Any]], static: list[list[Any]] | None = None,
) -> list[list[Any]]:
    return compose(INCIDENCIAS_COLUMNS, live_incidencias_rows(rows), static or [])


def historic_block(
    historic_rows: list[list[Any]], *, columns: int = len(SEGUIMIENTO_COLUMNS_V2),
) -> list[list[Any]]:
    """Separador + filas del histórico, listo para pegarlo bajo la zona viva."""
    if not historic_rows:
        return []
    return [_separator_row(SEPARATOR_PEDIDOS, columns), *historic_rows]


def pendientes_block(
    pendientes: list[list[Any]], *, columns: int = len(INCIDENCIAS_COLUMNS),
) -> list[list[Any]]:
    """Separador + pendientes heredados de la hoja vieja (los de encima del
    marcador «^^^^»), para la pestaña de Incidencias."""
    if not pendientes:
        return []
    return [_separator_row(SEPARATOR_INCIDENCIAS, columns), *pendientes]


def _header_format(columns: int, widths: list[int]) -> list[dict[str, Any]]:
    """Cabecera en negrita con fondo gris, congelada, y anchos de columna."""
    requests: list[dict[str, Any]] = [
        {"updateSheetProperties": {
            "properties": {"sheetId": None,
                           "gridProperties": {"frozenRowCount": 1}},
            "fields": "gridProperties.frozenRowCount",
        }},
        {"repeatCell": {
            "range": {"sheetId": None, "startRowIndex": 0, "endRowIndex": 1,
                      "startColumnIndex": 0, "endColumnIndex": columns},
            "cell": {"userEnteredFormat": {
                "backgroundColor": _rgb(_HEADER_BG),
                "textFormat": {"bold": True},
            }},
            "fields": "userEnteredFormat(backgroundColor,textFormat)",
        }},
    ]
    for i, width in enumerate(widths[:columns]):
        requests.append({"updateDimensionProperties": {
            "range": {"sheetId": None, "dimension": "COLUMNS",
                      "startIndex": i, "endIndex": i + 1},
            "properties": {"pixelSize": width},
            "fields": "pixelSize",
        }})
    return requests


def _situacion_format(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Colorea la celda Situación de cada fila con los tonos del sistema de
    diseño — los MISMOS hex que usa el Excel local (`SITUACION_FILL`), para que
    la hoja de Drive y el Excel no se vean distintos."""
    requests: list[dict[str, Any]] = []
    for i, row in enumerate(rows, start=1):   # fila 0 = cabecera
        situ = row.get("situacion") or "listo"
        fill = SITUACION_FILL.get(situ)
        if fill is None:
            continue
        bg, ink = fill
        requests.append({"repeatCell": {
            "range": {"sheetId": None, "startRowIndex": i, "endRowIndex": i + 1,
                      "startColumnIndex": 0, "endColumnIndex": 1},
            "cell": {"userEnteredFormat": {
                "backgroundColor": _hex_rgb(bg),
                "textFormat": {"bold": True, "foregroundColor": _hex_rgb(ink)},
            }},
            "fields": "userEnteredFormat(backgroundColor,textFormat)",
        }})
    return requests


def pedidos_format(
    rows: list[dict[str, Any]], static: list[list[Any]] | None = None,
) -> list[dict[str, Any]]:
    """Formato completo de la pestaña: cabecera, anchos, Importe en €,
    Situación coloreada (solo en la zona VIVA) y el separador destacado."""
    ordered = sort_by_situacion(rows)
    columns = len(SEGUIMIENTO_COLUMNS_V2)
    total_rows = len(ordered) + 1
    requests = _header_format(columns, _PEDIDOS_WIDTHS_PX)
    if ordered:
        # Importe (columna 7, índice 6) con formato de moneda.
        requests.append({"repeatCell": {
            "range": {"sheetId": None, "startRowIndex": 1,
                      "endRowIndex": total_rows,
                      "startColumnIndex": 6, "endColumnIndex": 7},
            "cell": {"userEnteredFormat": {"numberFormat": {
                "type": "NUMBER", "pattern": "#,##0.00 €",
            }}},
            "fields": "userEnteredFormat.numberFormat",
        }})
        requests.extend(_situacion_format(ordered))
    # El autofiltro cubre SOLO la zona viva: si abarcara el histórico, ordenar
    # por una columna mezclaría los dos bloques.
    requests.append({"setBasicFilter": {"filter": {"range": {
        "sheetId": None, "startRowIndex": 0, "endRowIndex": total_rows,
        "startColumnIndex": 0, "endColumnIndex": columns,
    }}}})
    if static:
        requests.append(_separator_format(total_rows, columns))
    return requests


def _separator_format(row_index: int, columns: int) -> dict[str, Any]:
    """La fila del separador, en gris y negrita, para que se vea de lejos que
    ahí abajo empieza algo que la app no actualiza."""
    return {"repeatCell": {
        "range": {"sheetId": None, "startRowIndex": row_index,
                  "endRowIndex": row_index + 1,
                  "startColumnIndex": 0, "endColumnIndex": columns},
        "cell": {"userEnteredFormat": {
            "backgroundColor": _rgb(_SEPARATOR_BG),
            "textFormat": {"bold": True},
        }},
        "fields": "userEnteredFormat(backgroundColor,textFormat)",
    }}


def incidencias_format(
    rows: list[dict[str, Any]], static: list[list[Any]] | None = None,
) -> list[dict[str, Any]]:
    columns = len(INCIDENCIAS_COLUMNS)
    total_rows = len(incidencia_rows(rows)) + 1
    requests = _header_format(columns, _INCIDENCIAS_WIDTHS_PX)
    requests.append({"setBasicFilter": {"filter": {"range": {
        "sheetId": None, "startRowIndex": 0, "endRowIndex": total_rows,
        "startColumnIndex": 0, "endColumnIndex": columns,
    }}}})
    if static:
        requests.append(_separator_format(total_rows, columns))
    return requests


def push_managed_tabs(
    session: Session,
    sheets: ManagedTabTransport,
    rows: list[dict[str, Any]],
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Vuelca el formato nuevo a la pestaña gestionada (y a la de incidencias).

    `dry_run` calcula el resumen SIN tocar Drive: es la vista previa que Bart
    ve antes de confirmar. Devuelve qué pestañas se escribirían, cuántas filas
    y el desglose por Situación, más la pestaña histórica que NO se toca."""
    pedidos_tab, incidencias_tab = managed_tab_titles(session)
    _guard_not_historic(sheets, pedidos_tab)
    _guard_not_historic(sheets, incidencias_tab)

    ordenadas = sort_by_situacion(rows)
    incidencias = incidencia_rows(ordenadas)
    por_situacion: dict[str, int] = {}
    for row in ordenadas:
        clave = str(row.get("situacion_label") or row.get("situacion") or "—")
        por_situacion[clave] = por_situacion.get(clave, 0) + 1

    resumen: dict[str, Any] = {
        "mode": "managed_tab",
        "tab": pedidos_tab,
        "incidencias_tab": incidencias_tab,
        "historic_tab": sheets.first_tab_title(),
        "rows": len(ordenadas),
        "incidencias": len(incidencias),
        "por_situacion": por_situacion,
        "columns": list(SEGUIMIENTO_COLUMNS_V2),
        "dry_run": dry_run,
        "written": False,
    }
    existing = sheets.tab_titles()
    # Lo que hay que PRESERVAR: del separador hacia abajo. Se lee también en
    # dry-run, para poder decir en la vista previa cuántas filas estáticas
    # sobreviven — que es justo lo que da miedo al pulsar.
    estatico_pedidos = (
        static_block(sheets.tab_values(pedidos_tab)) if pedidos_tab in existing else []
    )
    estatico_incidencias = (
        static_block(sheets.tab_values(incidencias_tab))
        if incidencias_tab in existing else []
    )
    # El separador no cuenta como fila de datos.
    resumen["historico_preservado"] = max(len(estatico_pedidos) - 1, 0)
    resumen["pendientes_preservados"] = max(len(estatico_incidencias) - 1, 0)

    if dry_run:
        return resumen

    resumen["created_tabs"] = [
        t for t in (pedidos_tab, incidencias_tab) if t not in existing
    ]

    sheets.ensure_tab(pedidos_tab)
    sheets.replace_tab(pedidos_tab, build_pedidos_grid(ordenadas, estatico_pedidos))
    sheets.format_tab(pedidos_tab, pedidos_format(ordenadas, estatico_pedidos))

    sheets.ensure_tab(incidencias_tab)
    sheets.replace_tab(
        incidencias_tab, build_incidencias_grid(ordenadas, estatico_incidencias),
    )
    sheets.format_tab(
        incidencias_tab, incidencias_format(ordenadas, estatico_incidencias),
    )

    resumen["written"] = True
    logger.info(
        "drive: volcado del seguimiento a «%s» (%d vivas + %d históricas) y «%s» "
        "(%d vivas + %d heredadas); histórico en bruto «%s» intacto",
        pedidos_tab, len(ordenadas), resumen["historico_preservado"],
        incidencias_tab, len(incidencias), resumen["pendientes_preservados"],
        resumen["historic_tab"],
    )
    return resumen
