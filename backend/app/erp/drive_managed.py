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

El histórico convertido al formato nuevo vive en su propia pestaña
(«Histórico (formato nuevo)», ver `drive_historico`), que el volcado periódico
NO reescribe: así reordenar por Situación no se lo lleva por delante.
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


def build_pedidos_grid(rows: list[dict[str, Any]]) -> list[list[Any]]:
    """Cabecera + una fila por pedido, ordenadas por Situación. Misma
    serialización que la pantalla y que «Descargar Excel»."""
    return [list(SEGUIMIENTO_COLUMNS_V2)] + [
        row_to_pedidos_values(row) for row in sort_by_situacion(rows)
    ]


def build_incidencias_grid(rows: list[dict[str, Any]]) -> list[list[Any]]:
    """Cabecera + el subconjunto EXACTO de Situación=Incidencia."""
    return [list(INCIDENCIAS_COLUMNS)] + [
        incidencia_values(row) for row in incidencia_rows(sort_by_situacion(rows))
    ]


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


def pedidos_format(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Formato completo de la pestaña «Pedidos»: cabecera, anchos, Importe en
    €, Situación coloreada y autofiltro sobre todo el rango."""
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
    requests.append({"setBasicFilter": {"filter": {"range": {
        "sheetId": None, "startRowIndex": 0, "endRowIndex": total_rows,
        "startColumnIndex": 0, "endColumnIndex": columns,
    }}}})
    return requests


def incidencias_format(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    columns = len(INCIDENCIAS_COLUMNS)
    total_rows = len(incidencia_rows(rows)) + 1
    requests = _header_format(columns, _INCIDENCIAS_WIDTHS_PX)
    requests.append({"setBasicFilter": {"filter": {"range": {
        "sheetId": None, "startRowIndex": 0, "endRowIndex": total_rows,
        "startColumnIndex": 0, "endColumnIndex": columns,
    }}}})
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
    if dry_run:
        return resumen

    existing = sheets.tab_titles()
    resumen["created_tabs"] = [
        t for t in (pedidos_tab, incidencias_tab) if t not in existing
    ]

    sheets.ensure_tab(pedidos_tab)
    sheets.replace_tab(pedidos_tab, build_pedidos_grid(ordenadas))
    sheets.format_tab(pedidos_tab, pedidos_format(ordenadas))

    sheets.ensure_tab(incidencias_tab)
    sheets.replace_tab(incidencias_tab, build_incidencias_grid(ordenadas))
    sheets.format_tab(incidencias_tab, incidencias_format(ordenadas))

    resumen["written"] = True
    logger.info(
        "drive: volcado del seguimiento a «%s» (%d filas) y «%s» (%d); "
        "histórico «%s» intacto",
        pedidos_tab, len(ordenadas), incidencias_tab, len(incidencias),
        resumen["historic_tab"],
    )
    return resumen
