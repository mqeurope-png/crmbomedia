"""ERP-F6 — seguimiento de pedidos: la vista que sustituye el Excel manual.

Bart mantiene a mano `seguimiento pedidos.xls` (7.743 filas) duplicando datos
que BoHub ya tiene. Este módulo construye esas mismas filas desde la base de
datos del ERP: mismas columnas, mismo orden. El histórico del Excel NO se
importa (proyecto aparte que Bart debe decidir); BoHub enseña lo suyo.

Columnas del Excel real (el orden importa: la exportación y la hoja de Drive
lo respetan):

    Empresa · Fecha entrada albarán · Cliente · Vendedor · OFI-TER-SAT ·
    Transport · Preparado · Recogido · F Envío Factura · Productos · Proforma ·
    Albarán / Nº Pedido Web · Nº de Factura · Tracking · Nº de Serie ·
    WhiteRIP · Orden

«Orden» NO es un campo: en 7.743 filas solo hay 37 valores y son comentarios
(«EL PRIMER ENVÍO HA LLEGADO ROTO»). Su contenido vive en las observaciones
del pedido y en la hoja de Drive esa columna no se toca nunca.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.erp.models import (
    Carrier,
    InvoiceStatus,
    Order,
    OrderSource,
    TransportStatus,
)

# --- columnas del seguimiento (DEFINICIÓN ÚNICA) --------------------------------
#
# ERP-F6-fix1 — la hoja real de Bart no usa los nombres canónicos: `Nº` frente a
# `Núm`, mayúsculas cambiantes (`TRACKING`, `FACTURA`), tildes ausentes
# (`Fecha entrada albaran`, `F Envio Factura`) y, en la cabecera repetida a
# media hoja, nombres realmente distintos (`f` por Empresa, `Albarán` por
# `Albarán / Nº Pedido Web`, `FACTURA` por `Núm de Factura`). Por eso cada
# columna declara AQUÍ, en un solo sitio, sus nombres aceptados, y la
# comparación se hace SIEMPRE sobre la forma normalizada (sin tildes, en
# minúsculas, con las variantes de «número» unificadas).


@dataclass(frozen=True)
class SegColumn:
    #: Nombre canónico (el del Excel de Bart) — cabecera de export y filas nuevas.
    header: str
    #: Nombres aceptados en la hoja (se normalizan al compararlos).
    aliases: tuple[str, ...] = ()
    #: Imprescindible para identificar la fila del pedido: si falta, no se
    #: sincroniza (Parte D). El resto son opcionales: si falta una, se omite
    #: esa columna y se avisa, pero se sincroniza lo demás.
    required: bool = False
    #: Columna de notas manuales de Bart («Orden»): JAMÁS se escribe.
    unmanaged: bool = False
    #: Alias ya normalizados (se rellena en __post_init__-equivalente abajo).
    norm_aliases: frozenset[str] = field(default_factory=frozenset)


def _num_variants_unified(text: str) -> str:
    """Unifica las variantes de «número» a un único token `num`: `nº`, `n°`,
    `no.`, `núm`, `num`, `numero` y hasta la `n` suelta (`n factura`)."""
    return re.sub(r"\bn(?:o|um|umero)?\b\.?", "num", text)


def normalize_header(text: Any) -> str:
    """Forma comparable de un nombre de columna: sin tildes ni diacríticos, en
    minúsculas, espacios colapsados y variantes de «número» unificadas. Es la
    ÚNICA vía por la que se comparan cabeceras y alias."""
    s = unicodedata.normalize("NFKD", str(text or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    # º (ordinal) ya cae a «o» en NFKD; ° (grados) no descompone, se fuerza.
    s = s.replace("°", "o").replace("º", "o")
    s = s.casefold()
    s = re.sub(r"\s+", " ", s).strip()
    return _num_variants_unified(s)


def _col(header: str, *aliases: str, required: bool = False,
         unmanaged: bool = False) -> SegColumn:
    # El propio nombre canónico siempre cuenta como alias.
    all_aliases = (header, *aliases)
    return SegColumn(
        header=header, aliases=all_aliases, required=required, unmanaged=unmanaged,
        norm_aliases=frozenset(normalize_header(a) for a in all_aliases),
    )


#: Columnas del seguimiento, en el ORDEN del Excel de Bart. Los alias se listan
#: en su forma simple; se normalizan al construir la columna.
SEG_COLUMNS: list[SegColumn] = [
    _col("Empresa", "f"),
    _col("Fecha entrada albarán", "fecha entrada albaran", "fecha"),
    _col("Cliente"),
    _col("Vendedor"),
    _col("OFI-TER-SAT", "ofi ter sat"),
    _col("Transport", "transporte", "transportista"),
    _col("Preparado"),
    _col("Recogido"),
    _col("F Envío Factura", "f envio factura", "fecha envio factura"),
    _col("Productos"),
    _col("Proforma"),
    _col("Albarán / Nº Pedido Web", "albaran / num pedido web", "albaran",
         "albaran / pedido web", required=True),
    _col("Nº de Factura", "num de factura", "factura", "n factura"),
    _col("Tracking"),
    _col("Nº de Serie", "numero de serie", "n de serie", "serie"),
    _col("WhiteRIP", "white rip"),
    _col("Orden", unmanaged=True),
]

#: Cabecera canónica (export + filas nuevas) — derivada de la definición única.
SEGUIMIENTO_COLUMNS: list[str] = [c.header for c in SEG_COLUMNS]
#: Columna que identifica la fila de un pedido en la hoja.
KEY_COLUMN_INDEX = next(i for i, c in enumerate(SEG_COLUMNS) if c.required)
KEY_COLUMN = SEG_COLUMNS[KEY_COLUMN_INDEX].header
#: «Orden» es la columna de notas manuales de Bart: JAMÁS se escribe en Drive.
UNMANAGED_COLUMN_INDEXES = {i for i, c in enumerate(SEG_COLUMNS) if c.unmanaged}

#: Mínimo de columnas que debe casar una fila para considerarla una CABECERA
#: (y no una fila de datos). La cabecera superior casa las 17; la repetida a
#: media hoja casa ~12; una fila de datos, casi ninguna.
HEADER_MIN_MATCHES = 5


def match_header_columns(header_row: list[Any]) -> dict[int, int]:
    """`{índice de columna lógica → índice de columna en la hoja}` para una
    fila de cabecera, comparando por alias normalizados. Asignación 1:1 de
    izquierda a derecha (una celda no reclama dos columnas)."""
    mapping: dict[int, int] = {}
    for sheet_col, cell in enumerate(header_row):
        norm = normalize_header(cell)
        if not norm:
            continue
        for canon, col in enumerate(SEG_COLUMNS):
            if canon in mapping:
                continue
            if norm in col.norm_aliases:
                mapping[canon] = sheet_col
                break
    return mapping


def header_score(header_row: list[Any]) -> int:
    """Cuántas columnas lógicas reconoce una fila (para distinguir cabecera de
    fila de datos, y la cabecera repetida)."""
    return len(match_header_columns(header_row))


def is_structure_row(row: list[Any]) -> bool:
    """¿La fila es ESTRUCTURA (separador «^^^^», cabecera repetida) y no un
    pedido? No se interpreta ni se sobrescribe."""
    first = str(row[0] if row else "").strip()
    if not any(str(c).strip() for c in row):
        return True
    if first.startswith("^^^^"):
        return True
    return header_score(row) >= HEADER_MIN_MATCHES


# --- identificación del pedido en la hoja (ERP-F6-fix2) -------------------------
#
# Bart escribe el número de pedido DESNUDO (`99866`); BoHub escribía su
# referencia con prefijo de tienda (`BOPRIN-99866`). Al buscar su propia
# referencia no encontraba nada y duplicaba filas que ya existían. Ahora el
# pedido se identifica por el NÚMERO (ignorando el prefijo), confirmado con un
# segundo dato (factura, cliente o fecha) para no confundir dos pedidos con el
# mismo número entre tiendas.

_TRAILING_DECIMAL = re.compile(r"\.0+$")
_DIGIT_RUN = re.compile(r"\d+")
#: Formas societarias que se ignoran al comparar clientes (concatenadas, sin
#: separadores: «S.L.» → «sl»). La más larga primero.
_CLIENT_LEGAL_SUFFIXES = (
    "sociedadlimitada", "streamtec", "slne", "sarl", "sccl", "scoop", "coop",
    "bvba", "eurl", "gmbh", "ltd", "llc", "bv", "nv", "spa", "srl",
    "slu", "sau", "sas", "sll", "scp", "sl", "sa", "sc", "cb", "ag", "kg",
)
#: Formas societarias también como TOKEN suelto (para nombres con espacios).
_CLIENT_LEGAL_TOKENS = frozenset({
    "sl", "slu", "sa", "sau", "sas", "sc", "scp", "cb", "srl", "sll", "sccl",
    "bvba", "eurl", "gmbh", "ltd", "llc", "bv", "nv", "spa", "ag", "kg",
    "sarl", "coop", "scoop", "sociedad", "limitada",
})


def extract_order_number(value: Any) -> str | None:
    """Número «desnudo» y canónico de una referencia: quita el prefijo de
    tienda (`BOPRIN-99866` → `99866`), la serie de una factura (`5-260695` →
    `260695`) y el `.0` que Excel añade a los numéricos (`99866.0` → `99866`);
    sin ceros a la izquierda (`000001` → `1`). None si no hay dígitos.

    Se queda con el ÚLTIMO grupo de dígitos: cubre prefijos alfabéticos
    (`BOPRIN-`) y numéricos (`5-`) sin confundirlos con el número real."""
    s = _TRAILING_DECIMAL.sub("", str(value or "").strip())
    groups = _DIGIT_RUN.findall(s)
    if not groups:
        return None
    return str(int(groups[-1]))


def numbers_match(a: Any, b: Any) -> bool:
    """Coincidencia ESTRICTA del número completo (no «contiene»): `5742` no
    casa con `15742` ni con `57420`."""
    na, nb = extract_order_number(a), extract_order_number(b)
    return na is not None and na == nb


def normalize_client(value: Any) -> str:
    """Nombre de cliente comparable: sin tildes, minúsculas, sin signos ni
    espacios, y sin la forma societaria final. `DUPLICODER, S.L.` y
    `DUPLICODER` → `duplicoder`."""
    s = unicodedata.normalize("NFKD", str(value or ""))
    s = "".join(c for c in s if not unicodedata.combining(c)).casefold()
    s = re.sub(r"[^a-z0-9]", "", s)
    for suffix in _CLIENT_LEGAL_SUFFIXES:
        if s.endswith(suffix) and len(s) > len(suffix) + 2:
            return s[: -len(suffix)]
    return s


def _client_tokens(value: Any) -> list[str]:
    """Tokens del nombre, sin tildes, minúsculas, sin formas societarias."""
    s = unicodedata.normalize("NFKD", str(value or ""))
    s = "".join(c for c in s if not unicodedata.combining(c)).casefold()
    return [t for t in re.split(r"[^a-z0-9]+", s) if t and t not in _CLIENT_LEGAL_TOKENS]


def clients_match(a: Any, b: Any) -> bool:
    """¿Dos nombres de cliente son el mismo? Tolerante a tildes, mayúsculas,
    formas societarias y a que uno sea más rico que el otro (`DM Document
    Materiel SA` ↔ `DOCUMENT MATERIEL SA`). Es un dato de CONFIRMACIÓN
    secundaria (el número ya casó), así que se admite contención."""
    na, nb = normalize_client(a), normalize_client(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    shorter, longer = (na, nb) if len(na) <= len(nb) else (nb, na)
    if len(shorter) >= 6 and shorter in longer:
        return True
    # Solapamiento de tokens significativos (ambos sentidos, ≥1 token ≥4).
    ta, tb = set(_client_tokens(a)), set(_client_tokens(b))
    shared = {t for t in (ta & tb) if len(t) >= 4}
    return bool(shared) and (shared == {t for t in ta if len(t) >= 4}
                             or shared == {t for t in tb if len(t) >= 4})


def compose_client(company: Any, person: Any) -> str | None:
    """ERP-F6-fix4 — formato de Bart `Empresa (Persona)`. Con ambos datos se
    escriben los dos; con uno solo, ese; sin ninguno, None."""
    emp = str(company or "").strip()
    per = str(person or "").strip()
    if emp and per and emp.casefold() != per.casefold():
        return f"{emp} ({per})"
    return emp or per or None


def split_client_parts(value: Any) -> list[str]:
    """Partes de una celda de cliente: `Empresa (Persona)` → [Empresa, Persona].
    Sin paréntesis, la celda entera es la única parte."""
    text = str(value or "").strip()
    if not text:
        return []
    m = re.match(r"^(.*?)\s*\(([^)]*)\)\s*$", text)
    if m:
        return [p.strip() for p in (m.group(1), m.group(2)) if p.strip()]
    return [text]


_SHEET_DATE_FORMATS = ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d-%m-%Y")


#: Epoch del serial de fechas de Excel (día 0 = 1899-12-30 por el bug de 1900).
_EXCEL_EPOCH = date(1899, 12, 30)


def parse_sheet_date(value: Any) -> date | None:
    """Fecha de una celda de la hoja → date, o None si no se reconoce. Tolera
    `dd/mm/aaaa`, `d/m/aaaa`, `dd/mm/aa`, ISO y el SERIAL numérico de Excel
    (p. ej. `46235`), que openpyxl a veces entrega en celdas de fecha."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = _TRAILING_DECIMAL.sub("", str(value or "").strip())
    if not s:
        return None
    for fmt in _SHEET_DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()  # noqa: DTZ007 — fecha civil
        except ValueError:
            continue
    # Serial de Excel: un entero «grande» (evita confundir un día suelto).
    if s.isdigit() and 20000 <= int(s) <= 90000:
        from datetime import timedelta  # noqa: PLC0415

        return _EXCEL_EPOCH + timedelta(days=int(s))
    return None


def same_day(a: Any, b: Any) -> bool:
    """¿Dos celdas de fecha son el MISMO día, ignorando el formato? (Parte B)."""
    da, db = parse_sheet_date(a), parse_sheet_date(b)
    return da is not None and da == db


#: ERP-F6-fix4 — mínimo de dígitos para casar por número desnudo. Un `1`
#: (de `MANUAL-000001`) casaría con cualquier cosa; se exige un número «de
#: verdad» de al menos 4 dígitos.
MIN_MATCH_DIGITS = 4


def match_number(value: Any) -> str | None:
    """Número canónico para EMPAREJAR: como `extract_order_number` pero solo si
    tiene al menos `MIN_MATCH_DIGITS` dígitos y NO es una referencia `MANUAL-`
    (los manuales no participan en la búsqueda por número)."""
    if str(value or "").strip().upper().startswith("MANUAL-"):
        return None
    num = extract_order_number(value)
    if num is None or len(num) < MIN_MATCH_DIGITS:
        return None
    return num


def order_match_numbers(row: dict[str, Any]) -> set[str]:
    """Números por los que un pedido puede localizarse en la hoja: el del
    pedido web/albarán propio y, si se conoce, el de albarán de FACTUSOL. Se
    excluyen los manuales y los números de menos de 4 dígitos (ERP-F6-fix4)."""
    out: set[str] = set()
    for candidate in (row.get("order_number"), row.get("albaran_pedido"),
                      row.get("albaran_number")):
        num = match_number(candidate)
        if num:
            out.add(num)
    return out


def reference_for_row(row: dict[str, Any], *, prefer_albaran: bool = True) -> str:
    """Valor a escribir en «Albarán / Núm Pedido WEb»: el número DESNUDO, en el
    formato de Bart. Si `prefer_albaran` y el pedido tiene número de albarán, se
    escribe ese (sus filas antiguas usan el albarán); si no, el del pedido web.
    """
    if prefer_albaran and row.get("albaran_number"):
        base = row["albaran_number"]
    else:
        base = row.get("order_number") or row.get("albaran_pedido")
    return extract_order_number(base) or str(base or "").strip()

#: Abreviaturas de empresa por serie que usa Bart en su hoja. ERP-F6-fix3: son
#: CONFIGURABLES en /erp/settings (junto a series_names) y se precargan solo
#: con las confirmadas — la de la serie 4 (Lambert) NO se inventa: queda vacía
#: hasta que Bart la confirme.
DEFAULT_SERIES_ABBREVIATIONS: dict[int, str] = {1: "BO", 2: "MQ", 5: "ST"}


def series_abbreviations_config(raw: Any) -> dict[int, str]:
    """`{serie → abreviatura}` configurado, partiendo de las confirmadas. Solo
    se conservan las no vacías."""
    out = dict(DEFAULT_SERIES_ABBREVIATIONS)
    if isinstance(raw, dict):
        for key, value in raw.items():
            try:
                serie = int(str(key).strip())
            except (TypeError, ValueError):
                continue
            text = str(value or "").strip()
            if text:
                out[serie] = text
            else:
                out.pop(serie, None)
    return out


def normalize_abbr(value: Any) -> str:
    """Forma comparable de una abreviatura de empresa: mayúsculas, sin tildes.
    Así `st`, `ST` y `St` (o `BOM`/`bom`) no se tratan como distintas al
    comparar para detectar duplicados."""
    s = unicodedata.normalize("NFKD", str(value or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().upper()


#: ERP-F6-fix4 — variantes históricas escritas a mano en la hoja de Bart que se
#: aceptan como equivalentes a la abreviatura canónica de cada serie. Al
#: comparar valen todas; al ESCRIBIR se usa siempre la canónica, y una variante
#: en la hoja no es conflicto ni se reescribe.
DEFAULT_SERIES_ABBR_VARIANTS: dict[int, list[str]] = {
    1: ["BOM", "BOMEDIA"],
    5: ["STR", "STREAMTEC"],
}


def abbr_variants_config(raw: Any) -> dict[int, list[str]]:
    """`{serie → [variantes]}`, partiendo de las conocidas. Se pueden añadir más
    en /erp/settings; una lista vacía no borra las por defecto de esa serie."""
    out = {k: list(v) for k, v in DEFAULT_SERIES_ABBR_VARIANTS.items()}
    if isinstance(raw, dict):
        for key, value in raw.items():
            try:
                serie = int(str(key).strip())
            except (TypeError, ValueError):
                continue
            extra = [str(x).strip() for x in value if str(x).strip()] \
                if isinstance(value, list) else []
            if extra:
                out.setdefault(serie, [])
                for x in extra:
                    if x not in out[serie]:
                        out[serie].append(x)
    return out


def abbr_alias_to_canonical(
    abbreviations: dict[int, str], variants: dict[int, list[str]],
) -> dict[str, str]:
    """`{forma normalizada → abreviatura canónica}` para aceptar variantes al
    comparar la columna Empresa. `STR`→`ST`, `BOMEDIA`→`BO`…"""
    out: dict[str, str] = {}
    for serie, canonical in abbreviations.items():
        canon = canonical.strip()
        if not canon:
            continue
        out[normalize_abbr(canon)] = canon
        for variant in variants.get(serie, []):
            out.setdefault(normalize_abbr(variant), canon)
    return out


def _serie_of_invoice(invoice_number: Any) -> int | None:
    """Serie del número de factura de FACTUSOL (`5-260123` → 5)."""
    num = str(invoice_number or "")
    head = num.split("-", 1)[0].strip() if "-" in num else ""
    return int(head) if head.isdigit() else None


def store_serie_for(
    *, store_slug: str | None, store_id: str | None, source: str | None,
    by_source: dict[str, Any],
) -> int | None:
    """Serie configurada para la TIENDA (por slug; o por store_id; o el valor
    global de WooCommerce como respaldo). None si no hay ninguna."""
    for key in (store_slug, store_id, source):
        if not key:
            continue
        raw = by_source.get(key)
        if raw is not None and str(raw).strip().isdigit():
            return int(str(raw).strip())
    return None


def resolve_empresa_serie(
    *,
    invoice_number: Any,
    store_slug: str | None,
    store_id: str | None,
    source: str | None,
    by_source: dict[str, Any],
) -> int | None:
    """Serie (empresa emisora) para la columna Empresa del seguimiento
    (ERP-F6-fix3), por ORDEN de prioridad:
      1. la serie de la FACTURA, si ya está facturado (dato real, manda);
      2. la serie configurada para su TIENDA (por slug; o por store_id, o el
         valor global de WooCommerce como respaldo);
      3. None → la celda queda VACÍA (nunca un valor por defecto)."""
    inv = _serie_of_invoice(invoice_number)
    if inv is not None:
        return inv
    return store_serie_for(
        store_slug=store_slug, store_id=store_id, source=source, by_source=by_source,
    )


def serie_of_invoice(invoice_number: Any) -> int | None:
    """Serie explícita de un número de factura (`1-260737` → 1). Público para
    la resolución de empresa de una fila de la hoja (ERP-F6-fix5)."""
    return _serie_of_invoice(invoice_number)

#: Orígenes del envío vistos en el Excel real (OFI-TER-SAT). Valor INICIAL de
#: la lista configurable de /erp/settings; se pueden añadir más.
DEFAULT_SHIPPING_ORIGINS: list[str] = [
    "SAT", "OFI", "TER", "directo", "INSITU", "ADR", "MAD",
]

_INVOICED_STATUSES = {
    InvoiceStatus.GENERATED,
    InvoiceStatus.INVOICED_BY_ERP,
    InvoiceStatus.ALREADY_INVOICED_EXTERNALLY,
}
_SHIPPED_STATUSES = {
    TransportStatus.IN_TRANSIT,
    TransportStatus.DELIVERED,
    TransportStatus.ALREADY_SHIPPED_EXTERNALLY,
}


def shipping_origins_config(raw: Any) -> list[str]:
    """Lista configurada de orígenes del envío; defaults si no hay nada."""
    out: list[str] = []
    if isinstance(raw, list):
        seen: set[str] = set()
        for v in raw:
            text = str(v or "").strip()
            if text and text.casefold() not in seen:
                seen.add(text.casefold())
                out.append(text)
    return out or list(DEFAULT_SHIPPING_ORIGINS)


#: Reason con el que la importación/externalización ESTAMPA las transiciones
#: (external_processing.py). Un valor así NO es un hecho real.
_IMPORT_STAMP_REASON = "Procesado externamente"
_EXTERNAL_TERMINAL_STATES = {
    "already_completed_externally",
    "already_shipped_externally",
    "already_invoiced_externally",
}


def _real_event_date(order: Order, domain: str, to_statuses: set[str]) -> datetime | None:
    """ERP-F6-fix4 — fecha de un HECHO REAL (embalado, envío, factura enviada),
    NUNCA la fecha con que la importación del 4-ago estampó el estado.

    Se descartan las transiciones que son marca de importación: las de estados
    terminales «already_*_externally», las que llevan el reason de
    externalización, y las estampadas el MISMO día en que se importó el pedido
    (created_at) — el patrón del bug: un único día repetido en los tres campos.
    """
    import_day = order.created_at.date() if order.created_at else None
    for h in order.status_history:
        if getattr(h.domain, "value", h.domain) != domain:
            continue
        if h.to_status not in to_statuses:
            continue
        if h.to_status in _EXTERNAL_TERMINAL_STATES:
            continue
        if (h.reason or "") == _IMPORT_STAMP_REASON:
            continue
        if import_day and h.changed_at and h.changed_at.date() == import_day:
            continue  # estampado el día de la importación: no es un hecho
        return h.changed_at
    return None


def _iso_date(value: datetime | None) -> str | None:
    return value.date().isoformat() if value else None


def _estado(order: Order) -> str:
    """pendiente / enviado / facturado — el filtro de estado de la vista."""
    if order.invoice_status in _INVOICED_STATUSES or order.factusol_invoice_number:
        return "facturado"
    if order.transport_status in _SHIPPED_STATUSES:
        return "enviado"
    return "pendiente"


def _en_curso(order: Order, estado: str) -> bool:
    """La sección de arriba del Excel: lo que Bart mira a diario. Un pedido
    sale de «en curso» cuando está entregado Y facturado, o cuando se marcó
    como gestionado fuera del sistema."""
    if order.externally_processed_at is not None:
        return False
    return not (
        estado == "facturado" and order.transport_status == TransportStatus.DELIVERED
    )


#: ERP-Woo — estados de WooCommerce que SIEMPRE sacan el pedido del seguimiento
#: (`trash`/`deleted` = pedido enviado a la papelera en la tienda).
_WOO_STATUS_ALWAYS_OUT = {"cancelled", "failed", "trash", "deleted"}


def _is_fulfilled(order: Order, estado: str) -> bool:
    """¿El pedido se CUMPLIÓ (se envió o se facturó)? Señales de BoHub que se
    usan (informe): enviado (transporte in_transit / delivered /
    already_shipped_externally), facturado (factura FACTUSOL emitida o
    invoice_status facturado), o con número de tracking. Sirve para la regla del
    reembolso: solo se ocultan los reembolsos de pedidos que NUNCA se cumplieron.
    """
    if estado in ("enviado", "facturado"):
        return True
    if order.transport_status in _SHIPPED_STATUSES:
        return True
    return bool(order.tracking_number)


def visibility_for_status(
    order: Order, estado: str, woo_status: str | None,
) -> tuple[bool, str | None, bool]:
    """(oculto_por_estado, motivo, reembolsado_visible) para un estado de
    WooCommerce dado, según la regla de Bart:
      - cancelled / failed (y trash) → FUERA, siempre.
      - refunded sin cumplir (ni enviado ni facturado) → FUERA (como cancelado).
      - refunded ya cumplido → se QUEDA, marcado «reembolsado».
      - pending / processing / completed / on-hold / None → normal, se queda.
    Es INDEPENDIENTE de la exclusión manual de F6-fix7 (que va por su flag).
    La reconciliación la usa con el estado RECIÉN consultado en la tienda."""
    st = (woo_status or "").strip().lower()
    if st in _WOO_STATUS_ALWAYS_OUT:
        return True, st, False
    if st == "refunded":
        if _is_fulfilled(order, estado):
            return False, "refunded", True
        return True, "refunded_sin_cumplir", False
    return False, None, False


def woo_status_visibility(order: Order, estado: str) -> tuple[bool, str | None, bool]:
    """Visibilidad según el estado de WooCommerce ALMACENADO en el pedido."""
    return visibility_for_status(order, estado, order.woo_status)


def build_rows(
    session: Session,
    *,
    customer_names: dict[str, dict[str, str | None]],
) -> list[dict[str, Any]]:
    """Todas las filas del seguimiento (sin filtrar). Carga pedidos, líneas e
    historial en 3 queries y los transportistas en 1 — sin N+1."""
    from app.erp.api.factusol import FALLBACK_SERIES_NAMES  # noqa: PLC0415
    from app.erp.models import ErpDriveSyncRow  # noqa: PLC0415
    from app.integrations.factusol.service import series_config  # noqa: PLC0415
    from app.models.integration_settings import IntegrationAccount  # noqa: PLC0415

    orders = list(session.scalars(
        select(Order).options(
            selectinload(Order.lines), selectinload(Order.status_history)
        )
    ))
    carriers = {c.id: c.name for c in session.scalars(select(Carrier))}
    # ERP-F6-fix7 — «escrito en Drive» = tiene fila sincronizada con la hoja.
    # Es DISTINTO de «excluido»: se usa para separar en la vista «pendiente de
    # escribir» de lo ya escrito, sin sacar nada de la vista diaria.
    written_ids = set(session.scalars(
        select(ErpDriveSyncRow.order_id).where(ErpDriveSyncRow.synced_at.isnot(None))
    ))
    series_cfg = series_config(session)
    by_source = series_cfg.get("by_source") if isinstance(series_cfg.get("by_source"), dict) else {}
    series_names = {
        int(k): str(v).strip()
        for k, v in (series_cfg.get("names") or {}).items()
        if str(k).strip().isdigit() and str(v).strip()
    }
    abbreviations = series_abbreviations_config(series_cfg.get("series_abbreviations"))
    # ERP-F6-fix3: slug de la tienda (IntegrationAccount.account_id) de cada
    # pedido, en 1 query — la config de serie por tienda va por slug.
    store_ids = {o.store_id for o in orders if o.store_id}
    store_slugs: dict[str, str] = {}
    if store_ids:
        store_slugs = {
            a.id: a.account_id
            for a in session.scalars(
                select(IntegrationAccount).where(IntegrationAccount.id.in_(store_ids))
            )
        }
    rows: list[dict[str, Any]] = []
    for o in orders:
        names = customer_names.get(o.id) or {}
        cliente_company = names.get("company_name")
        cliente_person = names.get("contact_name")
        # ERP-F6-fix4: formato de Bart «Empresa (Persona)» cuando hay ambos.
        cliente = compose_client(cliente_company, cliente_person)
        source = getattr(o.external_source, "value", o.external_source)
        # ERP-F6-fix5: serie de FACTURA (real) y serie de TIENDA (deducida) por
        # separado — el sync las trata distinto en filas ya existentes.
        serie_invoice = _serie_of_invoice(o.factusol_invoice_number)
        serie_store = store_serie_for(
            store_slug=store_slugs.get(o.store_id) if o.store_id else None,
            store_id=o.store_id, source=source, by_source=by_source,
        )
        serie = serie_invoice if serie_invoice is not None else serie_store
        estado = _estado(o)
        # ERP-Woo — regla de cancelado/reembolsado/fallido (independiente de la
        # exclusión manual de F6-fix7).
        oculto_estado, estado_woo_motivo, reembolsado = woo_status_visibility(o, estado)
        productos = " · ".join(
            f"{float(line.quantity):g}× {line.description or line.product_sku}"
            for line in o.lines
        )
        rows.append({
            "id": o.id,
            "order_number": o.order_number,
            "serie": serie,
            # ERP-F6-fix5: serie de factura registrada en BoHub (real) y serie
            # deducida de la tienda, para la resolución de Empresa del sync.
            "serie_invoice": serie_invoice,
            "serie_store": serie_store,
            "empresa": (
                series_names.get(serie) or FALLBACK_SERIES_NAMES.get(serie)
                or (f"Serie {serie}" if serie else None)
            ),
            # ERP-F6-fix3: la abreviatura que usa Bart en su hoja (BO/MQ/ST…).
            # Vacía si no hay serie o esa serie no tiene abreviatura configurada
            # — nunca un valor por defecto.
            "empresa_corta": abbreviations.get(serie, "") if serie else "",
            "fecha": _iso_date(o.placed_at or o.created_at),
            "cliente": cliente,
            # ERP-F6-fix4: partes por separado para confirmar por empresa O
            # persona (basta con que case cualquiera).
            "cliente_company": cliente_company,
            "cliente_person": cliente_person,
            # Sin campo de agente en el pedido (fuera del alcance de F6): los
            # pedidos web son «WEB» (3.605 de 7.743 en el Excel); el resto
            # queda vacío hasta que Bart decida añadir el agente al modelo.
            "vendedor": "WEB" if o.external_source == OrderSource.WOOCOMMERCE else "",
            "origen": o.shipping_origin,
            "transportista": carriers.get(o.carrier_id) if o.carrier_id else None,
            # ERP-F6-fix4: SOLO hechos reales; nunca la fecha estampada en la
            # importación (esas quedan vacías, no engañan ni en vista ni hoja).
            "preparado": _iso_date(_real_event_date(o, "preparation", {"packed"})),
            "recogido": _iso_date(_real_event_date(
                o, "transport", {"in_transit", "delivered", "label_created"},
            )),
            "fecha_envio_factura": _iso_date(_real_event_date(
                o, "invoice", {"generated", "invoiced_by_erp"},
            )),
            "productos": productos,
            "proforma": (
                o.external_id
                if o.external_source == OrderSource.FACTUSOL_PROFORMA else None
            ),
            "albaran_pedido": o.order_number,
            # ERP-F6-fix2: nº de albarán de FACTUSOL, si algún día se conoce.
            # Hoy BoHub no lo rastrea (queda None → se usa el nº de pedido web).
            "albaran_number": None,
            "factura": o.factusol_invoice_number,
            "tracking": o.tracking_number,
            "num_serie": o.serial_number,
            "whiterip": o.whiterip_license,
            "orden": o.notes,
            "estado": estado,
            "en_curso": _en_curso(o, estado),
            # ERP-F6-fix7 — excluido del seguimiento (reversible; no toca el
            # pedido ni FACTUSOL). Quién/cuándo/motivo para la trazabilidad.
            "excluido": o.seguimiento_excluded_at is not None,
            "excluido_en": _iso_date(o.seguimiento_excluded_at),
            "excluido_por": o.seguimiento_excluded_by_user_id,
            "excluido_motivo": o.seguimiento_excluded_reason,
            # ERP-F6-fix7 — «escrito en Drive» vs «pendiente de escribir». La
            # vista diaria enseña ambos; el pendiente es lo que aún no está en
            # la hoja (y no está excluido).
            "escrito_drive": o.id in written_ids,
            "pendiente_escribir": (
                _en_curso(o, estado)
                and o.seguimiento_excluded_at is None
                and not oculto_estado
                and o.id not in written_ids
            ),
            # ERP-Woo — estado de WooCommerce y su efecto en el seguimiento.
            "woo_status": o.woo_status,
            "oculto_por_estado": oculto_estado,
            "estado_woo_motivo": estado_woo_motivo,
            "reembolsado": reembolsado,
        })
    return rows


#: Claves de fila por las que se puede ordenar (E3-A-fix1: misma idea).
SORT_KEYS = {
    "fecha", "cliente", "empresa", "vendedor", "transportista", "origen",
    "estado", "factura", "albaran_pedido",
}
_SEARCH_FIELDS = (
    "cliente", "albaran_pedido", "proforma", "factura", "tracking", "num_serie",
)


def filter_rows(
    rows: list[dict[str, Any]],
    *,
    serie: int | None = None,
    vendedor: str | None = None,
    transportista: str | None = None,
    origen: str | None = None,
    desde: date | None = None,
    hasta: date | None = None,
    estado: str | None = None,
    q: str | None = None,
    en_curso: bool = True,
    ver_excluidos: bool = False,
    ver_ocultos_estado: bool = False,
    pendiente_escribir: bool | None = None,
    sort: str = "fecha",
    direction: str = "desc",
) -> list[dict[str, Any]]:
    """Filtros + búsqueda + orden de la vista, en Python (mismo patrón que el
    explorador de documentos). Por defecto: solo pedidos EN CURSO — la parte
    de arriba del Excel, lo que Bart mira a diario.

    ERP-F6-fix7 — los pedidos EXCLUIDOS a mano quedan FUERA por defecto. Con
    `ver_excluidos` se listan SOLO los excluidos.
    ERP-Woo — los OCULTOS POR ESTADO (cancelado/fallido/reembolso no cumplido)
    también quedan FUERA por defecto; con `ver_ocultos_estado` se listan SOLO
    esos (para revisarlos). Son cosas DISTINTAS y con vistas distintas.
    `pendiente_escribir=True` deja solo los que aún no están en la hoja de
    Drive."""
    out = rows
    if ver_excluidos:
        # Vista de excluidos MANUALMENTE: solo ellos, sin el filtro de «en curso».
        return _sort_rows([r for r in out if r["excluido"]], sort, direction)
    if ver_ocultos_estado:
        # Vista de ocultados por ESTADO de WooCommerce: solo esos.
        return _sort_rows([r for r in out if r["oculto_por_estado"]], sort, direction)
    # En cualquier otra vista, ni los excluidos ni los ocultados por estado
    # aparecen (ni cuentan como pendientes).
    out = [r for r in out if not r["excluido"] and not r["oculto_por_estado"]]
    if pendiente_escribir:
        out = [r for r in out if r["pendiente_escribir"]]
    if en_curso:
        out = [r for r in out if r["en_curso"]]
    if serie is not None:
        out = [r for r in out if r["serie"] == serie]
    if vendedor:
        out = [r for r in out if (r["vendedor"] or "").casefold() == vendedor.casefold()]
    if transportista:
        out = [
            r for r in out
            if (r["transportista"] or "").casefold() == transportista.casefold()
        ]
    if origen:
        out = [r for r in out if (r["origen"] or "").casefold() == origen.casefold()]
    if desde:
        out = [r for r in out if r["fecha"] and date.fromisoformat(r["fecha"]) >= desde]
    if hasta:
        out = [r for r in out if r["fecha"] and date.fromisoformat(r["fecha"]) <= hasta]
    if estado:
        out = [r for r in out if r["estado"] == estado]
    if q:
        needle = q.casefold().strip()
        out = [
            r for r in out
            if any(needle in str(r[f] or "").casefold() for f in _SEARCH_FIELDS)
        ]
    return _sort_rows(out, sort, direction)


def _sort_rows(
    out: list[dict[str, Any]], sort: str, direction: str,
) -> list[dict[str, Any]]:
    key = sort if sort in SORT_KEYS else "fecha"
    reverse = direction != "asc"
    out = sorted(
        out,
        key=lambda r: (r[key] is None, str(r[key] or "").casefold()),
        reverse=reverse,
    )
    if reverse:
        # los None siempre al final, también en descendente
        out = [r for r in out if r[key] is not None] + [r for r in out if r[key] is None]
    return out


# --- forma «hoja» (Drive + exportación) ----------------------------------------


def _sheet_date(iso: str | None) -> str:
    if not iso:
        return ""
    d = date.fromisoformat(iso)
    return f"{d.day}/{d.month}/{d.year}"


def row_to_sheet_values(row: dict[str, Any], *, include_orden: bool = False) -> list[str]:
    """Fila de la vista → los 17 valores en el ORDEN del Excel de Bart. En la
    hoja de Drive «Orden» nunca se escribe (columna manual); en la exportación
    local sí va (con las observaciones del pedido)."""
    return [
        row.get("empresa_corta") or "",
        _sheet_date(row.get("fecha")),
        row.get("cliente") or "",
        row.get("vendedor") or "",
        row.get("origen") or "",
        row.get("transportista") or "",
        _sheet_date(row.get("preparado")),
        _sheet_date(row.get("recogido")),
        _sheet_date(row.get("fecha_envio_factura")),
        (row.get("productos") or "")[:300],
        row.get("proforma") or "",
        row.get("albaran_pedido") or "",
        row.get("factura") or "",
        row.get("tracking") or "",
        row.get("num_serie") or "",
        row.get("whiterip") or "",
        (row.get("orden") or "")[:300] if include_orden else "",
    ]


def export_xlsx(rows: list[dict[str, Any]]) -> bytes:
    """Exportación local a .xlsx: mismas columnas y orden que el Excel de
    Bart, respetando los filtros ya aplicados. Funciona sin Drive."""
    import io  # noqa: PLC0415

    from openpyxl import Workbook  # noqa: PLC0415

    wb = Workbook()
    ws = wb.active
    ws.title = "Seguimiento"
    ws.append(SEGUIMIENTO_COLUMNS)
    for row in rows:
        ws.append(row_to_sheet_values(row, include_orden=True))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def snapshot_dump(values: list[str]) -> str:
    return json.dumps(values, ensure_ascii=False)


def snapshot_load(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return [str(v) for v in data] if isinstance(data, list) else None
