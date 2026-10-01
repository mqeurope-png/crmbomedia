"""Volcado del seguimiento (formato NUEVO) a su pestaña gestionada de Drive.

La hoja de Drive de Bart tiene dos mundos y no se mezclan:

- La pestaña **histórica** en bruto (la hoja vieja) es suya: miles de filas
  con anotaciones a mano. La sincronización de siempre (`drive_sheets`) solo
  INSERTA ahí y jamás reescribe ni formatea. Este módulo **no la toca**, y lo
  comprueba antes de escribir por su CONTENIDO, esté en la posición que esté
  (`_guard_pestana_de_la_app`). Su histórico ya vive, importado, bajo el
  separador de la pestaña gestionada, que lo preserva en cada pasada.
- La pestaña **gestionada** («Seguimiento (app)», configurable) es de la app:
  se reescribe entera en cada actualización con las 20 columnas del rediseño
  2026 (la última, la «id» técnica oculta), ordenada por fecha del pedido (más
  reciente primero) y con la celda
  Situación coloreada — la misma
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
import math
import re
import zlib
from collections import Counter
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any, NoReturn

from sqlalchemy.orm import Session

from app.erp.drive_historico import SITUACION_HISTORICO
from app.erp.drive_sheets import DriveSyncError, ManagedTabTransport
from app.erp.seguimiento import (
    COBRO_LABELS,
    DATE_PATTERN,
    HISTORICO_DATE_COLUMNS,
    INCIDENCIAS_COLUMNS,
    INCIDENCIAS_DATE_COLUMNS,
    NO_APLICA,
    PEDIDOS_DATE_COLUMNS,
    PREPARACION_INDEX,
    PREPARACION_LABELS,
    SEGUIMIENTO_COLUMNS_V2,
    SITUACION_FILL,
    SITUACION_LABELS,
    envio_vocabulary,
    envio_vocabulary_antiguo,
    incidencia_rows,
    incidencia_values,
    match_number,
    parse_sheet_date,
    redistribute_nota,
    row_to_pedidos_values,
    sort_by_fecha_desc,
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

#: Anchos de columna de la pestaña «Pedidos» (píxeles ≈ los del Excel local). La
#: última («id») es técnica y va OCULTA; el ancho es indiferente.
_PEDIDOS_WIDTHS_PX = [96, 116, 82, 210, 76, 240, 88, 130, 102, 88,
                      96, 88, 96, 96, 110, 92, 116, 150, 220, 300]
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
    """¿Esta fila es un separador de zonas?"""
    return str((row or [""])[0] or "").strip().startswith(SEPARATOR_PREFIX)


def static_block(values: list[list[Any]]) -> list[list[Any]]:
    """Lo que hay del PRIMER separador hacia abajo (separador incluido), o [] si
    la pestaña aún no tiene zona estática.

    Sin separador NO se preserva nada a propósito: una pestaña escrita antes de
    que existieran las zonas es toda zona viva, y adivinar dónde empezaría el
    histórico sería inventarse un bloque."""
    for i, row in enumerate(values):
        if is_separator(list(row)):
            return [list(r) for r in values[i:]]
    return []


def historico_manual_rows(values: list[list[Any]]) -> list[list[Any]]:
    """El histórico MANUAL —lo irrepetible— como filas de DATOS (sin separador),
    del PRIMER separador hacia abajo.

    En la zona estática conviven, bajo el único separador «HISTÓRICO», los
    COMPLETADOS de BoHub (que la app REGENERA en cada pasada, reconocibles por
    `Situación = "Completado"`) y el histórico MANUAL —irrepetible— que se
    preserva byte a byte. Aquí se devuelve SOLO el manual: se saltan los
    separadores (incluido el viejo «COMPLETADOS» de hojas anteriores) y las
    filas de completados de BoHub. Sin separador (pestaña previa a las zonas),
    [] — no se inventa un histórico."""
    return [
        list(r) for r in static_block(values)
        if not is_separator(list(r)) and not es_fila_completado(list(r))
    ]


def historico_separator_row(
    values: list[list[Any]], *, columns: int = len(SEGUIMIENTO_COLUMNS_V2),
) -> list[Any]:
    """La fila del separador «HISTÓRICO», reutilizando la que ya hubiera en la
    pestaña (byte a byte, como el resto de la zona estática) o una nueva si no la
    hay. Se salta un separador «COMPLETADOS» de hojas viejas (#478): su leyenda no
    debe quedarse como separador del histórico."""
    for row in static_block(values):
        r = list(row)
        if is_separator(r) and "completados" not in _texto(r[0]).casefold():
            return r
    return _separator_row(SEPARATOR_PEDIDOS, columns)


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


def _cabecera_de_la_app(row: list[Any], cabecera: list[str]) -> bool:
    """¿Es esta fila la cabecera que escribe la app en esa pestaña? Basta con el
    principio: las versiones anteriores del formato (17/18 columnas) empiezan
    igual y solo se diferencian al final."""
    return [_texto(c).casefold() for c in row[:3]] == [c.casefold() for c in cabecera[:3]]


def fila_de_seguimiento_app(fila: list[Any]) -> bool:
    """¿Fila con la forma de «Seguimiento (app)»? Tecleada a mano (Origen =
    MANUAL) o con una Situación de las que escribe la app (incluidas «Completado»
    y «Histórico»). En la hoja vieja esas columnas son Empresa y OFI-TER-SAT, que
    nunca llevan esos valores."""
    situacion = _texto(fila[0]).casefold() if fila else ""
    return (
        is_manual_row(fila)
        or situacion in _SITUACION_POR_ETIQUETA
        or situacion in {SITUACION_COMPLETADO_LABEL.casefold(), SITUACION_HISTORICO.casefold()}
    )


def _guard_pestana_de_la_app(
    title: str, valores: list[list[Any]], cabecera: list[str],
    fila_propia: Callable[[list[Any]], bool] | None = None,
) -> None:
    """La pestaña que se va a REESCRIBIR entera tiene que ser de la app.

    Es la única protección que separa «reescribir entera» de «destruir el
    archivo de Bart», así que se comprueba en cada escritura (y en la vista
    previa), no una sola vez al configurar: en Drive se renombran y se mueven
    pestañas.

    Se decide por el CONTENIDO, no por la posición. Antes se comparaba con la
    primera pestaña del documento, y eso fallaba en los dos sentidos: bloqueaba
    «Seguimiento (app)» en cuanto alguien la ponía la primera, aunque el
    histórico viva DENTRO de ella (bajo el separador, preservado en cada
    pasada), y no protegía la hoja vieja si se movía a otro sitio. Es de la app
    si está vacía (no hay nada que perder), si tiene la cabecera que escribe la
    app, su separador de zonas o filas con su forma (`fila_propia`: así se
    reconoce aunque alguien borre la cabecera). La hoja vieja en bruto no tiene
    nada de eso (su cabecera es «Empresa, Fecha entrada albarán…» y su
    marcador, «^^^^»), así que se rechaza esté donde esté."""
    if not any(_texto(c) for fila in valores for c in fila):
        return
    if any(_cabecera_de_la_app(list(f), cabecera) for f in valores[:5]):
        return
    if any(is_separator(list(f)) for f in valores):
        return
    if fila_propia is not None and any(fila_propia(list(f)) for f in valores):
        return
    raise DriveSyncError(
        f"la pestaña «{title}» no tiene el formato de la app (ni su cabecera ni "
        "el separador del histórico): parece la hoja vieja en bruto u otra "
        "pestaña hecha a mano, y el volcado la reescribe entera, así que se "
        "perdería. No se ha escrito nada. Pon otro título en Configuración ERP "
        "(si la pestaña no existe, la app la crea)."
    )


def pestana_archivo(titles: list[str], gestionadas: tuple[str, ...]) -> str:
    """La pestaña que la vista previa nombra como «no se toca»: la primera que
    NO es de la app (normalmente la hoja vieja en bruto), o "" si no hay
    ninguna. Solo informa; la protección es `_guard_pestana_de_la_app`."""
    propias = {t.strip().casefold() for t in gestionadas}
    return next((t for t in titles if t.strip().casefold() not in propias), "")


#: Día 0 de Google Sheets (y de Excel): las fechas son «días desde aquí».
_SHEETS_EPOCH = date(1899, 12, 30)


def sheet_serial(value: date) -> int:
    """Serial de Sheets de una fecha: con `numberFormat` DATE se ve como fecha
    y, sobre todo, ORDENA como fecha."""
    return (value - _SHEETS_EPOCH).days


def _date_cell(value: Any) -> Any:
    """Celda de una columna de fecha, lista para `USER_ENTERED`:
      - `date` (formato nuevo) → serial;
      - texto con fecha reconocible (histórico: «3/2/2026», «2026-02-03») →
        serial, para que el bloque estático también ordene;
      - un serial que ya viene como número (o como texto de dígitos, que es
        como devuelve la API una celda de fecha sin formato) → tal cual;
      - una fecha ROTA («27/07/202») o cualquier otro texto → se queda como
        texto, sin inventar nada."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    parsed = parse_sheet_date(value)
    if parsed is not None:
        return sheet_serial(parsed)
    text = str(value or "").strip()
    if text.isdigit() and 4 <= len(text) <= 6:
        return int(text)
    return value if value is not None else ""


def dates_to_serial(rows: list[list[Any]], columns: tuple[int, ...]) -> list[list[Any]]:
    """Las columnas de fecha de `rows`, como valor de fecha (serial)."""
    out: list[list[Any]] = []
    for row in rows:
        r = list(row)
        for c in columns:
            if c < len(r):
                r[c] = _date_cell(r[c])
        out.append(r)
    return out


def date_format_requests(
    columns: tuple[int, ...], *, first_row: int, last_row: int,
) -> list[dict[str, Any]]:
    """`numberFormat` de fecha (DD/MM/AAAA) sobre las columnas de fecha, filas
    `first_row`..`last_row` (0-based, `last_row` excluido). Cubre también el
    bloque estático: una celda de texto no se ve afectada."""
    if last_row <= first_row:
        return []
    return [{"repeatCell": {
        "range": {"sheetId": None, "startRowIndex": first_row, "endRowIndex": last_row,
                  "startColumnIndex": c, "endColumnIndex": c + 1},
        "cell": {"userEnteredFormat": {"numberFormat": {
            "type": "DATE", "pattern": DATE_PATTERN.lower(),
        }}},
        "fields": "userEnteredFormat.numberFormat",
    }} for c in columns]


def live_pedidos_rows(rows: list[dict[str, Any]]) -> list[list[Any]]:
    """Una fila por pedido vivo, por fecha del pedido (más reciente primero).
    Misma serialización que la pantalla y que «Descargar Excel», con las
    fechas como serial de Sheets (valor de fecha real)."""
    return dates_to_serial(
        [row_to_pedidos_values(row) for row in sort_by_fecha_desc(rows)],
        PEDIDOS_DATE_COLUMNS,
    )


def live_incidencias_rows(rows: list[dict[str, Any]]) -> list[list[Any]]:
    """El subconjunto EXACTO de Situación=Incidencia (ahora, solo lo reportado
    a mano: ver `workflow.order_alerts`), en el mismo orden que la hoja."""
    return dates_to_serial(
        [incidencia_values(row) for row in incidencia_rows(sort_by_fecha_desc(rows))],
        INCIDENCIAS_DATE_COLUMNS,
    )


#: Cabeceras de formatos ANTERIORES de la pestaña (FOTOS fijas, nunca derivadas
#: de la actual: así siguen reconociendo una pestaña vieja aunque se añadan
#: columnas). Las dos columnas que han ido entrando —«Fecha recogido» y
#: «Courier»— lo hicieron justo detrás de «Envío», así que una fila vieja se
#: pone al día abriendo huecos en ese punto (`_HUECO`), sin mover nada más.
#:
#: 17 columnas (#457): sin «Fecha recogido» ni la «id» técnica.
_HEADER_V2_SIN_RECOGIDO: list[str] = [
    "Situación", "Nº pedido", "Fecha", "Cliente", "Origen", "Productos", "Importe",
    "Empresa (serie)", "Factura", "Fecha factura", "Factura enviada", "Cobro",
    "Preparación", "Envío", "Tracking", "Nº serie · WhiteRIP", "Nota / Incidencia",
]
#: 18/19 columnas: con «Fecha recogido» (y la «id» al final), sin «Courier».
_HEADER_V2_SIN_COURIER: list[str] = [
    *_HEADER_V2_SIN_RECOGIDO[:14], "Fecha recogido", *_HEADER_V2_SIN_RECOGIDO[14:],
]
#: Índice por el que entran los huecos (detrás de «Envío»).
_HUECO = SEGUIMIENTO_COLUMNS_V2.index("Envío") + 1
_COURIER_INDEX = SEGUIMIENTO_COLUMNS_V2.index("Courier")
_RECOGIDO_INDEX = SEGUIMIENTO_COLUMNS_V2.index("Fecha recogido")
#: Índice de la columna técnica «id» (la última). Oculta en la hoja.
_ID_INDEX = SEGUIMIENTO_COLUMNS_V2.index("id")

#: Formatos de la pestaña: el actual y los dos anteriores (y «no se sabe por
#: la cabecera»: no la hay, o está retocada).
FORMATO_ACTUAL = "actual"
FORMATO_SIN_COURIER = "sin_courier"      # 18/19 columnas
FORMATO_SIN_RECOGIDO = "sin_recogido"    # 17 columnas
FORMATO_DESCONOCIDO = "desconocido"
#: Forma de las «id» que pone BoHub (uuid4): pedido, histórico y filas a mano.
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
#: Huecos que hay que abrir detrás de «Envío» en cada formato viejo (y sus
#: nombres, por si es la cabecera).
_HUECOS: dict[str, list[str]] = {
    FORMATO_SIN_COURIER: ["Courier"],
    FORMATO_SIN_RECOGIDO: ["Courier", "Fecha recogido"],
}
#: Columna de la «id» en cada formato (el de 17 columnas no la tenía).
_COLUMNA_ID: dict[str, int | None] = {
    FORMATO_ACTUAL: _ID_INDEX,
    FORMATO_SIN_COURIER: _ID_INDEX - 1,
    FORMATO_SIN_RECOGIDO: None,
}
#: La cabecera ENTERA de cada formato, columna a columna (la de 18/19 columnas,
#: con su «id» al final: la de 18, sin ella, solo difiere en esa celda vacía).
_CABECERAS: dict[str, list[str]] = {
    FORMATO_ACTUAL: list(SEGUIMIENTO_COLUMNS_V2),
    FORMATO_SIN_COURIER: [*_HEADER_V2_SIN_COURIER, "id"],
    FORMATO_SIN_RECOGIDO: list(_HEADER_V2_SIN_RECOGIDO),
}
#: Todos los nombres de columna que ha escrito la app, sin mayúsculas (los de
#: los formatos anteriores están todos en el actual).
_NOMBRES_CONOCIDOS = frozenset(c.casefold() for c in SEGUIMIENTO_COLUMNS_V2)


def _letra(idx: int) -> str:
    """Letra de columna de la hoja (0 → A, 25 → Z, 26 → AA)."""
    letras = ""
    idx += 1
    while idx:
        idx, resto = divmod(idx - 1, 26)
        letras = chr(ord("A") + resto) + letras
    return letras


def _mal_puestas(header: list[Any], esperada: list[str]) -> list[int]:
    """Columnas de la cabecera `esperada` en las que la leída lleva el nombre de
    OTRA columna: un nombre conocido fuera de su sitio delata una columna
    insertada, borrada o movida a mano (todo lo de detrás está corrido). Una
    celda con otro texto, o vacía, es solo un nombre retocado: no descoloca nada.
    Lo que haya a la derecha de la última columna del formato no cuenta."""
    textos = [_texto(h).casefold() for h in header]
    return [
        i for i, nombre in enumerate(esperada)
        if (leido := textos[i] if i < len(textos) else "") != nombre.casefold()
        and leido in _NOMBRES_CONOCIDOS
    ]


def _formatos_posibles(header: list[Any]) -> list[str]:
    """Los formatos con los que encaja la cabecera (salvo nombres retocados)."""
    return [f for f, esperada in _CABECERAS.items() if not _mal_puestas(header, esperada)]


def formato_de_cabecera(header: list[Any]) -> str:
    """¿Con qué formato se escribió la pestaña, según su cabecera? Se compara
    ENTERA, columna a columna, con la de cada formato (`_mal_puestas`): insertar,
    borrar o mover una columna a mano deja nombres conocidos fuera de su sitio, y
    eso nunca se acepta; renombrar o vaciar celdas no descoloca nada (la pasada
    vuelve a escribir los nombres buenos). Lo que haya a la derecha de la última
    columna del formato —p. ej. «Courier» tecleado en T1 de una pestaña vieja—
    no cuenta (ver `comprobar_columnas`). Si no encaja con ninguno, o con varios
    (cabecera casi toda vaciada), «desconocido»."""
    posibles = _formatos_posibles(header)
    return posibles[0] if len(posibles) == 1 else FORMATO_DESCONOCIDO


def _ids_por_columna(values: list[list[Any]]) -> Counter[int]:
    """En qué columna lleva cada fila su «id» (un uuid, como los que pone
    BoHub): columna → nº de filas. Se miran TODAS las columnas: una «id» que
    ha ido a parar a la izquierda (columnas borradas) cuenta como fuera de
    sitio, no como que no hay «id»."""
    cuenta: Counter[int] = Counter()
    for fila in values:
        if not fila or is_separator(list(fila)):
            continue
        for i, celda in enumerate(fila):
            if _UUID_RE.match(_texto(celda)):
                cuenta[i] += 1
    return cuenta


def _columna_de_las_ids(values: list[list[Any]]) -> int | None:
    """La columna en la que está la mayoría de las «id» (None si no hay)."""
    ids = _ids_por_columna(values)
    return ids.most_common(1)[0][0] if ids else None


def formato_de_pestana(values: list[list[Any]]) -> str:
    """Formato de la pestaña, por su cabecera (`formato_de_cabecera`). Si encaja
    con varios (cabecera casi toda vaciada o renombrada), decide la columna en la
    que están las «id». SIN fila de cabecera no hay forma de ver si una columna se
    ha movido: solo se acepta una pestaña sin ninguna «id» (nueva, o solo filas
    tecleadas), que se lee con el formato actual; con «id», «desconocido» (ver
    `comprobar_columnas`: hay que volver a poner la cabecera)."""
    cabecera = cabecera_de(values)
    columna = _columna_de_las_ids(values)
    if not cabecera:
        return FORMATO_ACTUAL if columna is None else FORMATO_DESCONOCIDO
    posibles = _formatos_posibles(cabecera)
    if len(posibles) > 1:
        return next((f for f in posibles if columna is not None and _COLUMNA_ID[f] == columna),
                    FORMATO_DESCONOCIDO)
    return posibles[0] if posibles else FORMATO_DESCONOCIDO


def _fila_de_cabecera(values: list[list[Any]]) -> int | None:
    return next((i for i, r in enumerate(values[:5]) if es_cabecera(r)), None)


def _columna_movida(values: list[list[Any]], cabecera: list[Any], formato: str) -> int | None:
    """Una columna con el nombre de una de la app, a la derecha de las columnas
    del formato y CON datos debajo —p. ej. «Courier» cortada y pegada detrás de
    la «id» de una pestaña vieja, o «Nota / Incidencia» llevada más allá de la
    «id»—: es una columna movida (sus datos no se leerían), no un rótulo suelto
    (ese, sin nada debajo, no estorba)."""
    fila_cab = _fila_de_cabecera(values)
    for i in range(len(_CABECERAS[formato]), len(cabecera)):
        if _texto(cabecera[i]).casefold() in _NOMBRES_CONOCIDOS and any(
            len(f) > i and _texto(f[i])
            for k, f in enumerate(values) if k != fila_cab and not is_separator(list(f))
        ):
            return i
    return None


def _hay_filas(values: list[list[Any]]) -> bool:
    """¿Hay alguna fila con dato, aparte de la cabecera y los separadores?"""
    fila_cab = _fila_de_cabecera(values)
    return any(
        any(_texto(c) for c in f)
        for k, f in enumerate(values) if k != fila_cab and f and not is_separator(list(f))
    )


#: Qué hacer cuando las columnas no están donde tocan: deshacer el cambio, no
#: retocar la cabecera (eso no devuelve los datos a su columna).
_REMEDIO_DESHACER = (
    "deshaz ese cambio de columnas (Ctrl+Z, o «Historial de versiones» de la hoja; "
    "cambiar el nombre de la cabecera NO lo arregla)"
)


def comprobar_columnas(values: list[list[Any]], formato: str, title: str) -> None:
    """ANTES de tocar nada, que la pestaña se sepa leer, columna a columna:

    - la cabecera, la de un formato conocido, sin nombres de otras columnas fuera
      de su sitio (`formato_de_cabecera`); sin fila de cabecera, solo si no hay
      ninguna «id» (pestaña nueva o solo filas tecleadas);
    - ninguna columna con nombre de la app a la derecha de las del formato, con
      datos (`_columna_movida`);
    - la «id» (oculta, la última) en su sitio: que esté la columna, y que TODAS
      las «id» de las filas estén en ella (una fila con su «id» en otra columna
      tiene celdas corridas: se leería mal y su nota se pisaría).

    Si algo no cuadra —una columna insertada, borrada o movida a mano, celdas
    insertadas en una fila, «Courier» insertada dos veces, la «id» borrada, la
    fila de cabecera borrada…—, leerla descolocaría lo que va detrás (y la «id»
    de cada fila): no se escribe nada y se dice qué no está donde toca. Lo que la
    cabecera no puede delatar (columnas movidas y la cabecera retocada para que
    parezca buena) lo comprueba después el espejo contra la foto
    (`Espejo.comprobar_alineacion`)."""
    cabecera = cabecera_de(values)
    ids = _ids_por_columna(values)
    columna = ids.most_common(1)[0][0] if ids else None

    def _parar(motivo: str, remedio: str) -> NoReturn:
        raise DriveSyncError(
            f"la pestaña «{title}» tiene las columnas descolocadas: {motivo}. No se ha "
            f"escrito nada: {remedio}. Después, vuelve a actualizar."
        )

    if formato == FORMATO_DESCONOCIDO:
        if not cabecera:
            _parar(
                "no encuentro la fila de cabecera (la de «Situación», «Nº pedido»… en "
                "las 5 primeras filas) y así no se puede ver si alguna columna se ha "
                "movido",
                "vuelve a poner la fila de cabecera como estaba (Ctrl+Z, o «Historial "
                "de versiones» de la hoja)",
            )
        if _formatos_posibles(cabecera):
            _parar(
                "la cabecera está casi vacía y las «id» no dicen de qué versión es",
                "vuelve a poner los nombres de las columnas",
            )
        esperada = min(_CABECERAS.values(), key=lambda c: len(_mal_puestas(cabecera, c)))
        i = _mal_puestas(cabecera, esperada)[0]
        _parar(
            f"en la cabecera, la columna {_letra(i)} dice «{_texto(cabecera[i])}» donde "
            f"debería estar «{esperada[i]}»: se ha insertado, borrado o movido una "
            "columna a mano",
            _REMEDIO_DESHACER,
        )
    if cabecera and (movida := _columna_movida(values, cabecera, formato)) is not None:
        nombre = _texto(cabecera[movida])
        _parar(
            f"la columna {_letra(movida)} se llama «{nombre}» y tiene datos, pero está "
            "fuera de las columnas de la app (¿se ha movido a mano?)",
            f"si es la columna «{nombre}» movida, {_REMEDIO_DESHACER}; si es una columna "
            "tuya, cámbiale el nombre",
        )
    esperada_id = _COLUMNA_ID.get(formato)
    if not ids:
        sin_columna_id = esperada_id is not None and (
            len(cabecera) <= esperada_id
            or _texto(cabecera[esperada_id]).casefold() != "id"
        )
        if cabecera and sin_columna_id and _hay_filas(values):
            _parar(
                f"no está la columna «id» (la {_letra(esperada_id)}, oculta) ni ninguna "
                "«id» en las filas (¿se ha borrado la columna?)",
                "deshaz el borrado (Ctrl+Z, o «Historial de versiones» de la hoja)",
            )
        return
    if esperada_id is None:
        _parar(
            f"las «id» de sus filas están en la columna {_letra(columna)}, pero su "
            "cabecera es de una versión sin «id»",
            "revisa la cabecera",
        )
    fuera = [
        k + 1 for k, f in enumerate(values)
        if f and not is_separator(list(f))
        and any(_UUID_RE.match(_texto(c)) for j, c in enumerate(f) if j != esperada_id)
    ]
    if fuera:
        filas = ", ".join(str(n) for n in fuera[:5]) + (" …" if len(fuera) > 5 else "")
        _parar(
            f"en {len(fuera)} fila(s) (la {filas}) la «id» no está en la columna "
            f"{_letra(esperada_id)}: tienen celdas o columnas corridas (¿se han "
            "insertado o borrado celdas o columnas a mano?)",
            _REMEDIO_DESHACER,
        )


def realinear_fila(row: list[Any], header: list[Any], formato: str | None = None) -> list[Any]:
    """Una fila escrita con un formato anterior (17 o 18/19 columnas) → la del
    formato actual: se abren los huecos de «Fecha recogido» y/o «Courier» detrás
    de «Envío» —lo de delante no se mueve; lo de detrás corre una o dos
    posiciones— y se completa a su ancho. Sin perder ninguna celda (lo tecleado
    más allá de la última columna también corre). Una fila del formato actual
    solo se completa."""
    r = list(row)
    huecos = _HUECOS.get(formato or formato_de_cabecera(header), [])
    if huecos and len(r) > _HUECO:
        r[_HUECO:_HUECO] = [""] * len(huecos)
    return r + [""] * (len(SEGUIMIENTO_COLUMNS_V2) - len(r))


#: Columnas que lee y escribe la app (`tab_values`: A:Z). Lo que hubiera en la
#: última (Z) no cabe al insertar una columna: pasaría a AA, fuera de ese
#: rango, y se quedaría suelto (sin moverse con su fila).
_ANCHO_LECTURA = 26


def recuento_por_columna(values: list[list[Any]]) -> list[int]:
    """Celdas NO vacías de cada columna de la pestaña (todas las filas)."""
    out: list[int] = []
    for fila in values:
        for i, celda in enumerate(fila):
            if i >= len(out):
                out.extend([0] * (i + 1 - len(out)))
            if _texto(celda):
                out[i] += 1
    return out


def celdas_que_no_caben(values: list[list[Any]], formato: str = FORMATO_SIN_COURIER) -> int:
    """Celdas con dato en las últimas columnas que lee la app —Z, o Y y Z si el
    formato abre dos huecos (17 columnas)— o más allá: al poner la pestaña al
    formato actual se saldrían de su rango (A:Z)."""
    huecos = len(_HUECOS.get(formato, []))
    return sum(recuento_por_columna(values)[_ANCHO_LECTURA - huecos:]) if huecos else 0


def comprobar_que_cabe(values: list[list[Any]], formato: str, title: str) -> None:
    """ANTES de abrir los huecos de un formato viejo: que nada se salga del
    rango de la app (A:Z). Lo que pasara de Z quedaría fuera de lo que la app
    lee y borra, suelto, sin moverse ya con su fila: no se escribe nada."""
    fuera = celdas_que_no_caben(values, formato)
    if not fuera:
        return
    huecos = len(_HUECOS[formato])
    primera, ultima = _letra(_ANCHO_LECTURA - huecos), _letra(_ANCHO_LECTURA - 1)
    columnas = f"columna {ultima}" if huecos == 1 else f"columnas {primera} y {ultima}"
    raise DriveSyncError(
        f"la {columnas} de «{title}» tiene {fuera} celda(s) con dato: al añadir la "
        "columna «Courier» se saldrían del rango de la app y se quedarían sueltas. "
        "Muévelas a otra pestaña (o bórralas) y vuelve a actualizar. No se ha escrito "
        "nada."
    )


def _sin_filas_vacias_al_final(values: list[list[Any]]) -> list[list[Any]]:
    filas = list(values)
    while filas and not any(_texto(c) for c in filas[-1]):
        filas.pop()
    return filas


def _recuento_con_nombres(recuento: list[int], cabecera: list[Any]) -> dict[str, int]:
    nombres = [_texto(h) for h in cabecera]
    return {
        (nombres[i] if i < len(nombres) and nombres[i] else f"col {i + 1}"): n
        for i, n in enumerate(recuento) if n
    }


def migrar_columna_courier(
    sheets: ManagedTabTransport, title: str, valores: list[list[Any]],
) -> tuple[list[list[Any]], dict[str, Any]]:
    """MIGRACIÓN de la pestaña escrita antes de «Courier» (18/19 columnas → 20).

    Inserta la columna «Courier» detrás de «Envío» con `insertDimension`, en la
    cabecera y en TODAS las filas (zona viva, completados e histórico manual):
    lo de delante no se mueve y lo de detrás (Fecha recogido … la «id» oculta)
    corre una posición CON sus formatos, notas y su ocultación — que es lo que
    hace «insertar columna» en Sheets. La cabecera recibe su nombre en la MISMA
    petición (atómica): desde ese momento la pestaña tiene el formato actual y
    no se vuelve a migrar (idempotente). La columna nueva queda sin validación
    (es texto libre; la de BoHub va protegida en sus filas).

    Antes de insertar, dos comprobaciones (si falla alguna, no se toca nada):

    - se relee la pestaña y tiene que ser EXACTAMENTE lo leído: si no (alguien
      escribía, u otra pasada acaba de migrarla), se para — así nunca se
      inserta la columna dos veces;
    - la última columna que lee la app (Z) tiene que estar vacía: su contenido
      pasaría a AA, fuera del rango de la app, y se quedaría suelto.

    Y después, que no se ha perdido nada: el recuento de celdas no vacías por
    columna, antes y después, tiene que cuadrar en TODAS las columnas (lo de
    detrás de «Envío», una posición a la derecha; la columna nueva, solo con su
    cabecera), y las filas, ser las mismas. Si no cuadra (alguien escribía a la
    vez), la pasada se para sin escribir nada más; la siguiente parte ya del
    formato nuevo.

    Devuelve la pestaña releída (en el formato nuevo) y el resumen."""
    if [list(r) for r in sheets.tab_values(title, raw=True)] != [list(r) for r in valores]:
        raise DriveSyncError(
            f"la pestaña «{title}» ha cambiado mientras se preparaba la columna «Courier» "
            "(alguien estaba editando, u otra actualización la acaba de añadir): no se ha "
            "escrito nada; vuelve a intentarlo en un momento"
        )
    comprobar_que_cabe(valores, FORMATO_SIN_COURIER, title)
    cabecera_vieja = cabecera_de(valores)
    fila_cabecera = next(
        (i for i, r in enumerate(valores[:5]) if es_cabecera(r)), None,
    )
    antes = recuento_por_columna(valores)
    peticiones: list[dict[str, Any]] = [{"insertDimension": {
        "range": {"sheetId": None, "dimension": "COLUMNS",
                  "startIndex": _HUECO, "endIndex": _HUECO + 1},
        "inheritFromBefore": True,
    }}]
    # El nombre, en la cabecera (si la hay: sin ella, la pone la propia pasada
    # al reescribir; nunca en una fila de datos).
    if fila_cabecera is not None:
        peticiones.append({"updateCells": {
            "range": {"sheetId": None,
                      "startRowIndex": fila_cabecera, "endRowIndex": fila_cabecera + 1,
                      "startColumnIndex": _HUECO, "endColumnIndex": _HUECO + 1},
            "rows": [{"values": [{"userEnteredValue": {"stringValue": "Courier"}}]}],
            "fields": "userEnteredValue",
        }})
    # Sin validación heredada de «Envío»: el courier es texto libre.
    peticiones.append({"setDataValidation": {"range": {
        "sheetId": None, "startColumnIndex": _HUECO, "endColumnIndex": _HUECO + 1,
    }}})
    sheets.format_tab(title, peticiones)
    nuevos = [list(r) for r in sheets.tab_values(title, raw=True)]
    despues = recuento_por_columna(nuevos)

    # Lo esperado: el mismo recuento con la columna nueva en `_HUECO` (solo su
    # cabecera), en TODAS las columnas: con Z vacía antes, lo de Y pasa a Z y
    # nada sale del rango que se lee.
    nombre = 1 if fila_cabecera is not None else 0
    base = antes + [0] * max(_HUECO - len(antes), 0)
    esperado = [*base[:_HUECO], nombre, *base[_HUECO:]]
    ancho = max(len(esperado), len(despues))
    a = esperado + [0] * (ancho - len(esperado))
    b = despues + [0] * (ancho - len(despues))
    filas_antes = len(_sin_filas_vacias_al_final(valores))
    filas_despues = len(_sin_filas_vacias_al_final(nuevos))
    resumen: dict[str, Any] = {
        "estado": "hecha",
        "formato": FORMATO_SIN_COURIER,
        "filas": filas_antes,
        # Celdas con dato antes y después (sin contar la cabecera nueva).
        "celdas_antes": sum(antes),
        "celdas_despues": sum(despues) - nombre,
        "por_columna_antes": _recuento_con_nombres(antes, cabecera_vieja),
        "por_columna_despues": _recuento_con_nombres(despues, cabecera_de(nuevos)),
    }
    if filas_antes != filas_despues or a != b:
        logger.error(
            "drive: la migración de «%s» (columna Courier) no cuadra — filas %d → %d; "
            "celdas por columna antes %s, después %s",
            title, filas_antes, filas_despues, antes, despues,
        )
        raise DriveSyncError(
            f"la pestaña «{title}» ya tiene la columna «Courier», pero el recuento de "
            "celdas no cuadra con el de antes (¿alguien estaba escribiendo?). No se ha "
            "escrito nada más: revisa la hoja y vuelve a actualizar."
        )
    logger.info(
        "drive: «%s» migrada a 20 columnas (Courier detrás de Envío): %d filas, "
        "%d celdas, ninguna perdida", title, filas_antes, resumen["celdas_antes"],
    )
    return nuevos, resumen


def quitar_columna_courier(
    sheets: ManagedTabTransport, title: str, *, apply: bool = False,
) -> dict[str, Any]:
    """VUELTA ATRÁS de `migrar_columna_courier`, SOLO para volver a la versión
    anterior a «Courier» (ver docs/guia-erp-usuario.md): borra la columna O
    («Courier») con la cuenta de servicio —que puede aunque la hoja sea suya o
    esté protegida—; lo de detrás corre una posición a la izquierda con sus
    formatos y la «id» sigue la última y oculta. Se pierden las celdas de
    «Courier» (la versión anterior no las tiene).

    Las mismas comprobaciones que al insertarla: la pestaña tiene que tener su
    cabecera de 20 columnas y estar bien colocada (`comprobar_columnas`; si no,
    se dice qué falla y que NO se arranque la versión anterior hasta
    arreglarlo), se relee justo antes de borrar (si ha cambiado, no se toca) y
    después el recuento de celdas por columna tiene que cuadrar. Sin `apply`,
    solo informa."""
    valores = [list(r) for r in sheets.tab_values(title, raw=True)]
    cabecera = cabecera_de(valores)
    formato = formato_de_pestana(valores)
    if formato == FORMATO_DESCONOCIDO or not cabecera:
        try:
            comprobar_columnas(valores, formato, title)
        except DriveSyncError as exc:
            raise DriveSyncError(
                f"{exc} NO arranques la versión anterior hasta arreglarlo: la leería "
                "corrida."
            ) from exc
        raise DriveSyncError(
            f"la pestaña «{title}» no tiene fila de cabecera: vuelve a ponerla (Ctrl+Z, "
            "o «Historial de versiones») antes de quitar «Courier». NO arranques la "
            "versión anterior hasta arreglarlo."
        )
    if formato != FORMATO_ACTUAL:
        comprobar_columnas(valores, formato, title)
        raise DriveSyncError(
            f"la pestaña «{title}» ya está en el formato anterior a «Courier» "
            f"(«{formato}»): no hay nada que quitar"
        )
    try:
        comprobar_columnas(valores, formato, title)
    except DriveSyncError as exc:
        raise DriveSyncError(
            f"{exc} NO arranques la versión anterior hasta arreglarlo: la leería corrida."
        ) from exc
    fila_cab = _fila_de_cabecera(valores)
    antes = recuento_por_columna(valores)
    resumen: dict[str, Any] = {
        "filas": len(_sin_filas_vacias_al_final(valores)),
        "celdas_courier": sum(
            1 for k, f in enumerate(valores)
            if k != fila_cab and len(f) > _COURIER_INDEX and _texto(f[_COURIER_INDEX])
        ),
        "celdas_antes": sum(antes),
        "aplicado": False,
    }
    if not apply:
        return resumen
    if [list(r) for r in sheets.tab_values(title, raw=True)] != valores:
        raise DriveSyncError(
            f"la pestaña «{title}» ha cambiado mientras se preparaba: no se ha borrado "
            "nada; vuelve a intentarlo en un momento"
        )
    sheets.format_tab(title, [{"deleteDimension": {"range": {
        "sheetId": None, "dimension": "COLUMNS",
        "startIndex": _COURIER_INDEX, "endIndex": _COURIER_INDEX + 1,
    }}}])
    nuevos = [list(r) for r in sheets.tab_values(title, raw=True)]
    # Lo esperado: la misma pestaña sin la columna O. La última columna leída
    # (Z) no se compara: puede haber recibido lo que había en AA, que la app no
    # ve (no es una pérdida); por lo mismo, las filas se cuentan sin ella.
    ultima = _ANCHO_LECTURA - 1

    def _sin_z(filas: list[list[Any]]) -> list[list[Any]]:
        return _sin_filas_vacias_al_final([list(f[:ultima]) for f in filas])

    esperadas = _sin_z([[*f[:_COURIER_INDEX], *f[_COURIER_INDEX + 1:]] for f in valores])
    leidas = _sin_z(nuevos)
    a, b = recuento_por_columna(esperadas), recuento_por_columna(leidas)
    ancho = max(len(a), len(b))
    if len(esperadas) != len(leidas) or a + [0] * (ancho - len(a)) != b + [0] * (ancho - len(b)):
        logger.error(
            "drive: quitar «Courier» de «%s» no cuadra — esperado %s, leído %s", title, a, b,
        )
        raise DriveSyncError(
            f"la columna «Courier» de «{title}» se ha borrado, pero el recuento de celdas "
            "no cuadra con el de antes (¿alguien estaba escribiendo?): revisa la hoja "
            "antes de arrancar la versión anterior"
        )
    despues = recuento_por_columna(nuevos)
    resumen.update({
        "aplicado": True, "celdas_despues": sum(despues),
        # Lo que había más allá de Z (invisible para la app) y ahora está en Z.
        "celdas_que_entran_en_z": despues[ultima] if len(despues) > ultima else 0,
    })
    logger.info("drive: columna «Courier» quitada de «%s» (vuelta atrás): %d filas, "
                "%d celdas de Courier", title, resumen["filas"], resumen["celdas_courier"])
    return resumen


def realinear_pestana(values: list[list[Any]]) -> list[list[Any]]:
    """La pestaña ENTERA (cabecera, zona viva, separador, completados e
    histórico manual) puesta al formato actual, si se escribió con uno anterior.
    La cabecera recibe los nombres de las columnas nuevas; el resto de filas,
    huecos vacíos. Igual que haría insertar las columnas en la hoja. Con el
    formato actual, la devuelve tal cual (copia)."""
    filas = [list(r) for r in values]
    formato = formato_de_pestana(filas)
    huecos = _HUECOS.get(formato)
    if not huecos:
        return filas
    out: list[list[Any]] = []
    for r in filas:
        if es_cabecera(r) and len(r) >= _HUECO:
            out.append([*r[:_HUECO], *huecos, *r[_HUECO:]])
        elif len(r) > _HUECO:
            out.append([*r[:_HUECO], *([""] * len(huecos)), *r[_HUECO:]])
        else:
            out.append(r)
    return out


def normalize_static_pedidos(
    static: list[list[Any]], old_header: list[Any],
) -> list[list[Any]]:
    """El bloque estático de «Seguimiento (app)», puesto al día del formato:

    - si la pestaña se escribió con un formato anterior (17 columnas, sin
      «Fecha recogido»; 18/19, sin «Courier»), se abren los huecos de esas
      columnas en cada fila, para que el histórico no quede desplazado bajo la
      cabecera nueva (normalmente ya llega realineada: `realinear_pestana`);
    - lo que el import viejo empaquetó en «Nota / Incidencia»
      (`Vendedor: WEB · Transporte: UPS · Preparado: … · Proforma: …`) se
      reparte a sus columnas, sin pisar lo que ya tuviera dato;
    - las fechas reconocibles pasan a valor de fecha (serial); las rotas se
      quedan como texto.

    El separador no se toca (más allá del hueco). Idempotente."""
    if not static:
        return []
    huecos = _HUECOS.get(formato_de_cabecera(old_header), [])
    out: list[list[Any]] = []
    for row in static:
        r = list(row)
        if huecos and len(r) > _HUECO:
            r[_HUECO:_HUECO] = [""] * len(huecos)
        # El histórico manual se preserva byte a byte: NO se rellena la «id»
        # técnica aquí. La fila gana su id solo cuando el backfill se lo asigna
        # (por id de pedido real o sintético), reescribiendo esa celda.
        out.append(r if is_separator(r) else redistribute_nota(r))
    return dates_to_serial(out, HISTORICO_DATE_COLUMNS)


# --- filas añadidas A MANO en la zona viva (transición) ------------------------
#
# No todos los pedidos pasan aún por BoHub. El equipo teclea el que falta en la
# primera fila de «Seguimiento (app)» con Origen = MANUAL, y esa fila tiene que
# sobrevivir a cada «Actualizar»: por eso el volcado LEE la zona viva antes de
# reescribirla (leer → fusionar → escribir) en vez de pisarla a ciegas.
#
# - El marcador es la columna Origen = «MANUAL» (tolerante a may/min y
#   espacios): es el ÚNICO criterio. Lo demás de la zona viva es salida de BoHub
#   y se reescribe como siempre.
# - Las manuales se re-emiten MEZCLADAS con las de BoHub, todas juntas por
#   Fecha (más reciente primero, igual que la zona viva de BoHub): una fila
#   manual del 21/09 queda entre los pedidos de BoHub del 21/09, no fijada
#   arriba. Solo si no tiene una Fecha reconocible sube arriba del todo, para
#   que no se pierda de vista. Nunca se toca una celda que el usuario haya
#   escrito.
# - Si el pedido llega después por BoHub (mismo Nº), se FUSIONA en una sola
#   fila: BoHub rellena los huecos, lo escrito a mano se conserva y lo que
#   difiere se marca en la Nota («⚠ BoHub Columna: valor»), nunca se pisa.
#   Cuando la fila ya no tiene nada que perder (sin conflictos y sin nada que
#   solo esté escrito a mano), se ENTREGA a BoHub: deja de ser manual y la
#   escribe la sincronización con su Origen real. Si no, se queda MANUAL —
#   pasarla a BoHub haría que la siguiente pasada pisara en silencio lo que el
#   usuario escribió.

#: Valor de la columna Origen que marca una fila tecleada a mano.
MANUAL_MARKER = "MANUAL"
#: Prefijo de las marcas de conflicto que la app escribe en la Nota, siempre
#: ENTRE CORCHETES (`[⚠ BoHub Cliente: Roca SL]`): así se quitan enteras y se
#: recalculan en cada pasada aunque el valor lleve «·» dentro (`1 · Bomedia`),
#: sin tocar ni una letra de lo que escribió el usuario.
CONFLICT_PREFIX = "⚠ BoHub"
#: Marca de las celdas que RELLENÓ BoHub (`[BoHub: Factura#1a2b, Tracking#…]`),
#: cada una con la huella del valor que escribió. En la siguiente pasada, si la
#: celda sigue con ese valor (nadie la ha tocado), se pone al día con BoHub en
#: vez de quedarse congelada; si el usuario la ha corregido, la huella ya no
#: casa y la celda pasa a ser SUYA (lo escrito a mano manda, como siempre).
OWNED_PREFIX = "BoHub:"
#: Marcas de la app en la Nota: `[BoHub: …]` (celdas rellenadas), `[⚠ BoHub …]`
#: (conflictos) y `[⚠ revisar: …]` (espejo, Fase 2: fila que no valida). Todas
#: se quitan al leer y se recalculan en cada pasada.
_MARCAS_RE = re.compile(r"\s*\[(?:⚠ BoHub|BoHub:|⚠ revisar:)[^\]]*\]")
_PROPIAS_RE = re.compile(r"\[BoHub:([^\]]*)\]")

_SITUACION_INDEX = SEGUIMIENTO_COLUMNS_V2.index("Situación")
_NUMERO_INDEX = SEGUIMIENTO_COLUMNS_V2.index("Nº pedido")
_ORIGEN_INDEX = SEGUIMIENTO_COLUMNS_V2.index("Origen")
_IMPORTE_INDEX = SEGUIMIENTO_COLUMNS_V2.index("Importe")
_NOTA_INDEX = SEGUIMIENTO_COLUMNS_V2.index("Nota / Incidencia")
#: Lo que BoHub escribe cuando «no hay dato»: cuenta como vacío al fusionar.
_BOHUB_VACIOS = {"", "—", "-"}
#: Etiqueta de Situación (tal como se ve en la hoja) → clave, para colorear
#: también las filas manuales.
_SITUACION_POR_ETIQUETA = {v.casefold(): k for k, v in SITUACION_LABELS.items()}


def _texto(value: Any) -> str:
    return str(value if value is not None else "").strip()


def is_manual_row(row: list[Any]) -> bool:
    """¿Fila tecleada a mano? Origen = MANUAL (may/min y espacios aparte)."""
    if len(row) <= _ORIGEN_INDEX:
        return False
    return _texto(row[_ORIGEN_INDEX]).replace(" ", "").casefold() == MANUAL_MARKER.casefold()


def es_cabecera(row: list[Any]) -> bool:
    """¿Es la fila de cabecera? Si alguien la borra o mete una fila encima, la
    primera fila podría ser un pedido tecleado a mano: no se descarta a ciegas."""
    celdas = {_texto(c).casefold() for c in row}
    return "nº pedido" in celdas or "situación" in celdas


def live_zone(values: list[list[Any]]) -> list[list[Any]]:
    """Las filas de la zona VIVA de la pestaña: de debajo de la cabecera (si
    la hay) hasta el separador del histórico (o hasta el final si no hay)."""
    if not values:
        return []
    cuerpo: list[list[Any]] = []
    for row in values:
        if is_separator(list(row)):
            break
        if not es_cabecera(row):          # la cabecera, esté donde esté
            cuerpo.append(list(row))
    return cuerpo


_FECHA_VISTA_RE = re.compile(r"^\d{1,4}[/-]\d{1,2}[/-]\d{1,4}")
_HORA_VISTA_RE = re.compile(r"^\d{1,2}:\d{2}")


def cabecera_de(values: list[list[Any]]) -> list[Any]:
    """La fila de cabecera de la pestaña (la primera que lo parezca), o []."""
    return next((list(r) for r in values[:5] if es_cabecera(r)), [])


def _como_se_ve(cruda: list[Any], vista: list[Any]) -> list[Any]:
    """Una fila manual leída en bruto, con los NÚMEROS de columnas sin formato
    propio sustituidos por el texto que se veía. Una fecha tecleada en, p. ej.,
    Preparación se lee en bruto como su serial (46262); al subir o bajar la
    fila, esa celda ya no tendría su formato de fecha y se vería el número. Con
    el texto que se veía («28/08/2026») se queda igual que la tecleó el usuario.
    Las columnas de fecha y el importe no: esas llevan su formato en la zona
    viva y conviene que sigan siendo números."""
    out = list(cruda)
    for col, valor in enumerate(cruda):
        if (col in PEDIDOS_DATE_COLUMNS or col in (_IMPORTE_INDEX, _NUMERO_INDEX)
                or col >= len(vista)):
            continue
        if isinstance(valor, (int, float)) and not isinstance(valor, bool):
            texto = _texto(vista[col])
            # Solo si lo que se ve es una FECHA u HORA. Un número mostrado de
            # otra forma («123.456», «1,23E+14») se queda como número: su
            # texto perdería dígitos o cambiaría de significado.
            if texto and (_FECHA_VISTA_RE.match(texto) or _HORA_VISTA_RE.match(texto)):
                out[col] = texto
    return out


def manual_rows(
    values: list[list[Any]], formatted: list[list[Any]] | None = None,
) -> list[list[Any]]:
    """Las filas manuales de la zona viva, en su orden, a 18 columnas, con las
    fechas como valor de fecha y el resto tal cual. `values` es la lectura en
    bruto; `formatted` (opcional), la misma pestaña como se VE, para no perder
    el aspecto de una fecha tecleada en una columna que no es de fecha."""
    if not values:
        return []
    header = cabecera_de(values)
    vistas = live_zone(formatted) if formatted else []
    filas: list[list[Any]] = []
    for i, cruda in enumerate(live_zone(values)):
        fila = realinear_fila(cruda, header)
        if not is_manual_row(fila):
            continue
        if i < len(vistas):
            fila = _como_se_ve(fila, realinear_fila(vistas[i], header))
        filas.append(fila)
    return dates_to_serial(filas, PEDIDOS_DATE_COLUMNS)


def _numero_normalizado(value: Any) -> str:
    """Nº de pedido para emparejar: sin espacios, sin el `.0` que añade la
    hoja a los numéricos, en minúsculas."""
    s = _texto(value).replace(" ", "").casefold()
    while s.endswith(".0"):
        s = s[:-2]
    return s


def _importe(value: Any) -> float | None:
    """«1.234,56 €», «121,00», «121.5» o 121.0 → float; None si no es número."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = _texto(value).replace("€", "").replace(" ", "").replace("\u00a0", "")
    if not s:
        return None
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _vacio(value: Any) -> bool:
    return not _texto(value)


def _sin_dato(col: int, value: Any) -> bool:
    """¿La celda no dice nada? Vacía, «—»/«-» (la convención de la hoja para
    «sin dato», la use BoHub o quien teclea) o un importe 0."""
    if _texto(value) in _BOHUB_VACIOS:
        return True
    return col == _IMPORTE_INDEX and (_importe(value) or 0.0) == 0.0


def _vacio_bohub(col: int, value: Any) -> bool:
    """¿BoHub no tiene dato aquí? Un importe 0 en BoHub es «sin importe»: no
    se rellena un 0,00 € falso ni se marca como conflicto."""
    return _sin_dato(col, value)


def _iguales(col: int, manual: Any, bohub: Any) -> bool:
    """¿El valor escrito a mano y el de BoHub dicen lo mismo? Las fechas por
    su valor (da igual «28/08/2026» que el serial), el importe por número, el
    resto como texto sin mayúsculas ni espacios de más."""
    if col in PEDIDOS_DATE_COLUMNS:
        # El día, sin la hora: una fecha tecleada «01/09/2026 10:00» se lee en
        # bruto como 46266.4 y es el mismo día que el 46266 de BoHub.
        a, b = _date_cell(manual), _date_cell(bohub)
        if isinstance(a, float):
            a = math.floor(a)
        if isinstance(b, float):
            b = math.floor(b)
        return a == b
    if col == _IMPORTE_INDEX:
        a, b = _importe(manual), _importe(bohub)
        if a is not None and b is not None:
            return abs(a - b) < 0.005
    return _texto(manual).casefold() == _texto(bohub).casefold()


_NUMERO_PLANO_RE = re.compile(r"^-?\d+(?:\.\d+)?$")


def _huella(value: Any) -> str:
    """Huella corta de un valor, para saber si alguien ha tocado la celda. Un
    número vale lo mismo escrito como 121, 121.0 o «121.0» (según cómo lo
    devuelva la hoja), así que se normaliza antes."""
    if isinstance(value, str) and _NUMERO_PLANO_RE.match(value.strip()):
        value = float(value.strip())
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    # Sin pasar a minúsculas: corregir solo las mayúsculas también es corregir.
    return f"{zlib.crc32(_texto(value).encode('utf-8')):08x}"


def _separar_marcas(nota: Any) -> tuple[str, dict[str, str | None]]:
    """(lo que escribió el usuario, {columna rellenada por BoHub: huella}).
    Quita SOLO las marcas de la app —entre corchetes—; el texto del usuario
    sale tal cual."""
    texto = _texto(nota)
    propias: dict[str, str | None] = {}
    for grupo in _PROPIAS_RE.findall(texto):
        for pieza in grupo.split(","):
            nombre, _sep, huella = pieza.strip().rpartition("#")
            if not nombre:                          # sin huella: toda la pieza
                nombre, huella = pieza.strip(), ""
            if nombre:
                propias[nombre] = huella or None
    return _MARCAS_RE.sub("", texto).strip(), propias


def _en_marca(value: str) -> str:
    """Un valor dentro de una marca no puede cerrar ni abrir corchetes."""
    return value.replace("[", "(").replace("]", ")")


def _fusionar(manual: list[Any], bohub: list[Any]) -> tuple[list[Any], int, bool]:
    """Una fila manual + la de BoHub del mismo pedido → (fila fusionada,
    nº de conflictos, ¿se puede entregar a BoHub?).

    - Un hueco se rellena con lo de BoHub y queda apuntado en `[BoHub: …]`;
      las celdas apuntadas ahí son de BoHub y se ponen al día en cada pasada.
    - Lo escrito a mano manda; si BoHub dice otra cosa, se marca en la Nota
      (`[⚠ BoHub Columna: valor]`), nunca se pisa.
    - Se puede entregar a BoHub cuando no hay conflictos y no queda nada que
      solo esté escrito a mano (ni una celda que BoHub no tenga, ni la Nota
      del usuario, ni nada tecleado más allá de la última columna): entonces
      la fila de BoHub lo dice todo y la siguiente pasada no pierde nada."""
    fila = list(manual)
    nota_usuario, de_bohub = _separar_marcas(fila[_NOTA_INDEX])
    rellenadas: list[str] = []
    marcas: list[str] = []
    solo_a_mano = bool(nota_usuario) or any(
        _texto(c) for c in fila[len(SEGUIMIENTO_COLUMNS_V2):]
    )
    for col, nombre in enumerate(SEGUIMIENTO_COLUMNS_V2):
        # La «id» técnica no es un dato editable ni un conflicto: la fija el
        # writer (o el backfill), no la lógica de fusión. No va a la Nota.
        if col in (_ORIGEN_INDEX, _NUMERO_INDEX, _NOTA_INDEX, _ID_INDEX):
            continue
        mio, suyo = fila[col], bohub[col]
        suyo_vacio = _vacio_bohub(col, suyo)
        # ¿La rellenó BoHub y nadie la ha tocado desde entonces? (La huella
        # casa con lo que hay; sin huella —marca antigua— se da por intacta.)
        intacta = nombre in de_bohub and de_bohub[nombre] in (None, _huella(mio))
        if intacta or _sin_dato(col, mio):
            # Hueco, o celda de BoHub sin tocar: manda BoHub (al día).
            fila[col] = "" if suyo_vacio else suyo
            if not suyo_vacio:
                rellenadas.append(f"{nombre}#{_huella(suyo)}")
            continue
        if suyo_vacio:
            solo_a_mano = True
            continue
        if not _iguales(col, mio, suyo):
            marcas.append(f"[{CONFLICT_PREFIX} {nombre}: {_en_marca(_legible(col, suyo))}]")
    propias = f"[{OWNED_PREFIX} {', '.join(rellenadas)}]" if rellenadas else ""
    fila[_NOTA_INDEX] = " ".join(p for p in (nota_usuario, propias, *marcas) if p)
    return fila, len(marcas), not marcas and not solo_a_mano


def _legible(col: int, value: Any) -> str:
    """El valor de BoHub tal como lo leería una persona en la Nota: una fecha
    como «DD/MM/AAAA» (un serial ahí sería un número sin sentido) y el importe
    con dos decimales."""
    if col in PEDIDOS_DATE_COLUMNS and isinstance(value, int) and not isinstance(value, bool):
        d = _SHEETS_EPOCH + timedelta(days=value)
        return f"{d.day:02d}/{d.month:02d}/{d.year}"
    if col == _IMPORTE_INDEX and isinstance(value, (int, float)):
        return f"{float(value):.2f}".replace(".", ",")
    return _texto(value)


def merge_manual_rows(
    manuales: list[list[Any]], rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Cruza las filas manuales con los pedidos de BoHub por Nº de pedido.

    Devuelve `manuales` (las que sobreviven, ya fusionadas), `consumidos`
    (las filas de BoHub —por identidad del objeto, no por un campo que podría
    faltar— que NO se escriben en su zona porque ya están en una fila manual:
    nunca duplicar) y los recuentos para la vista previa.

    Emparejamiento: PRIMERO por `id` de pedido (columna técnica, hito id estable)
    —exacto, sin falsos positivos—; si la fila aún no lo lleva (transición, antes
    del backfill), por Nº normalizado (`BOPRIN-99931` ≡ ` boprin-99931 `); si no,
    por el número desnudo (`99931`) SOLO si un único pedido de BoHub lo tiene —dos
    tiendas pueden compartir número y casar la equivocada sería peor que no
    casar—. Nº/id vacíos → fila manual suelta. (El casado por Nº es la «semilla
    del backfill»: cuando cada fila lleve su `id`, este manda y el Nº ya no.)"""
    por_id: dict[str, dict[str, Any]] = {}
    exactos: dict[str, dict[str, Any]] = {}
    desnudos: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        oid = _texto(r.get("id"))
        if oid:
            por_id.setdefault(oid, r)
        numero = _numero_normalizado(r.get("order_number"))
        if numero:
            exactos.setdefault(numero, r)
        # Número desnudo con las MISMAS reglas que el resto del sistema
        # (`match_number`): 4 dígitos como mínimo y nunca un `MANUAL-`.
        desnudo = match_number(r.get("order_number"))
        if desnudo:
            desnudos.setdefault(desnudo, []).append(r)

    def _pedido_de(fila: list[Any]) -> dict[str, Any] | None:
        # 1) Por id de pedido (columna técnica): exacto, es la clave estable.
        fid = _texto(fila[_ID_INDEX]) if len(fila) > _ID_INDEX else ""
        if fid and fid in por_id:
            return por_id[fid]
        # 2) Transición: por Nº mientras la fila no tenga id (semilla del backfill).
        numero = _numero_normalizado(fila[_NUMERO_INDEX])
        if not numero:
            return None
        if numero in exactos:
            return exactos[numero]
        # El número desnudo solo si el usuario tecleó SOLO el número: `BOP-9`
        # no puede casar con `FLUXLA-9` por compartir el 9.
        if numero.isdigit():
            candidatos = desnudos.get(match_number(numero) or "", [])
            return candidatos[0] if len(candidatos) == 1 else None
        return None

    emparejadas = [(fila, _pedido_de(fila)) for fila in manuales]
    # Dos filas manuales del mismo pedido: ninguna se entrega (si una pasara a
    # BoHub y la otra consumiera el pedido, la entregada desaparecería).
    veces: dict[int, int] = {}
    for _fila, pedido in emparejadas:
        if pedido is not None:
            veces[id(pedido)] = veces.get(id(pedido), 0) + 1

    quedan: list[list[Any]] = []
    consumidos: set[int] = set()
    fusionadas = entregadas = conflictos = 0
    for fila, pedido in emparejadas:
        if pedido is None:
            quedan.append(fila)
            continue
        suyo = dates_to_serial([row_to_pedidos_values(pedido)], PEDIDOS_DATE_COLUMNS)[0]
        fusion, n_conflictos, entregable = _fusionar(fila, suyo)
        # Una fila manual que casa con un pedido de BoHub (LIVE, casado claro) se
        # queda con el `id` estable de ese pedido: a partir de aquí «upsert por
        # id». El casado dudoso del histórico va aparte, por el backfill revisado.
        oid = _texto(pedido.get("id"))
        if oid and len(fusion) > _ID_INDEX:
            fusion[_ID_INDEX] = oid
        fusionadas += 1
        conflictos += n_conflictos
        if entregable and veces[id(pedido)] == 1:
            entregadas += 1               # la escribe BoHub, con su Origen real
            continue
        consumidos.add(id(pedido))
        quedan.append(fusion)
    return {
        "manuales": quedan,
        "consumidos": consumidos,
        "fusionadas": fusionadas,
        "entregadas": entregadas,
        "conflictos": conflictos,
    }


_COBRO_INDEX = SEGUIMIENTO_COLUMNS_V2.index("Cobro")
_ENVIO_INDEX = SEGUIMIENTO_COLUMNS_V2.index("Envío")
#: El vocabulario exacto con el que BoHub rellena esas columnas.
_COBRO_BOHUB = set(COBRO_LABELS.values())
_PREPARACION_BOHUB = {*PREPARACION_LABELS.values(), NO_APLICA, "—"}
_ENVIO_BOHUB = {*envio_vocabulary(), *envio_vocabulary_antiguo(), NO_APLICA, "—"}
#: Etiqueta de Origen que BoHub escribía antes para un pedido manual sin canal.
#: Una pestaña escrita antes de este cambio las tiene en su zona viva: NO son
#: filas tecleadas a mano (ver `_es_fila_heredada_de_bohub`).
_ORIGEN_BOHUB_ANTIGUO = "Manual"


def _es_fila_heredada_de_bohub(fila: list[Any]) -> bool:
    """¿Es una fila que escribió BoHub antes de existir el marcador?

    Hasta este cambio, un pedido manual de BoHub sin canal salía con Origen
    «Manual», que ahora es el marcador de fila tecleada. Esas filas ya están en
    la hoja y tomarlas por manuales las congelaría arriba para siempre.

    Se reconocen por su CONTENIDO, no por el Nº (que puede haberse guardado
    como número) ni consultando BoHub (una fila tecleada con «Manual» cuyo
    pedido entra luego en BoHub no puede confundirse con una heredada):
    Origen exactamente «Manual» —así lo escribía BoHub; el marcador que se
    teclea es «MANUAL»—, sin marcas de la app en la Nota, y Cobro, Preparación
    y Envío con el vocabulario EXACTO que usa BoHub. Quien teclea un pedido a
    mano no rellena esas tres columnas con esas etiquetas."""
    if _texto(fila[_ORIGEN_INDEX]) != _ORIGEN_BOHUB_ANTIGUO:
        return False
    if _MARCAS_RE.search(_texto(fila[_NOTA_INDEX])):
        return False
    return (
        _texto(fila[_COBRO_INDEX]) in _COBRO_BOHUB
        and _texto(fila[PREPARACION_INDEX]) in _PREPARACION_BOHUB
        and (_texto(fila[_ENVIO_INDEX]) in _ENVIO_BOHUB
             or _texto(fila[_ENVIO_INDEX]).startswith("Enviado · "))
    )


def rows_for_format(values: list[list[Any]]) -> list[dict[str, Any]]:
    """Filas ya escritas (listas de celdas) → lo mínimo que necesita el
    formato: la Situación, leída de su etiqueta, para colorearla. Una
    etiqueta que no se reconoce no se colorea (antes se pintaba de «Listo»)."""
    out: list[dict[str, Any]] = []
    for row in values:
        etiqueta = _texto(row[_SITUACION_INDEX] if row else "").casefold()
        out.append({"situacion": _SITUACION_POR_ETIQUETA.get(etiqueta)})
    return out


def _clave_numero_desc(texto: str) -> tuple[int, ...]:
    """Desempate determinista: el Nº de pedido «más alto» (como texto)
    primero, para que dos pasadas seguidas ordenen igual — el mismo desempate
    que usa `sort_by_fecha_desc` con el pedido de BoHub."""
    return tuple(-ord(c) for c in texto.casefold())


def _clave_zona_viva(fila: list[Any]) -> tuple[int, float, tuple[int, ...]]:
    """Orden de una fila YA MATERIALIZADA (18 columnas, Fecha como serial si
    se pudo leer): por Fecha, más reciente primero — el mismo criterio que
    `sort_by_fecha_desc`, con el mismo desempate por Nº de pedido.

    Sin una Fecha reconocible, el criterio depende de si la fila es tecleada a
    mano (Origen = MANUAL): esa sube arriba del todo, para que no se pierda de
    vista; una de BoHub se queda al final, como ya hacía `sort_by_fecha_desc`."""
    fecha = fila[2] if len(fila) > 2 else ""
    numero = _clave_numero_desc(_texto(fila[_NUMERO_INDEX] if len(fila) > _NUMERO_INDEX else ""))
    if isinstance(fecha, (int, float)) and not isinstance(fecha, bool):
        return (1, -float(fecha), numero)
    return (0, 0.0, numero) if is_manual_row(fila) else (2, 0.0, numero)


def zona_viva_pedidos(
    rows: list[dict[str, Any]], manual: list[list[Any]] | None = None,
) -> list[list[Any]]:
    """La zona viva de «Seguimiento (app)»: los pedidos de BoHub (`rows`) y
    las filas tecleadas a mano (`manual`) YA FUSIONADAS, todas juntas y por
    Fecha (más reciente primero) — el mismo criterio y sentido que ya usaba
    la zona viva de BoHub (`sort_by_fecha_desc`). Una manual del 21/09 queda
    entre los pedidos de BoHub del 21/09, no fijada arriba; una manual sin
    Fecha reconocible sí sube arriba del todo, para que no se pierda de
    vista (una de BoHub sin fecha se sigue quedando al final, como siempre)."""
    de_bohub = dates_to_serial(
        [row_to_pedidos_values(row) for row in rows], PEDIDOS_DATE_COLUMNS,
    )
    return sorted([*(manual or []), *de_bohub], key=_clave_zona_viva)


def build_pedidos_grid(
    rows: list[dict[str, Any]], static: list[list[Any]] | None = None,
    manual: list[list[Any]] | None = None,
) -> list[list[Any]]:
    """Cabecera + zona viva (manuales y de BoHub mezcladas por Fecha, ver
    `zona_viva_pedidos`) + bloque estático."""
    return compose(SEGUIMIENTO_COLUMNS_V2, zona_viva_pedidos(rows, manual), static or [])


def build_incidencias_grid(
    rows: list[dict[str, Any]], static: list[list[Any]] | None = None,
) -> list[list[Any]]:
    return compose(INCIDENCIAS_COLUMNS, live_incidencias_rows(rows), static or [])


def historic_block(
    historic_rows: list[list[Any]], *, columns: int = len(SEGUIMIENTO_COLUMNS_V2),
) -> list[list[Any]]:
    """El único separador «HISTÓRICO» + las filas de debajo (completados de BoHub
    y/o histórico manual), listo para pegarlo bajo la zona viva. [] si no hay
    filas (no se escribe un separador suelto)."""
    if not historic_rows:
        return []
    return [_separator_row(SEPARATOR_PEDIDOS, columns), *historic_rows]


#: Etiqueta de la columna Situación de un pedido COMPLETADO en el histórico.
#: No es una cola de la línea de vida: dice, sin más, que Bart lo dio por
#: cerrado («Marcar completado»). Es además la MARCA que distingue una fila de
#: BoHub (que la app regenera en cada pasada) del histórico MANUAL —irrepetible—
#: que usa «Histórico»: por eso el histórico manual se preserva y los completados
#: se vuelven a escribir al día. El bloque de completados no se colorea (el color
#: va solo en la zona viva).
SITUACION_COMPLETADO_LABEL = "Completado"


def es_fila_completado(row: list[Any]) -> bool:
    """¿Fila de un COMPLETADO de BoHub? (Situación = «Completado»). Es lo que
    separa lo que la app regenera del histórico MANUAL, ahora que ambos viven
    bajo el mismo separador «HISTÓRICO» (sin un bloque etiquetado aparte)."""
    if len(row) <= _SITUACION_INDEX:
        return False
    return _texto(row[_SITUACION_INDEX]).casefold() == SITUACION_COMPLETADO_LABEL.casefold()


def completados_values(rows: list[dict[str, Any]]) -> list[list[Any]]:
    """Los pedidos COMPLETADOS como filas de «Pedidos» (misma serialización que
    la zona viva), con la Situación fijada a «Completado» y las fechas como
    valor de fecha."""
    filas: list[list[Any]] = []
    for row in rows:
        vals = row_to_pedidos_values(row)
        vals[_SITUACION_INDEX] = SITUACION_COMPLETADO_LABEL
        filas.append(vals)
    return dates_to_serial(filas, PEDIDOS_DATE_COLUMNS)


def _numeros_del_bloque(bloque: list[list[Any]]) -> set[str]:
    """Números (desnudos, ≥4 dígitos) presentes en un bloque de filas, por su
    columna Nº. Sirve para no duplicar contra el histórico manual."""
    nums: set[str] = set()
    for row in bloque:
        if len(row) > _NUMERO_INDEX:
            n = match_number(row[_NUMERO_INDEX])
            if n:
                nums.add(n)
    return nums


def completados_filas(
    completados: list[dict[str, Any]], manual_static: list[list[Any]],
) -> list[list[Any]]:
    """Las filas de los pedidos COMPLETADOS de BoHub (SIN separador propio), para
    pegarlas bajo el separador «HISTÓRICO», encima del histórico manual.

    - Se REGENERAN enteras desde la BD en cada actualización → «Envío en vivo»:
      tracking, fecha recogido, `transport_status` del webhook, factura, cobro…
      quedan al día por Nº aunque el pedido ya esté abajo, en el histórico. Y es
      idempotente por construcción (una fila por pedido, sin duplicar entre
      pasadas: al releer, la fila anterior —Situación «Completado»— se descarta).
    - Deduplicado por Nº contra el histórico MANUAL (`manual_static`): un
      completado que Bart ya tenía tecleado a mano no se vuelve a escribir.
    Sin completados (o si todos ya están a mano), [].

    Nota (evolución): este casado por Nº es el ÚNICO punto que asume el Nº como
    identidad. Cuando la hoja lleve un `id` de pedido estable (columna `id` +
    histórico importado a `seguimiento_legacy` con ids), este match por Nº pasa a
    ser el «backfill de la primera vez» para asignar esos ids; el resto de la
    lógica ya es por marca (`Situación = "Completado"`), no por Nº, así que no hay
    arquitectura que desmontar."""
    if not completados:
        return []
    ya_a_mano = _numeros_del_bloque(manual_static)
    filas: list[list[Any]] = []
    for vals in completados_values(sort_by_fecha_desc(completados)):
        num = match_number(vals[_NUMERO_INDEX])
        if num and num in ya_a_mano:
            continue  # ya está en el histórico manual: no duplicar
        filas.append(vals)
    return filas


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
        fill = SITUACION_FILL.get(row.get("situacion") or "")
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
    manual: list[list[Any]] | None = None,
) -> list[dict[str, Any]]:
    """Formato completo de la pestaña: cabecera, anchos, Importe en €,
    Situación coloreada (solo en la zona VIVA) y el separador destacado. La
    zona viva mezcla las filas `manual` y las de BoHub por Fecha (ver
    `zona_viva_pedidos`)."""
    # MISMA función que `build_pedidos_grid`: el color de Situación va por
    # índice de fila, así que grid y formato no pueden ordenar distinto.
    ordered = rows_for_format(zona_viva_pedidos(rows, manual))
    columns = len(SEGUIMIENTO_COLUMNS_V2)
    total_rows = len(ordered) + 1
    requests = _header_format(columns, _PEDIDOS_WIDTHS_PX)
    # La columna técnica «id» (última) va OCULTA: es la clave de casado, no la mira
    # una persona. Se oculta, no se protege aquí (eso es Fase 2).
    requests.append({"updateDimensionProperties": {
        "range": {"sheetId": None, "dimension": "COLUMNS",
                  "startIndex": _ID_INDEX, "endIndex": _ID_INDEX + 1},
        "properties": {"hiddenByUser": True},
        "fields": "hiddenByUser",
    }})
    # La columna Situación se limpia antes de colorear: `replace_tab` borra los
    # valores, no el formato, y una celda que hoy no lleva color (una fila a
    # mano con una Situación que no se reconoce, o una fila que ha bajado)
    # heredaría el de la pasada anterior.
    # Hasta el FINAL de la hoja (sin `endRowIndex`): si la pestaña encoge, las
    # filas que ya no se escriben tampoco se quedan con color.
    requests.append({"repeatCell": {
        "range": {"sheetId": None, "startRowIndex": 1,
                  "startColumnIndex": 0, "endColumnIndex": 1},
        "cell": {"userEnteredFormat": {}},
        "fields": "userEnteredFormat(backgroundColor,textFormat)",
    }})
    if ordered:
        # Importe con formato de moneda.
        requests.append({"repeatCell": {
            "range": {"sheetId": None, "startRowIndex": 1,
                      "endRowIndex": total_rows,
                      "startColumnIndex": _IMPORTE_INDEX,
                      "endColumnIndex": _IMPORTE_INDEX + 1},
            "cell": {"userEnteredFormat": {"numberFormat": {
                "type": "NUMBER", "pattern": "#,##0.00 €",
            }}},
            "fields": "userEnteredFormat.numberFormat",
        }})
        requests.extend(_situacion_format(ordered))
    # Fechas como fecha (DD/MM/AAAA) en la zona viva Y en la estática. En el
    # histórico MANUAL «Preparación» también es una fecha (en la zona viva y en
    # el bloque de completados es un estado del taller), así que ahí se le da
    # formato de fecha aparte —solo del separador del histórico manual hacia
    # abajo—.
    estaticas = len(static or [])
    requests.extend(date_format_requests(
        PEDIDOS_DATE_COLUMNS, first_row=1, last_row=total_rows + estaticas,
    ))
    # El autofiltro cubre SOLO la zona viva: si abarcara el histórico, ordenar
    # por una columna mezclaría los dos bloques.
    requests.append({"setBasicFilter": {"filter": {"range": {
        "sheetId": None, "startRowIndex": 0, "endRowIndex": total_rows,
        "startColumnIndex": 0, "endColumnIndex": columns,
    }}}})
    # El separador «HISTÓRICO» en gris; y «Preparación» como fecha en el histórico
    # MANUAL. Bajo ese único separador conviven los completados de BoHub (cuya
    # «Preparación» es un ESTADO, como en la zona viva) y el histórico manual
    # (donde «Preparación» es una FECHA), así que el formato de fecha empieza en la
    # primera fila manual: la primera que no es separador ni completado.
    manual_start: int | None = None
    for offset, row in enumerate(static or []):
        abs_row = total_rows + offset
        if is_separator(row):
            requests.append(_separator_format(abs_row, columns))
        elif manual_start is None and not es_fila_completado(row):
            manual_start = abs_row
    if manual_start is not None:
        requests.extend(date_format_requests(
            (PREPARACION_INDEX,), first_row=manual_start, last_row=total_rows + estaticas,
        ))
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
    requests.extend(date_format_requests(
        INCIDENCIAS_DATE_COLUMNS, first_row=1, last_row=total_rows + len(static or []),
    ))
    requests.append({"setBasicFilter": {"filter": {"range": {
        "sheetId": None, "startRowIndex": 0, "endRowIndex": total_rows,
        "startColumnIndex": 0, "endColumnIndex": columns,
    }}}})
    if static:
        requests.append(_separator_format(total_rows, columns))
    return requests


def rescatar_filas_de_bohub(
    session: Session, espejo: Any, rows: list[dict[str, Any]],
    completados: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """INVARIANTE del volcado: una fila de BoHub que estaba en la hoja (la foto
    de la pasada anterior) NUNCA desaparece sin motivo. Si un pedido sale de la
    selección viva, o pasa a los completados, o se quitó del seguimiento, o se
    anuló (oculto por estado), o se marcó gestionado fuera: eso es legítimo.
    Cualquier otro caso es un fallo de la selección (01/10/2026: cuatro pedidos
    entregados y sin completar desaparecieron de la hoja): la fila se conserva
    —con sus datos al día— en la zona viva (o en los completados) y queda un
    aviso en el log y en el resumen (`espejo.stats["filas_rescatadas"]`)."""
    from app.erp.models.seguimiento_mirror import KIND_ORDER  # noqa: PLC0415

    previas = {rid for rid, (kind, _v) in espejo.snapshot.items() if kind == KIND_ORDER}
    escritas = {str(r.get("id")) for r in (*rows, *completados) if r.get("id")}
    faltan = previas - escritas
    if not faltan:
        return rows, completados
    from app.erp.api.seguimiento import _rows  # noqa: PLC0415

    por_id = {str(r["id"]): r for r in _rows(session) if str(r.get("id")) in faltan}
    rows, completados = list(rows), list(completados)
    rescatadas: list[str] = []
    for rid in sorted(faltan):
        fila = por_id.get(rid)
        if fila is None:
            continue                                  # el pedido ya no existe
        if fila["excluido"] or (fila["oculto_por_estado"] and not fila["forzado"]):
            continue                                  # quitado o anulado
        if fila["completado"]:
            completados.append(fila)
        elif fila["en_curso"]:
            rows.append(fila)
        else:
            continue                                  # gestionado fuera
        rescatadas.append(str(fila.get("order_number") or rid))
    if rescatadas:
        logger.warning(
            "drive: %d pedido(s) de BoHub iban a desaparecer de «Seguimiento (app)» sin "
            "estar quitados, anulados, completados ni gestionados fuera; se conservan "
            "sus filas: %s", len(rescatadas), ", ".join(rescatadas),
        )
        espejo.stats["filas_rescatadas"] = rescatadas
    return rows, completados


def push_managed_tabs(
    session: Session,
    sheets: ManagedTabTransport,
    rows: list[dict[str, Any]],
    *,
    completados: list[dict[str, Any]] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Vuelca el formato nuevo a la pestaña gestionada (y a la de incidencias).

    `rows` son los pedidos EN CURSO (la zona viva). `completados` son los
    pedidos que Bart dio por cerrados («Marcar completado»): en vez de
    desaparecer al salir de la zona viva, se acumulan en el HISTÓRICO —su
    propio bloque, encima del histórico manual, regenerado y deduplicado en
    cada actualización— para que la hoja sea el historial completo de BoHub.
    El histórico MANUAL (irrepetible) se preserva byte a byte, aparte.

    `dry_run` calcula el resumen SIN tocar Drive: es la vista previa que Bart
    ve antes de confirmar. Devuelve qué pestañas se escribirían, cuántas filas
    y el desglose por Situación, más la pestaña histórica que NO se toca."""
    pedidos_tab, incidencias_tab = managed_tab_titles(session)

    existing = sheets.tab_titles()
    # LEER antes de escribir: la pestaña trae las filas tecleadas a mano
    # (Origen = MANUAL) en su zona viva y el histórico bajo el separador.
    # Ambas cosas se conservan; lo demás de la zona viva es de BoHub y se
    # reescribe. Se lee también en dry-run, para que la vista previa diga qué
    # sobrevive — que es justo lo que da miedo al pulsar.
    # Se lee EN BRUTO (sin formatear: números como números, fechas como
    # serial, texto como texto) y se escribe igual, para que una celda tecleada
    # a mano vuelva exactamente como estaba — leída formateada y reescrita
    # «como si la teclease un usuario», Sheets la reinterpretaría.
    valores_pedidos = (
        sheets.tab_values(pedidos_tab, raw=True) if pedidos_tab in existing else []
    )
    valores_incidencias = (
        sheets.tab_values(incidencias_tab, raw=True) if incidencias_tab in existing else []
    )
    # Las DOS pestañas se comprueban antes de escribir ninguna: una que no sea
    # de la app (la hoja vieja en bruto, una pestaña hecha a mano) no se toca.
    _guard_pestana_de_la_app(
        pedidos_tab, valores_pedidos, SEGUIMIENTO_COLUMNS_V2, fila_de_seguimiento_app,
    )
    _guard_pestana_de_la_app(incidencias_tab, valores_incidencias, INCIDENCIAS_COLUMNS)
    # FORMATO de la pestaña, por su cabecera (o por dónde están las «id»). Si
    # las «id» no están donde dice el formato (una columna insertada o borrada a
    # mano…), no se sabe leer: se para aquí, sin tocar nada.
    formato = formato_de_pestana(valores_pedidos)
    comprobar_columnas(valores_pedidos, formato, pedidos_tab)
    # ESPEJO (Fase 2): su estado en la BD se carga ANTES de tocar la hoja. Si lo
    # guardado es del formato anterior a «Courier», se pone al día aquí (en la
    # misma transacción que el resto de la pasada); si la BD ni siquiera está
    # migrada, se para sin haber tocado la hoja.
    from app.erp.seguimiento_mirror import Espejo  # noqa: PLC0415

    espejo = Espejo.cargar(session, dry_run=dry_run)
    # Y, contra la foto, que las filas de BoHub no estén corridas (columnas
    # movidas con la cabecera retocada para parecer buena): tampoco se toca nada.
    espejo.comprobar_alineacion(realinear_pestana(valores_pedidos), pedidos_tab)
    # Una pestaña escrita antes de «Courier» (18/19 columnas) se MIGRA aquí, una
    # sola vez, insertando la columna en la propia hoja (cabecera y todas las
    # filas, con un recuento de celdas antes/después); desde ahí ya tiene el
    # formato actual. En la vista previa no se toca: se lee como si ya estuviera
    # migrada y se avisa de que se hará. Una pestaña aún más vieja (17 columnas,
    # sin «Fecha recogido» ni «id») se pone al día al reescribirla, como antes.
    migracion: dict[str, Any] | None = None
    if formato != FORMATO_ACTUAL and dry_run:
        migracion = {
            "estado": "pendiente", "formato": formato,
            "filas": len(_sin_filas_vacias_al_final(valores_pedidos)),
            "celdas_antes": sum(recuento_por_columna(valores_pedidos)),
            "celdas_que_no_caben": celdas_que_no_caben(valores_pedidos, formato),
        }
    elif formato == FORMATO_SIN_COURIER:
        valores_pedidos, migracion = migrar_columna_courier(
            sheets, pedidos_tab, valores_pedidos,
        )
    elif formato != FORMATO_ACTUAL:
        # 17 columnas: se reescribe entera con los dos huecos abiertos; antes,
        # que nada de Y/Z se salga del rango de la app.
        comprobar_que_cabe(valores_pedidos, formato, pedidos_tab)
        migracion = {
            "estado": "reescrita", "formato": formato,
            "filas": len(_sin_filas_vacias_al_final(valores_pedidos)),
        }
    # Lo leído TAL CUAL está en la hoja (para saber, antes de escribir, si alguien
    # la ha tocado mientras tanto) y la pestaña puesta al formato actual (con lo
    # que se trabaja).
    leidos_pedidos = valores_pedidos
    valores_pedidos = realinear_pestana(valores_pedidos)
    # La misma pestaña como se VE, solo si hay filas manuales: sirve para no
    # perder el aspecto de lo tecleado en columnas sin formato propio.
    vistos = (
        realinear_pestana(sheets.tab_values(pedidos_tab))
        if any(is_manual_row(r) for r in live_zone(valores_pedidos)) else None
    )
    # INVARIANTE: una fila de BoHub que estaba en la hoja no desaparece si el
    # pedido no se ha quitado, anulado, completado ni gestionado fuera.
    rows, completados = rescatar_filas_de_bohub(session, espejo, rows, completados or [])
    # ESPEJO: antes de pintar, lo que una persona editó en las columnas
    # editables de las filas de BoHub se lee de vuelta (hoja ≠ snapshot), y las
    # filas se pintan con lo manual aplicado. Sin snapshot no se infiere nada.
    rows, completados = espejo.leer_filas_bohub(valores_pedidos, rows, completados)

    manuales_leidas = [
        r for r in manual_rows(valores_pedidos, vistos)
        if not _es_fila_heredada_de_bohub(r)
    ]
    fusion = merge_manual_rows(manuales_leidas, rows)
    # Filas tecleadas a mano: se validan; las válidas reciben id y se ingieren en
    # BoHub; las que no, se marcan «⚠ revisar» y no se ingieren (#466 intacta).
    manuales = espejo.procesar_manuales(
        fusion["manuales"], {str(r.get("id")) for r in rows if r.get("id")},
    )

    # Zona viva por fecha del pedido, de más reciente a más antiguo (la
    # Situación es una columna más: con su color y reordenable con el
    # autofiltro). El bloque histórico de debajo del separador NO se toca.
    todas = sort_by_fecha_desc(rows)
    # Un pedido que ya está en una fila manual (fusionada) NO se repite en la
    # zona de BoHub de «Seguimiento (app)»: una sola fila por pedido. Pero
    # sigue siendo un pedido de BoHub: cuenta en el desglose y, si es una
    # Incidencia, sale en «Incidencias (app)».
    ordenadas = [r for r in todas if id(r) not in fusion["consumidos"]]
    incidencias = incidencia_rows(todas)
    por_situacion: dict[str, int] = {}
    for row in todas:
        clave = str(row.get("situacion_label") or row.get("situacion") or "—")
        por_situacion[clave] = por_situacion.get(clave, 0) + 1

    resumen: dict[str, Any] = {
        "mode": "managed_tab",
        "tab": pedidos_tab,
        "incidencias_tab": incidencias_tab,
        "historic_tab": pestana_archivo(existing, (pedidos_tab, incidencias_tab)),
        "rows": len(ordenadas),
        "incidencias": len(incidencias),
        "por_situacion": por_situacion,
        "columns": list(SEGUIMIENTO_COLUMNS_V2),
        "dry_run": dry_run,
        "written": False,
        # Filas tecleadas a mano que se CONSERVAN (mezcladas con las de BoHub
        # por Fecha), y cuántas de ellas ya se han cruzado con su pedido de
        # BoHub (y con cuántos conflictos marcados) o se han entregado a
        # BoHub por no tener nada que perder.
        "manuales": len(manuales),
        "manuales_fusionadas": fusion["fusionadas"],
        "manuales_entregadas": fusion["entregadas"],
        "conflictos": fusion["conflictos"],
        # Migración de la pestaña al formato con «Courier» (None si ya lo tenía):
        # «pendiente» en la vista previa; «hecha», con el recuento de celdas, al
        # insertar la columna; «reescrita» si era del formato de 17 columnas.
        "migracion_courier": migracion,
    }
    # El histórico MANUAL (irrepetible) que hay que PRESERVAR, puesto al día del
    # formato (hueco de «Fecha recogido» si la pestaña era de 17 columnas, fechas
    # como valor de fecha), sin reordenarlo ni quitar nada. `historico_manual_rows`
    # devuelve SOLO el manual: salta los separadores y las filas de completados de
    # BoHub (Situación «Completado»), que la app regenera. Así el histórico manual
    # se conserva byte a byte aunque comparta separador con los completados.
    estatico_manual = normalize_static_pedidos(
        historico_manual_rows(valores_pedidos),
        valores_pedidos[0] if valores_pedidos else [],
    )
    # ESPEJO: con el histórico ya importado a BoHub (`seguimiento_legacy`), cada
    # fila recibe su id, se leen de vuelta sus ediciones y se quitan las que son
    # el MISMO pedido que ya se pinta arriba (vivo, completado o fila manual
    # casada). Sin histórico importado, se conserva tal cual, como siempre.
    ids_vivos = (
        {str(r.get("id")) for r in (*ordenadas, *completados) if r.get("id")}
        | {_texto(f[_ID_INDEX]) for f in manuales if len(f) > _ID_INDEX and _texto(f[_ID_INDEX])}
    )
    if espejo.legacy_activo():
        estatico_manual = espejo.procesar_historico(estatico_manual, ids_vivos)
    # Los pedidos completados de BoHub, REGENERADOS en cada actualización y
    # deduplicados por Nº contra el manual: van bajo el ÚNICO separador
    # «HISTÓRICO», encima del histórico manual (sin un bloque etiquetado aparte).
    # Regenerar = «Envío en vivo»: aunque el pedido ya esté abajo, su fila se
    # reescribe al día por Nº. Idempotente (al releer, la fila anterior se
    # descarta por su Situación «Completado»). El casado por Nº solo cuenta con
    # las filas del histórico que aún NO llevan id; con id, el deduplicado es por
    # id (arriba).
    filas_completados = completados_filas(
        completados,
        [f for f in estatico_manual if not (len(f) > _ID_INDEX and _texto(f[_ID_INDEX]))],
    )
    # ESPEJO: filas manuales/del histórico que ya no están en la hoja → borrado
    # lógico en BoHub; un borrado masivo se trata como accidente y se restaura.
    ids_pintados = (
        {str(r.get("id")) for r in ordenadas if r.get("id")}
        | {_texto(f[_ID_INDEX]) for f in (*filas_completados, *manuales, *estatico_manual)
           if len(f) > _ID_INDEX and _texto(f[_ID_INDEX])}
    )
    restaurar_manuales, restaurar_historico = espejo.borrados(ids_pintados)
    manuales = [*manuales, *restaurar_manuales]
    estatico_manual = [*estatico_manual, *restaurar_historico]
    estatico_pedidos = (
        [historico_separator_row(valores_pedidos), *filas_completados, *estatico_manual]
        if (filas_completados or estatico_manual) else []
    )
    estatico_incidencias = dates_to_serial(
        static_block(valores_incidencias), INCIDENCIAS_DATE_COLUMNS,
    )
    resumen["historico_preservado"] = len(estatico_manual)
    resumen["completados_historico"] = len(filas_completados)
    # El separador no cuenta como fila de datos.
    resumen["pendientes_preservados"] = max(len(estatico_incidencias) - 1, 0)
    resumen["espejo"] = espejo.stats

    if dry_run:
        return resumen

    resumen["created_tabs"] = [
        t for t in (pedidos_tab, incidencias_tab) if t not in existing
    ]

    grid_pedidos = build_pedidos_grid(ordenadas, estatico_pedidos, manuales)
    # ESPEJO: la pestaña se reescribe entera, así que si alguien ha escrito en
    # ella MIENTRAS se calculaba esta pasada, su cambio se perdería. Se relee
    # justo antes de escribir y, si ha cambiado, se aborta sin escribir ni
    # confirmar: la pasada siguiente recoge la edición. Nada se pierde.
    if pedidos_tab in existing and sheets.tab_values(pedidos_tab, raw=True) != leidos_pedidos:
        raise DriveSyncError(
            "la hoja ha cambiado mientras se sincronizaba (alguien estaba "
            "editando): no se ha escrito nada; vuelve a intentarlo en un momento"
        )
    sheets.ensure_tab(pedidos_tab)
    sheets.replace_tab(pedidos_tab, grid_pedidos, raw=True)
    sheets.format_tab(pedidos_tab, pedidos_format(ordenadas, estatico_pedidos, manuales))
    # ESPEJO: protección de columnas bloqueadas (filas de BoHub), validación en
    # la zona viva y marca naranja de «⚠ revisar». Va aparte y es de mejor
    # esfuerzo: si Google la rechaza, los datos ya están bien escritos y la
    # pasada no se pierde (se avisa en el resumen y se reintenta en la siguiente).
    from app.erp.seguimiento_mirror import protection_requests  # noqa: PLC0415

    # Solo si el transporte sabe leer las protecciones actuales: sin eso, cada
    # pasada APILARÍA protecciones nuevas encima de las anteriores.
    leer_meta = getattr(sheets, "tab_metadata", None)
    if callable(leer_meta):
        try:
            proteccion = protection_requests(
                grid_pedidos, espejo.tipos, espejo.genei_ids, meta=leer_meta(pedidos_tab),
                service_email=getattr(sheets, "service_email", None),
            )
            sheets.format_tab(pedidos_tab, proteccion)
            espejo.stats["protecciones"] = sum(1 for r in proteccion if "addProtectedRange" in r)
        except DriveSyncError as exc:
            espejo.stats["proteccion_error"] = str(exc)[:200]
            logger.warning("drive: no se pudo aplicar la protección del espejo: %s", exc)
    # La foto nueva, SOLO tras escribir bien la hoja (si la escritura falla, el
    # llamador no confirma y la pasada siguiente lo repite todo).
    espejo.guardar_snapshot(grid_pedidos)

    sheets.ensure_tab(incidencias_tab)
    sheets.replace_tab(
        incidencias_tab, build_incidencias_grid(todas, estatico_incidencias), raw=True,
    )
    sheets.format_tab(
        incidencias_tab, incidencias_format(todas, estatico_incidencias),
    )

    resumen["written"] = True
    logger.info(
        "drive: volcado del seguimiento a «%s» (%d de BoHub + %d a mano, %d "
        "fusionadas, %d conflictos + %d completados + %d históricas) y «%s» "
        "(%d vivas + %d heredadas); pestaña «%s» sin tocar",
        pedidos_tab, len(ordenadas), len(manuales), fusion["fusionadas"],
        fusion["conflictos"], resumen["completados_historico"],
        resumen["historico_preservado"], incidencias_tab, len(incidencias),
        resumen["pendientes_preservados"], resumen["historic_tab"] or "—",
    )
    return resumen
