"""Importación del histórico de la hoja vieja al formato NUEVO (una vez).

La pestaña histórica de Bart tiene miles de filas en el formato de siempre (17
columnas: Empresa, Fecha entrada albarán, Cliente, …, Orden) y un marcador a
mano —«^^^^ Aquí arriba pedidos que faltan entregar»— que la parte en dos:

- **encima**: su lista de trabajo, pedidos pendientes de entregar. NO son
  histórico, así que se heredan como incidencias abiertas en la zona estática de
  «Incidencias (app)»;
- **debajo**: lo ya entregado → la zona estática de «Seguimiento (app)».

La hoja vieja **no se toca**: sigue siendo el archivo en bruto por si hay que
revisar algo del mapeo.

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

Qué se conserva, y DÓNDE: cada dato va a su columna real (Vendedor → Origen,
Transporte → Envío, Preparado → Preparación, Recogido → Fecha recogido). En
«Nota / Incidencia» solo queda lo que no tiene columna: la columna manual
«Orden» (texto libre, sin su etiqueta) y la «Proforma» (con ella, que si no no
se sabe qué es ese número). Un histórico ya escrito con el formato viejo, que
lo llevaba todo empaquetado ahí (`Vendedor: WEB · Transporte: UPS · …`), se
reparte solo en el siguiente volcado (`seguimiento.redistribute_nota`).

La Situación NO se puede recalcular para el histórico (haría falta el pedido en
BoHub, y estas filas son de años atrás), así que va fija a «Histórico». Así
tampoco compite con los pedidos vivos si alguien mira las dos pestañas.
"""

from __future__ import annotations

import logging
from typing import Any

from app.erp.drive_sheets import DriveSyncError, ManagedTabTransport
from app.erp.seguimiento import (
    INCIDENCIAS_COLUMNS,
    SEG_COLUMNS,
    SEGUIMIENTO_COLUMNS_V2,
    is_structure_row,
    match_header_columns,
    redistribute_nota,
)

logger = logging.getLogger(__name__)

#: Pestaña donde aterrizaba el histórico antes de las zonas. Ya no se escribe:
#: el histórico es la zona estática de «Seguimiento (app)». Se conserva el
#: nombre para poder avisar de que sobra si alguien la tiene de la vez anterior.
LEGACY_HISTORICO_TAB = "Histórico (formato nuevo)"

#: Situación fija del histórico: no se puede recalcular en vivo.
SITUACION_HISTORICO = "Histórico"
#: Tipo con el que se heredan los «faltan entregar» de la hoja vieja.
TIPO_PENDIENTE = "Pendiente entrega (histórico)"
#: Prefijo del marcador que parte la hoja vieja (lo escribe Bart a mano).
MARKER_PREFIX = "^^^^"

#: Mínimo de columnas reconocidas para fiarse de la cabecera encontrada.
_MIN_HEADER_MATCHES = 5

_IDX = {c.header: i for i, c in enumerate(SEG_COLUMNS)}
#: Posición de «Nota / Incidencia» en el formato nuevo (la última).
NOTA_INDEX = SEGUIMIENTO_COLUMNS_V2.index("Nota / Incidencia")


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
    """Lo que NO tiene columna propia en el formato nuevo (las notas a mano y
    la proforma), junto y etiquetado, para no perder nada del histórico. Lo
    que sí la tiene —vendedor, transporte, preparado, recogido— va a su
    columna (`map_row`), no aquí."""
    piezas: list[str] = []
    for etiqueta, header in (
        ("Orden", "Orden"),                 # la columna de notas a mano
        ("Proforma", "Proforma"),
    ):
        valor = _cell(row, col_map, header)
        if valor:
            piezas.append(f"{etiqueta}: {valor}")
    return " · ".join(piezas)[:500]


def _origen(row: list[Any], col_map: dict[int, int]) -> str:
    """Origen del histórico: el vendedor (WEB / nombre) y el canal
    (OFI-TER-SAT), juntos si hay los dos y son distintos («WEB · SAT»)."""
    partes = [_cell(row, col_map, "Vendedor"), _cell(row, col_map, "OFI-TER-SAT")]
    return " · ".join(dict.fromkeys(p for p in partes if p))


def map_row(row: list[Any], col_map: dict[int, int]) -> list[Any]:
    """Una fila de la hoja vieja → los 18 valores del formato nuevo.

    Cada dato de la hoja vieja va a su columna real: Vendedor (+ canal) →
    Origen, Transporte → Envío, Preparado → Preparación, Recogido → Fecha
    recogido. Solo lo que no tiene columna (Orden, Proforma) va a «Nota /
    Incidencia». Lo que no existe en el histórico (Importe, Cobro, Fecha
    factura) se deja vacío: inventarlo sería peor que no tenerlo. Las fechas
    se dejan tal cual vienen (texto): el volcado las pasa a valor de fecha si
    se pueden leer, y una rota se queda como texto."""
    num_serie = _cell(row, col_map, "Nº de Serie")
    whiterip = _cell(row, col_map, "WhiteRIP")
    serie_whiterip = " · ".join(p for p in (num_serie, whiterip) if p)
    fila = [
        SITUACION_HISTORICO,                                  # Situación
        _cell(row, col_map, "Albarán / Nº Pedido Web"),       # Nº pedido
        _cell(row, col_map, "Fecha entrada albarán"),         # Fecha
        _cell(row, col_map, "Cliente"),                       # Cliente
        _origen(row, col_map),                                # Origen
        _cell(row, col_map, "Productos")[:300],               # Productos
        "",                                                   # Importe
        _cell(row, col_map, "Empresa"),                       # Empresa (serie)
        _cell(row, col_map, "Nº de Factura"),                 # Factura
        "",                                                   # Fecha factura
        _cell(row, col_map, "F Envío Factura"),               # Factura enviada
        "",                                                   # Cobro
        _cell(row, col_map, "Preparado"),                     # Preparación
        _cell(row, col_map, "Transport"),                     # Envío
        _cell(row, col_map, "Recogido"),                      # Fecha recogido
        _cell(row, col_map, "Tracking"),                      # Tracking
        serie_whiterip,                                       # Nº serie · WhiteRIP
        _nota(row, col_map),                                  # Nota / Incidencia
    ]
    # Y se reparte lo que la nota traiga empaquetado (`Orden: …`, `Proforma: …`,
    # y los `Vendedor/Transporte/Preparado/Recogido` de un histórico importado
    # con el formato viejo): cada token a su columna, sin pisar lo que ya hay.
    return redistribute_nota(fila)


def map_pendiente(row: list[Any], col_map: dict[int, int]) -> list[Any]:
    """Una fila de ENCIMA del marcador «^^^^» → los 7 valores de Incidencias.

    Ese bloque es la lista de trabajo de Bart: «pedidos que faltan entregar».
    No son histórico —están pendientes— así que se heredan como incidencias
    abiertas en vez de enterrarse en el archivo."""
    productos = _cell(row, col_map, "Productos")
    nota = _nota(row, col_map)
    motivo = " · ".join(p for p in (productos, nota) if p)[:500]
    return [
        _cell(row, col_map, "Albarán / Nº Pedido Web"),   # Nº pedido
        _cell(row, col_map, "Cliente"),                   # Cliente
        TIPO_PENDIENTE,                                   # Tipo
        motivo,                                           # Motivo
        _cell(row, col_map, "Vendedor"),                  # Asignado a
        _cell(row, col_map, "Fecha entrada albarán"),     # Fecha
        "Abierta",                                        # Estado
    ]


def _marker_index(values: list[list[Any]], start: int) -> int | None:
    """Fila del marcador «^^^^ Aquí arriba pedidos que faltan entregar», que
    parte la hoja vieja en dos mundos. `None` si la hoja no lo tiene."""
    for i in range(start, len(values)):
        first = str((list(values[i]) or [""])[0] or "").strip()
        if first.startswith(MARKER_PREFIX):
            return i
    return None


def plan_import(values: list[list[Any]]) -> dict[str, Any]:
    """Lee la hoja vieja y planifica la conversión SIN escribir nada.

    La hoja tiene DOS mundos separados por el marcador «^^^^ Aquí arriba
    pedidos que faltan entregar»:

    - ENCIMA: la lista de trabajo de Bart, pedidos pendientes de entregar. No
      son histórico: se heredan como incidencias abiertas.
    - DEBAJO: lo ya entregado, que es el archivo → histórico.

    La fila del propio marcador se descarta, como el resto de estructura
    (cabeceras repetidas, separadores, vacías).

    Devuelve el recuento honesto que pide el dry-run: cuántas van a pendientes,
    cuántas a histórico, cuántas se descartan y cuántas quedan DUDOSAS — filas
    que no son estructura pero tampoco traen nº de pedido. Las dudosas se
    importan igualmente (con su nota) para no perder información, pero se
    listan para que alguien las mire."""
    header_row, col_map = locate_header(values)
    reconocidas = [SEG_COLUMNS[c].header for c in sorted(col_map)]
    ausentes = [c.header for i, c in enumerate(SEG_COLUMNS) if i not in col_map]
    marker = _marker_index(values, header_row + 1)

    mapeadas: list[list[Any]] = []
    pendientes: list[list[Any]] = []
    dudosas: list[dict[str, Any]] = []
    descartadas = 0
    for i, raw in enumerate(values[header_row + 1:], start=header_row + 1):
        row = list(raw)
        if is_structure_row(row):
            descartadas += 1                 # incluye la fila del propio «^^^^»
            continue
        # Encima del marcador: pendientes de entregar. Sin marcador, todo es
        # histórico (que es como se comportaba antes de este split).
        if marker is not None and i < marker:
            pendientes.append(map_pendiente(row, col_map))
            continue
        mapped = map_row(row, col_map)
        if not mapped[1]:                    # sin nº de pedido
            dudosas.append({"fila": i + 1, "cliente": mapped[3],
                            "nota": mapped[NOTA_INDEX][:120]})
        mapeadas.append(mapped)

    return {
        "marcador_fila": (marker + 1) if marker is not None else None,
        "pendientes": len(pendientes),
        "pendientes_rows": pendientes,
        "pendientes_muestra": pendientes[:5],
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
    pedidos_tab: str,
    incidencias_tab: str,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Convierte la hoja vieja y la reparte en las DOS zonas estáticas:

    - el histórico (debajo del «^^^^») → zona estática de «Seguimiento (app)»,
    - los pendientes de entregar (encima) → zona estática de «Incidencias (app)».

    `dry_run` (por defecto) solo informa.

    La hoja vieja NO se escribe: se lee y ya. Y las zonas VIVAS de las dos
    pestañas se conservan: se leen antes y se vuelven a poner encima del
    separador, igual que el volcado periódico conserva las estáticas. Reejecutar
    reemplaza los bloques estáticos enteros, así que es idempotente y no duplica
    separadores."""
    from app.erp.drive_managed import (  # noqa: PLC0415
        cabecera_de,
        completados_static_block,
        compose,
        dates_to_serial,
        historic_block,
        incidencias_format,
        is_separator,
        live_zone,
        pedidos_format,
        pendientes_block,
        realinear_fila,
    )
    from app.erp.seguimiento import (  # noqa: PLC0415
        HISTORICO_DATE_COLUMNS,
        INCIDENCIAS_DATE_COLUMNS,
        PEDIDOS_DATE_COLUMNS,
    )

    historic = sheets.first_tab_title()
    for destino in (pedidos_tab, incidencias_tab):
        if destino.strip().casefold() == historic.strip().casefold():
            raise DriveSyncError(
                f"«{destino}» es la pestaña histórica en bruto: lo convertido va "
                "a las pestañas de la app, para no tocar el original"
            )

    plan = plan_import(sheets.tab_values(historic))
    resumen = {k: v for k, v in plan.items()
               if k not in ("rows", "pendientes_rows")}
    resumen.update({
        "tab": pedidos_tab, "incidencias_tab": incidencias_tab,
        "origen": historic, "dry_run": dry_run, "written": False,
    })
    if dry_run:
        return resumen

    def _live(title: str) -> list[list[Any]]:
        """La zona VIVA que ya hay en la pestaña (sin cabecera): todo lo que
        está por encima del separador."""
        if title not in sheets.tab_titles():
            return []
        values = [list(r) for r in sheets.tab_values(title, raw=True)]
        cuerpo = values[1:] if values else []
        for i, row in enumerate(cuerpo):
            if is_separator(row):
                return cuerpo[:i]
        return cuerpo

    # Las fechas del histórico, como valor de fecha donde se puedan leer (una
    # rota se queda como texto): así el bloque también ordena por fecha.
    historico = historic_block(dates_to_serial(plan["rows"], HISTORICO_DATE_COLUMNS))
    # La zona VIVA que ya hay —las filas de BoHub y las tecleadas a mano
    # (Origen = MANUAL)— se conserva ENTERA y en su orden: el import solo
    # reemplaza el histórico, nunca absorbe una fila manual. Se realinea a las
    # 18 columnas si la pestaña era de 17, y sus fechas van como fecha; en la
    # zona viva «Preparación» es un estado, así que no se toca.
    # Se lee y se escribe EN BRUTO: lo tecleado a mano vuelve tal cual.
    valores = (
        sheets.tab_values(pedidos_tab, raw=True) if pedidos_tab in sheets.tab_titles() else []
    )
    cabecera = cabecera_de(valores)
    vivas_pedidos = dates_to_serial(
        [realinear_fila(r, cabecera) for r in live_zone(valores)], PEDIDOS_DATE_COLUMNS,
    )
    # El bloque de COMPLETADOS de BoHub (histórico automático) que ya hubiera se
    # CONSERVA entre la zona viva y el histórico manual: el import solo
    # reemplaza el histórico manual (el que sale de la hoja vieja). Si no lo
    # preservara, la siguiente actualización lo regeneraría igualmente desde la
    # BD, pero así no hay un estado intermedio raro.
    completados_prev = dates_to_serial(
        completados_static_block(valores), PEDIDOS_DATE_COLUMNS,
    )
    estatico = [*completados_prev, *historico]
    sheets.ensure_tab(pedidos_tab)
    sheets.replace_tab(
        pedidos_tab, compose(SEGUIMIENTO_COLUMNS_V2, vivas_pedidos, estatico), raw=True,
    )
    # El mismo formato que el volcado periódico (cabecera congelada, anchos,
    # fechas, Situación coloreada según su etiqueta), por posición: la zona
    # viva se ha conservado tal cual.
    sheets.format_tab(pedidos_tab, pedidos_format([], estatico, vivas_pedidos))

    if plan["pendientes_rows"]:
        pendientes = pendientes_block(
            dates_to_serial(plan["pendientes_rows"], INCIDENCIAS_DATE_COLUMNS),
        )
        vivas_inc = dates_to_serial(_live(incidencias_tab), INCIDENCIAS_DATE_COLUMNS)
        sheets.ensure_tab(incidencias_tab)
        sheets.replace_tab(incidencias_tab, compose(
            INCIDENCIAS_COLUMNS, vivas_inc, pendientes,
        ), raw=True)
        sheets.format_tab(incidencias_tab, incidencias_format(
            [{"situacion": "incidencias", "fecha": None} for _ in vivas_inc], pendientes,
        ))

    resumen["written"] = True
    logger.info(
        "drive: hoja vieja repartida — %d al histórico de «%s», %d a pendientes "
        "de «%s» (%d descartadas, %d dudosas); «%s» sin tocar",
        plan["mapeadas"], pedidos_tab, plan["pendientes"], incidencias_tab,
        plan["descartadas"], plan["dudosas"], historic,
    )
    return resumen
