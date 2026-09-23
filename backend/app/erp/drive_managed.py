"""Volcado del seguimiento (formato NUEVO) a su pestaña gestionada de Drive.

La hoja de Drive de Bart tiene dos mundos y no se mezclan:

- La pestaña **histórica** (la primera del documento) es suya: miles de filas
  con anotaciones a mano. La sincronización de siempre (`drive_sheets`) solo
  INSERTA ahí y jamás reescribe ni formatea. Este módulo **no la toca**, y lo
  comprueba antes de escribir (`_guard_not_historic`).
- La pestaña **gestionada** («Seguimiento (app)», configurable) es de la app:
  se reescribe entera en cada actualización con las 17 columnas del rediseño
  2026, ordenada por fecha del pedido (más reciente primero) y con la celda
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
from datetime import date, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.erp.drive_sheets import DriveSyncError, ManagedTabTransport
from app.erp.seguimiento import (
    DATE_PATTERN,
    HISTORICO_DATE_COLUMNS,
    INCIDENCIAS_COLUMNS,
    INCIDENCIAS_DATE_COLUMNS,
    PEDIDOS_DATE_COLUMNS,
    PREPARACION_INDEX,
    SEGUIMIENTO_COLUMNS_V2,
    SITUACION_FILL,
    SITUACION_LABELS,
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

#: Anchos de columna de la pestaña «Pedidos» (píxeles ≈ los del Excel local).
_PEDIDOS_WIDTHS_PX = [96, 116, 82, 210, 76, 240, 88, 130, 102, 88,
                      96, 88, 96, 96, 92, 116, 150, 220]
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


#: Cabecera del formato nuevo ANTES de «Fecha recogido» (17 columnas, #457).
_HEADER_V2_SIN_RECOGIDO: list[str] = [
    c for c in SEGUIMIENTO_COLUMNS_V2 if c != "Fecha recogido"
]
_RECOGIDO_INDEX = SEGUIMIENTO_COLUMNS_V2.index("Fecha recogido")


def normalize_static_pedidos(
    static: list[list[Any]], old_header: list[Any],
) -> list[list[Any]]:
    """El bloque estático de «Seguimiento (app)», puesto al día del formato:

    - si la pestaña se escribió con la cabecera de 17 columnas (sin «Fecha
      recogido»), se abre el hueco de esa columna en cada fila, para que el
      histórico no quede desplazado bajo la cabecera nueva;
    - lo que el import viejo empaquetó en «Nota / Incidencia»
      (`Vendedor: WEB · Transporte: UPS · Preparado: … · Proforma: …`) se
      reparte a sus columnas, sin pisar lo que ya tuviera dato;
    - las fechas reconocibles pasan a valor de fecha (serial); las rotas se
      quedan como texto.

    El separador no se toca (más allá del hueco). Idempotente."""
    if not static:
        return []
    header = [str(h or "").strip() for h in old_header]
    realinear = header[:len(_HEADER_V2_SIN_RECOGIDO)] == _HEADER_V2_SIN_RECOGIDO
    out: list[list[Any]] = []
    for row in static:
        r = list(row)
        if realinear:
            r = r + [""] * (len(_HEADER_V2_SIN_RECOGIDO) - len(r))
            r.insert(_RECOGIDO_INDEX, "")
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
# - Las manuales se re-emiten arriba del todo, en el orden en que están (la
#   recién tecleada en la primera fila se queda en cabeza), sin tocar ninguna
#   celda que el usuario haya escrito.
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
_MARCAS_RE = re.compile(r"\s*\[(?:⚠ BoHub|BoHub:)[^\]]*\]")
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


def realinear_fila(row: list[Any], header: list[Any]) -> list[Any]:
    """Una fila escrita bajo la cabecera de 17 columnas → las 18 de ahora (se
    abre el hueco de «Fecha recogido»); en cualquier caso, a su ancho."""
    r = list(row)
    cabecera = [_texto(h) for h in header]
    if cabecera[:len(_HEADER_V2_SIN_RECOGIDO)] == _HEADER_V2_SIN_RECOGIDO:
        r = r + [""] * (len(_HEADER_V2_SIN_RECOGIDO) - len(r))
        r.insert(_RECOGIDO_INDEX, "")
    return r + [""] * (len(SEGUIMIENTO_COLUMNS_V2) - len(r))


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
        if col in PEDIDOS_DATE_COLUMNS or col == _IMPORTE_INDEX or col >= len(vista):
            continue
        if isinstance(valor, (int, float)) and not isinstance(valor, bool):
            texto = _texto(vista[col])
            crudo = _texto(int(valor) if float(valor).is_integer() else valor)
            if texto and texto != crudo:
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
    return f"{zlib.crc32(_texto(value).casefold().encode('utf-8')) & 0xFFFF:04x}"


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
        if col in (_ORIGEN_INDEX, _NUMERO_INDEX, _NOTA_INDEX):
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

    Devuelve `manuales` (las que se quedan arriba, ya fusionadas), `consumidos`
    (las filas de BoHub —por identidad del objeto, no por un campo que podría
    faltar— que NO se escriben en su zona porque ya están en una fila manual:
    nunca duplicar) y los recuentos para la vista previa.

    Emparejamiento: por Nº normalizado (`BOPRIN-99931` ≡ ` boprin-99931 `); si
    no, por el número desnudo (`99931`) SOLO si un único pedido de BoHub lo
    tiene — dos tiendas pueden compartir número y casar la equivocada sería
    peor que no casar. Nº vacío → fila manual suelta."""
    exactos: dict[str, dict[str, Any]] = {}
    desnudos: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        numero = _numero_normalizado(r.get("order_number"))
        if numero:
            exactos.setdefault(numero, r)
        # Número desnudo con las MISMAS reglas que el resto del sistema
        # (`match_number`): 4 dígitos como mínimo y nunca un `MANUAL-`.
        desnudo = match_number(r.get("order_number"))
        if desnudo:
            desnudos.setdefault(desnudo, []).append(r)

    def _pedido_de(fila: list[Any]) -> dict[str, Any] | None:
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


#: Etiqueta de Origen que BoHub escribía antes para un pedido manual sin canal.
#: Una pestaña escrita antes de este cambio las tiene en su zona viva: NO son
#: filas tecleadas a mano (ver `_es_fila_heredada_de_bohub`).
_ORIGEN_BOHUB_ANTIGUO = "Manual"


def _es_fila_heredada_de_bohub(session: Session, fila: list[Any]) -> bool:
    """¿Es una fila que escribió BoHub antes de existir el marcador?

    Hasta este cambio, un pedido manual de BoHub sin canal salía con Origen
    «Manual», que ahora es el marcador de fila tecleada. Esas filas ya están en
    la hoja: tomarlas por manuales las congelaría arriba para siempre. Se
    reconocen porque llevan EXACTAMENTE esa etiqueta (así la escribía BoHub; el
    marcador que se teclea es «MANUAL») y su Nº es de un pedido que BoHub
    conoce. Esas se descartan y BoHub las reescribe con su etiqueta nueva."""
    if _texto(fila[_ORIGEN_INDEX]) != _ORIGEN_BOHUB_ANTIGUO:
        return False
    numero = _texto(fila[_NUMERO_INDEX])
    if not numero:
        return False
    from sqlalchemy import func, select  # noqa: PLC0415

    from app.erp.models import Order  # noqa: PLC0415

    return session.scalar(
        select(func.count()).select_from(Order)
        .where(func.lower(Order.order_number) == numero.casefold()),
    ) > 0


def rows_for_format(values: list[list[Any]]) -> list[dict[str, Any]]:
    """Filas ya escritas (listas de celdas) → lo mínimo que necesita el
    formato: la Situación, leída de su etiqueta, para colorearla. Una
    etiqueta que no se reconoce no se colorea (antes se pintaba de «Listo»)."""
    out: list[dict[str, Any]] = []
    for row in values:
        etiqueta = _texto(row[_SITUACION_INDEX] if row else "").casefold()
        out.append({"situacion": _SITUACION_POR_ETIQUETA.get(etiqueta)})
    return out


def build_pedidos_grid(
    rows: list[dict[str, Any]], static: list[list[Any]] | None = None,
    manual: list[list[Any]] | None = None,
) -> list[list[Any]]:
    """Cabecera + filas manuales (arriba, en su orden) + zona viva de BoHub
    (por fecha) + bloque estático."""
    return compose(
        SEGUIMIENTO_COLUMNS_V2, [*(manual or []), *live_pedidos_rows(rows)], static or [],
    )


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
    zona viva son las filas `manual` (arriba) + las de BoHub."""
    # MISMO orden que `build_pedidos_grid` (manuales arriba, BoHub por fecha
    # desc): el color de Situación va por índice de fila, así que grid y
    # formato no pueden ordenar distinto.
    ordered = [*rows_for_format(manual or []), *sort_by_fecha_desc(rows)]
    columns = len(SEGUIMIENTO_COLUMNS_V2)
    total_rows = len(ordered) + 1
    requests = _header_format(columns, _PEDIDOS_WIDTHS_PX)
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
    # Fechas como fecha (DD/MM/AAAA) en la zona viva Y en la estática. En el
    # bloque estático «Preparación» también es una fecha (en vivo es un estado),
    # así que ahí se le da formato de fecha aparte.
    estaticas = len(static or [])
    requests.extend(date_format_requests(
        PEDIDOS_DATE_COLUMNS, first_row=1, last_row=total_rows + estaticas,
    ))
    if estaticas:
        requests.extend(date_format_requests(
            (PREPARACION_INDEX,), first_row=total_rows, last_row=total_rows + estaticas,
        ))
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
    # La misma pestaña como se VE, solo si hay filas manuales: sirve para no
    # perder el aspecto de lo tecleado en columnas sin formato propio.
    vistos = (
        sheets.tab_values(pedidos_tab)
        if any(is_manual_row(r) for r in live_zone(valores_pedidos)) else None
    )
    manuales_leidas = [
        r for r in manual_rows(valores_pedidos, vistos)
        if not _es_fila_heredada_de_bohub(session, r)
    ]
    fusion = merge_manual_rows(manuales_leidas, rows)
    manuales = fusion["manuales"]

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
        "historic_tab": sheets.first_tab_title(),
        "rows": len(ordenadas),
        "incidencias": len(incidencias),
        "por_situacion": por_situacion,
        "columns": list(SEGUIMIENTO_COLUMNS_V2),
        "dry_run": dry_run,
        "written": False,
        # Filas tecleadas a mano que se CONSERVAN arriba, y cuántas de ellas
        # ya se han cruzado con su pedido de BoHub (y con cuántos conflictos
        # marcados) o se han entregado a BoHub por no tener nada que perder.
        "manuales": len(manuales),
        "manuales_fusionadas": fusion["fusionadas"],
        "manuales_entregadas": fusion["entregadas"],
        "conflictos": fusion["conflictos"],
    }
    # Lo que hay que PRESERVAR bajo el separador, puesto al día del formato
    # (hueco de «Fecha recogido» si la pestaña era de 17 columnas, fechas como
    # valor de fecha), sin reordenarlo ni quitar nada.
    estatico_pedidos = normalize_static_pedidos(
        static_block(valores_pedidos), valores_pedidos[0] if valores_pedidos else [],
    )
    estatico_incidencias = dates_to_serial(
        static_block(sheets.tab_values(incidencias_tab, raw=True))
        if incidencias_tab in existing else [],
        INCIDENCIAS_DATE_COLUMNS,
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
    sheets.replace_tab(
        pedidos_tab, build_pedidos_grid(ordenadas, estatico_pedidos, manuales), raw=True,
    )
    sheets.format_tab(pedidos_tab, pedidos_format(ordenadas, estatico_pedidos, manuales))

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
        "fusionadas, %d conflictos + %d históricas) y «%s» (%d vivas + %d "
        "heredadas); histórico en bruto «%s» intacto",
        pedidos_tab, len(ordenadas), len(manuales), fusion["fusionadas"],
        fusion["conflictos"], resumen["historico_preservado"],
        incidencias_tab, len(incidencias), resumen["pendientes_preservados"],
        resumen["historic_tab"],
    )
    return resumen
