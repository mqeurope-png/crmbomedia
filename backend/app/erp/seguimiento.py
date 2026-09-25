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

from app.erp import woo_status as woo
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
            parsed = datetime.strptime(s, fmt).date()  # noqa: DTZ007 — fecha civil
        except ValueError:
            continue
        # `%Y` acepta un año de 3 dígitos («27/07/202», un dígito de menos):
        # eso es una fecha ROTA, no el año 202. No se inventa: None, y la
        # celda se queda como texto.
        if not (1990 <= parsed.year <= 2100):
            return None
        return parsed
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
    # «Marcar completado» (decisión de Bart): estado FINAL → sale de «en curso».
    if order.completed_at is not None:
        return False
    # Estado propio «Reembolsado»: se queda a la vista (con su pastilla) aunque
    # esté entregado y facturado — un reembolso deja trabajo por delante (el
    # abono) y es justo lo que hay que ver. Se cierra con «Marcar completado».
    if woo.is_refunded(order):
        return True
    return not (
        estado == "facturado" and order.transport_status == TransportStatus.DELIVERED
    )


def visibility_for_status(woo_status: str | None) -> tuple[bool, str | None, bool]:
    """(oculto_por_estado, motivo, reembolsado) de un pedido WEB según el estado
    de la tienda.

    La puerta de entrada web es HABER PASADO POR CAJA: `processing` (pagado /
    en preparación), `completed` (servido) y `refunded` (pagado y devuelto
    después) se quedan; `pending`, `on-hold`, `cancelled`, `failed`,
    `draft`/`checkout-draft` y `trash` quedan OCULTOS POR ESTADO, con su motivo
    y a un clic en «Ver ocultos por estado» (con «Reincluir» si hay que
    forzarlo). Un `pending` u `on-hold` es un carrito que todavía no ha entrado
    en el flujo real: no es trabajo de nadie.

    `refunded` NO oculta: es el estado propio «Reembolsado» de BoHub. Se
    devuelve marcado para que pantalla, Excel y Drive lo enseñen como tal y no
    se confunda ni con un pedido vivo ni con un anulado.

    Un pedido SIN estado (NULL) se QUEDA: no se conoce, y ocultarlo (#461)
    se llevó por delante ~90 pedidos legítimos importados antes de que
    existiera el campo. Solo oculta un estado EXPLÍCITO de la tienda (o el
    `not_found` que pone la reconciliación cuando la tienda ya no lo tiene).
    «Poner al día estados Woo» recorre también los NULL y les pone su estado
    real, así que con el tiempo dejan de serlo.

    Los dos lados de la comparación pasan por `woo.normalize` (guion, guion
    bajo, prefijo `wc-` y mayúsculas son lo mismo): el valor guardado es el
    crudo de la REST API (`on-hold`, `cancelled`…) y aquí nunca se compara tal
    cual. Es INDEPENDIENTE de la exclusión manual de F6-fix7 (que va por su
    flag). La reconciliación la usa con el estado RECIÉN consultado."""
    st = woo.normalize(woo_status)
    if st == woo.REFUNDED:
        return False, None, True
    if not st or st in woo.WEB_VISIBLE_STATUSES:
        return False, None, False
    return True, st, False


def woo_status_visibility(order: Order) -> tuple[bool, str | None, bool]:
    """Visibilidad según el estado de WooCommerce ALMACENADO en el pedido."""
    return visibility_for_status(order.woo_status)


def seguimiento_visibility(order: Order) -> tuple[bool, str | None, bool]:
    """(oculto, motivo, reembolsado) para el SEGUIMIENTO, que es la lista de
    pedidos VIVOS: fuera lo anulado, lo que la tienda no ha llegado a procesar
    y lo que todavía no ha entrado al flujo.

    Se resuelve con lo que ya cuenta el pedido: la anulación por `cancelled_at`,
    la aprobación por `workflow.is_approved` (que cuenta también los pedidos ya
    avanzados y las muestras, que entran directas a la Cola SAT) y el estado de
    la tienda por `woo_status_visibility`. El «forzar en seguimiento» es otra
    cosa y va aparte (lo resuelve `build_rows`): esto dice si el pedido queda
    oculto POR ESTADO, y el forzado decide si se enseña igualmente.

    Tres asimetrías, a propósito:

    - El REEMBOLSO manda sobre la anulación. Un web `refunded` se VE aunque
      esté anulado en BoHub — la auto-anulación por reembolso es justo lo que
      lo sacaba de la vista. Es SOLO visibilidad: el pedido sigue anulado para
      sus acciones, no se reactiva nada.
    - La aprobación NO se le exige a un pedido WEB: para la tienda, la puerta
      es el estado (`processing` en adelante), no que alguien lo apruebe.
    - La puerta de estado NO se le aplica a un pedido MANUAL: no tiene
      `woo_status`, y su regla sigue siendo `is_approved` (un manual aprobado
      se ve aunque no haya pagado)."""
    from app.erp.workflow import is_approved, is_web_order  # noqa: PLC0415

    web = is_web_order(order)
    oculto, motivo, reembolsado = (
        woo_status_visibility(order) if web else (False, None, False)
    )
    if reembolsado:
        return False, None, True
    if order.cancelled_at is not None:
        return True, "anulado", False
    if oculto:
        return True, motivo, False
    if not web and not is_approved(order):
        return True, "sin_aprobar", False
    return False, None, False


# --- rediseño 2026: hoja simplificada, ordenada por Situación ------------------
#
# Una fila por pedido; el estado en una columna (Situación), NO en la posición.
# La Situación reutiliza la clasificación de colas de la línea de vida
# (`workflow.py`), la misma que la bandeja — no decide nada nuevo, solo la
# presenta. Las incidencias van a una pestaña aparte del Excel, subconjunto
# EXACTO de los pedidos con Situación = Incidencia.

#: Situación propia (NO es una cola de la bandeja): pedido web reembolsado en
#: la tienda. Ni «Listo» ni «Anulado» — se ve por lo que es.
SITUACION_REEMBOLSADO = "reembolsado"

#: Etiqueta de la Situación en la hoja. Singular «Incidencia» (una fila por
#: pedido), a diferencia de la cola «Incidencias» de la bandeja.
SITUACION_LABELS: dict[str, str] = {
    "incidencias": "Incidencia",
    "por_revisar": "Por revisar",
    "por_facturar": "Por facturar",
    "por_cobrar": "Por cobrar",
    "por_enviar": "Por enviar",
    "listo": "Listo",
    SITUACION_REEMBOLSADO: "Reembolsado",
}
#: Prioridad de orden: lo urgente sube solo (Incidencia arriba, Listo abajo).
#: «Reembolsado» va al final: es informativo, no trabajo pendiente.
SITUACION_ORDER: dict[str, int] = {
    "incidencias": 0, "por_revisar": 1, "por_facturar": 2,
    "por_cobrar": 3, "por_enviar": 4, "listo": 5,
    SITUACION_REEMBOLSADO: 6,
}
#: Tono de color de la celda Situación (letras del sistema de diseño `--st-*`):
#: rojo(r) · ámbar(a) · azul(b) · teal(t) · verde(g) · neutro(n).
SITUACION_TONE: dict[str, str] = {
    "incidencias": "r", "por_revisar": "a", "por_facturar": "b",
    "por_cobrar": "b", "por_enviar": "t", "listo": "g",
    SITUACION_REEMBOLSADO: "n",
}
#: Relleno/tinta de la celda Situación en el Excel (hex del sistema de diseño,
#: sin `#`).
SITUACION_FILL: dict[str, tuple[str, str]] = {
    "incidencias": ("FDEAEA", "B42318"),
    "por_revisar": ("FDF1DD", "8A5C00"),
    "por_facturar": ("E8F0FF", "2451C7"),
    "por_cobrar": ("E8F0FF", "2451C7"),
    "por_enviar": ("E6F5F3", "0F766E"),
    "listo": ("E7F6EC", "1F7A45"),
    SITUACION_REEMBOLSADO: ("EEF0F4", "5B6472"),
}
#: Relleno neutro para una Situación desconocida.
_SITUACION_FILL_FALLBACK = ("EEF0F4", "5B6472")

#: Estado de preparación (SAT) → etiqueta corta para la columna Preparación.
PREPARACION_LABELS: dict[str, str] = {
    "pending_review": "Pendiente",
    "in_queue": "En cola",
    "preparing": "Preparando",
    "packed": "Listo",
    "blocked": "Bloqueado",
    "already_completed_externally": "Hecho (externo)",
}
#: Estado de transporte → etiqueta corta para la columna Envío.
ENVIO_LABELS: dict[str, str] = {
    "not_shipped": "Sin enviar",
    "label_created": "Etiqueta creada",
    "in_transit": "En tránsito",
    "delivered": "Entregado",
    "incident": "Incidencia",
    "returned": "Devuelto",
    "already_shipped_externally": "Enviado (externo)",
}
#: Tipo de excepción → etiqueta legible (pestaña Incidencias).
EXCEPTION_TYPE_LABELS: dict[str, str] = {
    "stock_shortage": "Falta de stock",
    "material_defective": "Material defectuoso",
    "sat_issue": "Incidencia en SAT",
    "size_exceeds_carrier": "Excede el tamaño del transportista",
    "blocked_by_customer_request": "Bloqueado por el cliente",
    "carrier_incident": "Incidencia de transporte",
    "returned_by_transport": "Devuelto por transporte",
    "factusol_write_failed": "Fallo al escribir en FACTUSOL",
    "invoice_email_failed": "Fallo al enviar la factura",
}
#: Estado de la excepción → etiqueta (pestaña Incidencias).
EXCEPTION_STATUS_LABELS: dict[str, str] = {
    "open": "Abierta", "in_progress": "En curso",
    "resolved": "Resuelta", "dismissed": "Descartada",
}
#: Estado de cobro para la columna Cobro (código → etiqueta).
COBRO_LABELS: dict[str, str] = {
    "cobrado": "Cobrado ✓", "pendiente": "Pendiente", "na": "—",
}

#: Texto de «no aplica»: Envío (y Preparación, si no pasó por el taller) de un
#: pedido «No requiere envío» (pestaña «Sin envío» de la Cola SAT).
NO_APLICA = "No aplica"
#: Lo que escribió #487 en la columna Envío para estos pedidos («enviado sin
#: seguimiento»). Ya NO se escribe: se sigue reconociendo como valor de BoHub
#: para las hojas que lo tengan hasta la siguiente pasada.
ENVIO_SIN_SEGUIMIENTO_LEGACY = "Enviado (sin seguimiento)"


def is_sin_envio(order: Order) -> bool:
    """¿Marcado «No requiere envío» (pestaña «Sin envío»)? El pedido NO se
    envía (recogida en tienda, licencia, servicio…): no cuenta como enviado."""
    return bool(getattr(order, "shipping_not_required", False))


def _prep_label(order: Order) -> str:
    """Etiqueta de la columna Preparación. Un pedido «No requiere envío» que
    se llegó a embalar sale «Listo»; si no pasó por el taller, «No aplica»."""
    st = str(getattr(order.preparation_status, "value", order.preparation_status) or "")
    if is_sin_envio(order) and st != "packed":
        return NO_APLICA
    return PREPARACION_LABELS.get(st, "—")


def _envio_label(order: Order) -> str:
    """Etiqueta de la columna Envío; «No aplica» si no requiere envío (no se
    envía: no es «enviado»).

    Con envío Genei y escaneos del transportista (`/tracking`), manda el ÚLTIMO
    ESCANEO REAL, en vocabulario cerrado («Pendiente de entrada en red»,
    «Recogido», «En reparto», «Entregado»…) — no el genérico del transporte,
    que podía dar por recogido lo que la agencia aún no había escaneado. El
    texto literal de la agencia se ve en la app (Enviados, ficha)."""
    if is_sin_envio(order):
        return NO_APLICA
    from app.erp.integrations.genei.service import genei_state_of  # noqa: PLC0415
    from app.erp.integrations.genei.tracking import carrier_step_label  # noqa: PLC0415

    real = carrier_step_label(genei_state_of(order).get("carrier_step"))
    if real:
        return real
    st = getattr(order.transport_status, "value", order.transport_status)
    return ENVIO_LABELS.get(str(st or ""), "—")


def envio_vocabulary() -> list[str]:
    """Todos los valores que BoHub escribe en la columna Envío (transporte +
    escaneo real del transportista), sin repetir."""
    from app.erp.integrations.genei.tracking import CARRIER_STEP_LABELS  # noqa: PLC0415

    return list(dict.fromkeys([*ENVIO_LABELS.values(), *CARRIER_STEP_LABELS.values()]))


def _cobro_state(order: Order) -> str:
    """`cobrado` / `pendiente` / `na` (sin factura → no aplica). Lee el estado
    CONTABLE ya persistido (`factusol_cobro_status`); NO toca FACTUSOL en vivo."""
    if not order.factusol_invoice_number:
        return "na"
    return "cobrado" if (order.factusol_cobro_status or "") == "cobrada" else "pendiente"


def _factura_label(order: Order) -> str:
    """Texto de la columna Factura: el nº de factura o, en una MUESTRA / envío
    no facturable, «No aplica» (no hay factura que esperar)."""
    from app.erp.sample_orders import is_sample_order  # noqa: PLC0415

    if is_sample_order(order):
        return NO_APLICA
    return order.factusol_invoice_number or ""


def _origen_label(order: Order) -> str:
    """Origen del pedido: `WEB` para los de la tienda; `Muestra` para un envío
    no facturable; para los demás, el canal de origen (`shipping_origin`:
    SAT/OFI/TER…) o «Manual (BoHub)» si no consta. No hay campo de comercial/agente en
    el pedido todavía."""
    from app.erp.sample_orders import is_sample_order  # noqa: PLC0415

    if order.external_source == OrderSource.WOOCOMMERCE:
        return "WEB"
    if is_sample_order(order):
        return "Muestra"
    # «Manual (BoHub)», no «Manual» a secas: en la hoja de Drive, Origen =
    # MANUAL marca una fila tecleada a mano (ver `drive_managed`), y un pedido
    # manual de BoHub no puede confundirse con ella.
    return (order.shipping_origin or "").strip() or "Manual (BoHub)"


def _serie_whiterip(order: Order) -> str:
    """Datos técnicos combinados «Nº serie · WhiteRIP» (solo máquinas/licencias);
    se unen los no vacíos."""
    parts = [str(order.serial_number or "").strip(), str(order.whiterip_license or "").strip()]
    return " · ".join(p for p in parts if p)


def _exception_motivo(exc: Any) -> str:
    """Texto legible de una excepción: la descripción libre de `metadata_json`
    (sat_issue), la ETA (stock eta_set) o la nota de resolución."""
    raw = exc.metadata_json
    if raw:
        try:
            meta = json.loads(raw)
        except (TypeError, ValueError):
            meta = None
        if isinstance(meta, dict):
            for key in ("description", "descripcion", "detalle", "motivo", "note", "text"):
                if meta.get(key):
                    return str(meta[key])
            if meta.get("eta_date"):
                prov = f" ({meta['provider']})" if meta.get("provider") else ""
                return f"ETA {meta['eta_date']}{prov}"
        elif isinstance(meta, str) and meta.strip():
            return meta.strip()
    return exc.resolution_note or ""


def _incidencia_details(
    session: Session, order_ids: list[str],
) -> dict[str, dict[str, Any]]:
    """`{order_id: {tipo, motivo, asignado, fecha, estado}}` de la excepción
    ABIERTA (o en curso) más reciente de cada pedido, para la pestaña
    Incidencias. Un pedido en Situación=Incidencia sin excepción registrada
    (bloqueo por empresa sin vincular / VIES) se sintetiza en `build_rows`."""
    if not order_ids:
        return {}
    from app.erp.models import ErpException, ExceptionStatus  # noqa: PLC0415
    from app.models.crm import User  # noqa: PLC0415

    excs = list(session.scalars(
        select(ErpException).where(
            ErpException.order_id.in_(order_ids),
            ErpException.status.in_([ExceptionStatus.OPEN, ExceptionStatus.IN_PROGRESS]),
        ).order_by(ErpException.created_at.desc())
    ))
    assignee_ids = {e.assigned_to_user_id for e in excs if e.assigned_to_user_id}
    names: dict[str, str] = {}
    if assignee_ids:
        names = {
            u.id: u.full_name
            for u in session.scalars(select(User).where(User.id.in_(assignee_ids)))
        }
    out: dict[str, dict[str, Any]] = {}
    for e in excs:
        if e.order_id in out:
            continue  # la más reciente por pedido (ya viene ordenado desc)
        tipo_v = getattr(e.type, "value", e.type)
        estado_v = getattr(e.status, "value", e.status)
        out[e.order_id] = {
            "tipo": EXCEPTION_TYPE_LABELS.get(str(tipo_v or ""), str(tipo_v or "")),
            "motivo": _exception_motivo(e),
            "asignado": names.get(e.assigned_to_user_id or "") or "",
            "fecha": _iso_date(e.created_at),
            "estado": EXCEPTION_STATUS_LABELS.get(str(estado_v or ""), str(estado_v or "")),
        }
    return out


def _primary_alert(workflow_row: dict[str, Any]) -> dict[str, Any] | None:
    """La alerta que explica por qué el pedido está donde está: la BLOQUEANTE
    (Incidencia, reportada a mano) y, si no hay, la de REVISAR (el aviso que
    detecta la app sola y manda a «Por revisar»). Alimenta la columna
    «Nota / Incidencia» de la hoja, que tiene que decir algo en los dos casos."""
    alerts = workflow_row.get("alerts", [])
    for alert in alerts:
        if alert.get("blocking"):
            return alert
    for alert in alerts:
        if alert.get("review"):
            return alert
    return None


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
    # Control manual — nombre de quien quitó el pedido del seguimiento (para
    # la vista de excluidos), en 1 query.
    excluded_by_ids = {
        uid for o in orders
        for uid in (o.seguimiento_excluded_by_user_id, o.completed_by_user_id,
                    o.seguimiento_forced_by_user_id)
        if uid
    }
    excluded_by_names: dict[str, str] = {}
    if excluded_by_ids:
        from app.models.crm import User  # noqa: PLC0415

        excluded_by_names = {
            u.id: u.full_name
            for u in session.scalars(select(User).where(User.id.in_(excluded_by_ids)))
        }
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
    # Rediseño 2026 — la Situación de cada pedido reutiliza la clasificación de
    # colas de la línea de vida (workflow.py), la misma que la bandeja. Se
    # calcula en lote (WorkflowContext, sin N+1); de ahí salen también las
    # alertas bloqueantes (para la columna Nota / Incidencia) y el último envío
    # de la factura por email.
    from app.erp.workflow import (  # noqa: PLC0415
        QUEUE_INCIDENCIAS,
        latest_invoice_emailed_map,
        workflows_for,
    )

    wf_map = workflows_for(session, orders)
    emailed_map = latest_invoice_emailed_map(session, [o.id for o in orders])
    incidencia_ids = [
        oid for oid, wf in wf_map.items() if wf.get("queue") == QUEUE_INCIDENCIAS
    ]
    inc_details = _incidencia_details(session, incidencia_ids)

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
        # Regla de vivos: anulado, manual sin aprobar y web que la tienda no ha
        # llegado a procesar (pending/on-hold/cancelled/failed/draft) quedan
        # FUERA; el web `refunded` se queda, marcado «Reembolsado». Independiente
        # de la exclusión manual de F6-fix7.
        oculto_estado, estado_woo_motivo, reembolsado = seguimiento_visibility(o)
        # «Forzar en seguimiento»: decisión explícita de ver igualmente uno de
        # los ocultos por estado. Sigue marcado como oculto (para poder
        # deshacerlo desde esa misma vista), pero cuenta como visible.
        forzado = o.seguimiento_forced_at is not None
        fuera_por_estado = oculto_estado and not forzado
        productos = " · ".join(
            f"{float(line.quantity):g}× {line.description or line.product_sku}"
            for line in o.lines
        )
        empresa_name = (
            series_names.get(serie) or FALLBACK_SERIES_NAMES.get(serie)
            or (f"Serie {serie}" if serie else None)
        )
        # Rediseño 2026 — Situación (cola de la línea de vida), incidencia y
        # datos derivados de la nueva hoja.
        wf = wf_map.get(o.id) or {}
        situacion = wf.get("queue") or "listo"
        blocking = _primary_alert(wf)
        # Detalle de la incidencia: la excepción abierta reportada a mano. Desde
        # que «Incidencia» es solo manual, esa excepción existe siempre que la
        # situación sea Incidencia; el fallback se queda por si acaso, para que
        # la pestaña Incidencias case SIEMPRE con la hoja.
        incidencia = inc_details.get(o.id)
        if situacion == QUEUE_INCIDENCIAS and incidencia is None:
            incidencia = {
                "tipo": "Incidencia",
                "motivo": (blocking or {}).get("text") or "",
                "asignado": "",
                "fecha": _iso_date(o.placed_at or o.created_at),
                "estado": "Abierta",
            }
        # Estado propio «Reembolsado»: manda sobre la cola de la línea de vida
        # (que, al estar el pedido auto-anulado, diría «Listo»). No pisa una
        # Incidencia: si hay una excepción abierta, sigue habiendo trabajo y la
        # pestaña Incidencias tiene que seguir cuadrando con la hoja.
        if reembolsado and situacion != QUEUE_INCIDENCIAS:
            situacion = SITUACION_REEMBOLSADO
        emailed = (emailed_map.get(o.id) or "")[:10] or None
        cobro = _cobro_state(o)
        rows.append({
            "id": o.id,
            "order_number": o.order_number,
            "serie": serie,
            # ERP-F6-fix5: serie de factura registrada en BoHub (real) y serie
            # deducida de la tienda, para la resolución de Empresa del sync.
            "serie_invoice": serie_invoice,
            "serie_store": serie_store,
            "empresa": empresa_name,
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
            "factura": _factura_label(o),
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
            "excluido_por_nombre": excluded_by_names.get(
                o.seguimiento_excluded_by_user_id or "",
            ),
            # «Marcar completado» (solo BoHub, reversible): estado FINAL manual;
            # se enseña como badge junto al estado y se filtra con
            # `estado=completado`.
            "completado": o.completed_at is not None,
            "completado_en": _iso_date(o.completed_at),
            "completado_por_nombre": excluded_by_names.get(o.completed_by_user_id or ""),
            # ERP-F6-fix7 — «escrito en Drive» vs «pendiente de escribir». La
            # vista diaria enseña ambos; el pendiente es lo que aún no está en
            # la hoja (y no está excluido).
            "escrito_drive": o.id in written_ids,
            "pendiente_escribir": (
                _en_curso(o, estado)
                and o.seguimiento_excluded_at is None
                and not fuera_por_estado
                and o.id not in written_ids
            ),
            # ERP-Woo — estado de WooCommerce y su efecto en el seguimiento.
            "woo_status": o.woo_status,
            "oculto_por_estado": oculto_estado,
            "estado_woo_motivo": estado_woo_motivo,
            #: El mismo motivo, en legible («Sin pagar», «En espera»…), para
            #: «Ver ocultos por estado».
            "estado_woo_motivo_label": woo.motivo_label(estado_woo_motivo),
            #: Estado propio «Reembolsado» (NO «anulado»): reembolsado en la
            #: tienda. Se VE en el seguimiento aunque esté anulado en BoHub.
            "reembolsado": reembolsado,
            #: «Forzar en seguimiento»: se ve aunque esté oculto por estado.
            "forzado": forzado,
            "forzado_en": _iso_date(o.seguimiento_forced_at),
            "forzado_por_nombre": excluded_by_names.get(
                o.seguimiento_forced_by_user_id or "",
            ),
            # --- rediseño 2026: hoja simplificada, ordenada por Situación ---
            #: Situación = cola de la línea de vida (reutiliza workflow.py).
            "situacion": situacion,
            "situacion_label": SITUACION_LABELS.get(situacion, situacion),
            "situacion_tone": SITUACION_TONE.get(situacion, "n"),
            #: Total del pedido (número + moneda) para la columna Importe.
            "importe": float(o.total_amount or 0),
            "moneda": o.currency or "EUR",
            #: «N · Nombre» de la empresa emisora (serie), p. ej. «2 · MQ Europe».
            "empresa_serie": (
                f"{serie} · {empresa_name}" if serie and empresa_name
                else (empresa_name or "")
            ),
            #: Fecha de la factura (emisión registrada en BoHub); reutiliza el
            #: hecho real del evento de factura, sin leer FECFAC en vivo.
            "fecha_factura": _iso_date(_real_event_date(
                o, "invoice", {"generated", "invoiced_by_erp"},
            )),
            #: Fecha del envío de la factura por email al cliente
            #: (`erp.invoice_emailed`), o None si no se envió.
            "factura_enviada": emailed,
            #: Estado de cobro FACTUSOL (contable, ya persistido).
            "cobro": cobro,
            "cobro_label": COBRO_LABELS.get(cobro, "—"),
            #: Preparación (SAT) y Envío; «No aplica» si no requiere envío.
            "preparacion": _prep_label(o),
            "envio": _envio_label(o),
            #: Origen: WEB o el canal/comercial.
            "origen_label": _origen_label(o),
            #: Datos técnicos combinados (máquinas/licencias).
            "serie_whiterip": _serie_whiterip(o),
            #: Motivo del bloqueo (columna Nota / Incidencia), si lo hay.
            "nota_incidencia": (blocking or {}).get("text") or "",
            #: Detalle de la incidencia (pestaña Incidencias) o None.
            "incidencia": incidencia,
            #: Espejo (Fase 2): ¿tiene envío Genei? Entonces su Tracking lo manda
            #: Genei (no se lee de la hoja y la celda va protegida).
            "envio_genei": bool(_genei_shipment_code(o)),
        })
    # Espejo (Fase 2): lo escrito a mano en la hoja en las columnas editables de
    # una fila de BoHub (Cliente, Factura, Factura enviada, Nº serie · WhiteRIP)
    # se ve también aquí — pantalla, Excel y hoja dicen lo mismo.
    from app.erp.seguimiento_mirror import apply_overrides_to_rows  # noqa: PLC0415

    apply_overrides_to_rows(session, rows)
    return rows


def _genei_shipment_code(order: Order) -> str | None:
    from app.erp.integrations.genei.service import shipment_code_of_order  # noqa: PLC0415

    try:
        return shipment_code_of_order(order)
    except Exception:  # noqa: BLE001 — packing_json raro: sin envío Genei
        return None


#: Claves de fila por las que se puede ordenar (E3-A-fix1: misma idea).
#: `situacion` (rediseño 2026) es la ordenación por DEFECTO: por prioridad de
#: cola y, dentro, por fecha (más nuevo primero) — lo urgente sube solo.
SORT_KEYS = {
    "situacion", "fecha", "cliente", "empresa", "vendedor", "transportista",
    "origen", "estado", "factura", "albaran_pedido",
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
    sort: str = "situacion",
    direction: str = "desc",
) -> list[dict[str, Any]]:
    """Filtros + búsqueda + orden de la vista, en Python (mismo patrón que el
    explorador de documentos). Por defecto: solo pedidos EN CURSO — la parte
    de arriba del Excel, lo que Bart mira a diario.

    ERP-F6-fix7 — los pedidos EXCLUIDOS a mano quedan FUERA por defecto. Con
    `ver_excluidos` se listan SOLO los excluidos.
    ERP-Woo — los OCULTOS POR ESTADO (anulado, o web que la tienda no llegó a
    procesar: pending / on-hold / cancelado / fallido / borrador) también quedan
    FUERA por defecto; con `ver_ocultos_estado` se listan SOLO esos (para
    revisarlos y, si hace falta, «Reincluir»). Son cosas DISTINTAS de la
    exclusión manual, y con vistas distintas.
    `pendiente_escribir=True` deja solo los que aún no están en la hoja de
    Drive."""
    out = rows
    if ver_excluidos:
        # Vista de excluidos MANUALMENTE: solo ellos, sin el filtro de «en curso».
        return _sort_rows([r for r in out if r["excluido"]], sort, direction)
    if ver_ocultos_estado:
        # Vista de ocultados por ESTADO: solo esos — incluidos los FORZADOS,
        # que siguen listados aquí para poder deshacer el forzado.
        return _sort_rows([r for r in out if r["oculto_por_estado"]], sort, direction)
    # En cualquier otra vista, ni los excluidos ni los ocultados por estado
    # aparecen (ni cuentan como pendientes); un forzado sí.
    out = [
        r for r in out
        if not r["excluido"] and not (r["oculto_por_estado"] and not r["forzado"])
    ]
    if pendiente_escribir:
        out = [r for r in out if r["pendiente_escribir"]]
    if estado == "completado":
        # «Completado» es final (nunca «en curso»): se lista aparte, con los
        # demás filtros de la vista.
        out = [r for r in out if r["completado"]]
    elif en_curso:
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
    if estado and estado != "completado":
        out = [r for r in out if r["estado"] == estado]
    if q:
        needle = q.casefold().strip()
        out = [
            r for r in out
            if any(needle in str(r[f] or "").casefold() for f in _SEARCH_FIELDS)
        ]
    return _sort_rows(out, sort, direction)


def _neg_ordinal(iso: str | None) -> int:
    """Ordinal NEGADO de una fecha ISO (para ordenar de más nueva a más vieja
    en orden ascendente); 0 si no hay fecha."""
    return -date.fromisoformat(iso).toordinal() if iso else 0


def _situacion_sort_key(row: dict[str, Any]) -> tuple[int, bool, int]:
    """Orden por Situación (prioridad de cola) y, dentro de cada grupo, por
    fecha descendente (más nuevo primero), con los sin fecha al final."""
    prio = SITUACION_ORDER.get(row.get("situacion") or "listo", 99)
    iso = row.get("fecha")
    return (prio, iso is None, _neg_ordinal(iso))


def sort_by_situacion(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Las filas ordenadas por Situación (prioridad de cola + fecha desc), que
    es el orden del rediseño 2026 en la pantalla y en «Descargar Excel»."""
    return sorted(rows, key=_situacion_sort_key)


def sort_by_fecha_desc(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Las filas por fecha del pedido, de más reciente a más antiguo (los sin
    fecha al final). Es el orden de la zona viva de la pestaña gestionada de
    Drive: ahí la Situación es una columna más (con su color y su autofiltro),
    no el criterio de orden. Desempate determinista por nº de pedido (el más
    alto primero), para que dos pasadas seguidas escriban la hoja igual."""
    return sorted(
        rows,
        key=lambda r: (
            r.get("fecha") is None, _neg_ordinal(r.get("fecha")),
            _desc_key(str(r.get("order_number") or "")),
        ),
    )


def _desc_key(text: str) -> tuple[int, ...]:
    """Clave que ordena un texto DESCENDENTE dentro de un `sorted` ascendente."""
    return tuple(-ord(c) for c in text)


def _sort_rows(
    out: list[dict[str, Any]], sort: str, direction: str,
) -> list[dict[str, Any]]:
    # Rediseño 2026 — orden por Situación: prioridad de cola + fecha desc. No
    # depende de `direction` (la prioridad manda; la fecha va siempre desc).
    if sort == "situacion":
        return sort_by_situacion(out)
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


# --- rediseño 2026: hoja simplificada (pantalla + exportación) -----------------
#
# 17 columnas, una fila por pedido, el estado en la columna Situación (no en la
# posición) y ordenadas por Situación. Es la forma de la pantalla y del Excel;
# la hoja de Drive mantiene su formato histórico aparte (ver el PR).

#: Columnas de la hoja «Pedidos» (rediseño 2026), en orden.
SEGUIMIENTO_COLUMNS_V2: list[str] = [
    "Situación", "Nº pedido", "Fecha", "Cliente", "Origen", "Productos",
    "Importe", "Empresa (serie)", "Factura", "Fecha factura",
    "Factura enviada", "Cobro", "Preparación", "Envío", "Fecha recogido",
    "Tracking", "Nº serie · WhiteRIP", "Nota / Incidencia",
    # Hito «id estable»: clave técnica de casado (última columna, OCULTA en la
    # hoja). Va la última a propósito: así los índices posicionales de todo lo
    # anterior (fechas, parser #465, anchos) no se mueven. Para pedidos de BoHub
    # es el `Order.id`; para el histórico legacy, su id sintético.
    "id",
]
#: Índice (0-based) de la columna técnica «id» (la última). Oculta en la hoja.
ID_INDEX = SEGUIMIENTO_COLUMNS_V2.index("id")
#: Columnas de la pestaña «Incidencias» (subconjunto de Situación=Incidencia).
INCIDENCIAS_COLUMNS: list[str] = [
    "Nº pedido", "Cliente", "Tipo", "Motivo", "Asignado a", "Fecha", "Estado",
]
#: Índices (0-based) de las columnas que son FECHAS. Se escriben como valor de
#: fecha real —`date` en el Excel, serial + `numberFormat` en Drive—, nunca
#: como texto, para que la hoja ordene por fecha de verdad (como texto,
#: «1/9/2026» va antes que «12/3/2026»). Visualización uniforme DD/MM/AAAA.
#: Fecha · Fecha factura · Factura enviada · Fecha recogido.
PEDIDOS_DATE_COLUMNS: tuple[int, ...] = (2, 9, 10, 14)
INCIDENCIAS_DATE_COLUMNS: tuple[int, ...] = (5,)
#: En la zona VIVA «Preparación» es un estado del taller («En cola», «Listo»),
#: pero en el HISTÓRICO es la fecha en que se preparó: ahí también va como
#: valor de fecha, para que ordene con el resto.
HISTORICO_DATE_COLUMNS: tuple[int, ...] = (*PEDIDOS_DATE_COLUMNS, 12)
#: Formato de fecha de esas columnas (Excel y Sheets usan el mismo patrón).
DATE_PATTERN = "DD/MM/YYYY"
#: Ancho aproximado de cada columna de «Pedidos» (para que el Excel se lea). La
#: última («id») es técnica y va OCULTA; el ancho solo se usaría si se muestra.
_PEDIDOS_WIDTHS = [13, 16, 11, 30, 10, 34, 12, 18, 14, 12, 13, 12, 13, 13, 12, 16, 20, 30, 300]
_INCIDENCIAS_WIDTHS = [16, 30, 24, 40, 18, 11, 12]

def _sheet_date_value(iso: str | None) -> date | str:
    """Celda de fecha del formato nuevo: un `date` real, o "" si no hay. Un
    valor que no es ISO (p. ej. una fecha tecleada a mano en la hoja que no se
    entiende —espejo, Fase 2—) sale tal cual, como texto: nunca rompe el volcado
    ni se inventa una fecha."""
    if not iso:
        return ""
    try:
        return date.fromisoformat(iso)
    except (TypeError, ValueError):
        return iso


#: Columnas del formato nuevo a las que se reparten los tokens que el histórico
#: traía empaquetados en «Nota / Incidencia» (`Clave: Valor · Clave: Valor`).
ORIGEN_INDEX = SEGUIMIENTO_COLUMNS_V2.index("Origen")
PREPARACION_INDEX = SEGUIMIENTO_COLUMNS_V2.index("Preparación")
ENVIO_INDEX = SEGUIMIENTO_COLUMNS_V2.index("Envío")
RECOGIDO_INDEX = SEGUIMIENTO_COLUMNS_V2.index("Fecha recogido")
NOTA_INDEX = SEGUIMIENTO_COLUMNS_V2.index("Nota / Incidencia")

#: Clave del token (normalizada: sin tildes, en minúsculas) → columna destino.
_NOTA_DESTINOS: dict[str, int] = {
    "vendedor": ORIGEN_INDEX,
    "transporte": ENVIO_INDEX,
    "transport": ENVIO_INDEX,
    "preparado": PREPARACION_INDEX,
    "recogido": RECOGIDO_INDEX,
}
#: Claves que se quedan en la Nota. `orden` va SIN su prefijo (es texto libre
#: del equipo, lo más valioso que hay ahí); `proforma` conserva la etiqueta
#: porque no tiene columna propia y sin ella no se sabría qué número es.
_NOTA_LIBRE_SIN_ETIQUETA = {"orden"}
#: Separador de tokens que escribió el import (punto medio con espacios).
_NOTA_SEPARADOR = "\u00b7"
#: Cabecera colada como fila de datos: no es una nota, es basura.
_NOTA_CABECERA = "nota / incidencia"


def _clave_token(text: str) -> str:
    """Clave de un token, normalizada para comparar: sin tildes, minúsculas."""
    s = unicodedata.normalize("NFKD", str(text or "").strip())
    return "".join(c for c in s if not unicodedata.combining(c)).casefold()


def split_nota_tokens(text: Any) -> tuple[dict[int, str], str]:
    """Reparte la «Nota / Incidencia» empaquetada del histórico.

    El import viejo volcaba ahí todo lo que no tenía columna, como
    `Vendedor: WEB · Transporte: UPS · Preparado: 28/08/2026 · Proforma: 1543`.
    Devuelve (columna destino → valor, nota que queda).

    Detalles que importan:

    - el valor puede llevar `:` dentro (`Orden: ALBARÁN: RECOGE EL CLIENTE…`),
      así que se parte por el PRIMER `:` y el resto es el valor entero;
    - `Orden` va a la nota SIN el prefijo; `Proforma` y cualquier clave
      desconocida, CON él (si no, no se sabría qué es ese número);
    - un token sin valor (`Vendedor:` a secas) o sin `:` se ignora, no rompe;
    - la cabecera colada (`Nota / Incidencia`) y el vacío no se parsean.

    Las fechas no se tocan aquí: `Preparado`/`Recogido` salen como texto y el
    volcado las pasa a valor de fecha si se pueden leer (una rota se queda como
    texto, ver `parse_sheet_date`)."""
    raw = str(text or "").strip()
    if not raw or raw.casefold() == _NOTA_CABECERA:
        return {}, ""
    campos: dict[int, str] = {}
    libres: list[str] = []
    for token in raw.split(_NOTA_SEPARADOR):
        token = token.strip()
        if not token:
            continue
        clave, sep, valor = token.partition(":")
        valor = valor.strip()
        if not sep or not valor:
            continue                      # `Vendedor:` a secas, o clave suelta
        destino = _NOTA_DESTINOS.get(_clave_token(clave))
        if destino is not None:
            campos.setdefault(destino, valor)
        elif _clave_token(clave) in _NOTA_LIBRE_SIN_ETIQUETA:
            libres.append(valor)
        else:
            libres.append(f"{clave.strip()}: {valor}")
    return campos, " · ".join(libres)[:500]


def redistribute_nota(row: list[Any]) -> list[Any]:
    """Una fila del formato nuevo con la nota empaquetada → la misma fila con
    cada token en su columna. Solo rellena columnas VACÍAS (lo que ya tiene
    dato manda) y deja la nota con lo que de verdad es texto libre. Idempotente:
    una fila ya repartida no tiene tokens que mover."""
    original = list(row)
    cruda = original[NOTA_INDEX] if len(original) > NOTA_INDEX else ""
    texto = str(cruda or "").strip()
    campos, nota = split_nota_tokens(cruda)
    if not campos and not nota and texto and texto.casefold() != _NOTA_CABECERA:
        return original                   # texto libre sin tokens: se queda tal cual
    if not campos and nota == texto:
        return original                   # nada que repartir: ni se toca ni se rellena
    out = original + [""] * max(0, len(SEGUIMIENTO_COLUMNS_V2) - len(original))
    for destino, valor in campos.items():
        if not str(out[destino] or "").strip():
            out[destino] = valor
    out[NOTA_INDEX] = nota
    return out


def row_to_pedidos_values(row: dict[str, Any]) -> list[Any]:
    """Los 19 valores de una fila de «Pedidos», en orden. `Importe` va como
    NÚMERO (float) y las fechas como `date` (ver `PEDIDOS_DATE_COLUMNS`) para
    que el Excel y la hoja las traten como lo que son; el resto, texto. La última
    columna es el `id` técnico (clave de casado; para un pedido de BoHub, su
    `Order.id`)."""
    return [
        row.get("situacion_label") or "",
        row.get("order_number") or "",
        _sheet_date_value(row.get("fecha")),
        row.get("cliente") or "",
        row.get("origen_label") or "",
        (row.get("productos") or "")[:300],
        float(row.get("importe") or 0),
        row.get("empresa_serie") or "",
        row.get("factura") or "",
        _sheet_date_value(row.get("fecha_factura")),
        _sheet_date_value(row.get("factura_enviada")),
        row.get("cobro_label") or "",
        row.get("preparacion") or "",
        row.get("envio") or "",
        # «Fecha recogido»: el hecho real de la Cola SAT (recogido / en
        # tránsito / etiqueta), nunca una fecha estampada al importar.
        _sheet_date_value(row.get("recogido")),
        row.get("tracking") or "",
        row.get("serie_whiterip") or "",
        row.get("nota_incidencia") or "",
        # id técnico (clave de casado). Para un pedido de BoHub, su `Order.id`.
        row.get("id") or "",
    ]


def incidencia_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Subconjunto EXACTO de filas con Situación = Incidencia (para la pestaña
    Incidencias); mismo conjunto que en la hoja Pedidos."""
    return [r for r in rows if r.get("situacion") == "incidencias"]


def incidencia_values(row: dict[str, Any]) -> list[Any]:
    """Los 7 valores de una fila de la pestaña «Incidencias»."""
    detail = row.get("incidencia") or {}
    return [
        row.get("order_number") or "",
        row.get("cliente") or "",
        detail.get("tipo") or "",
        detail.get("motivo") or "",
        detail.get("asignado") or "",
        _sheet_date_value(detail.get("fecha")),
        detail.get("estado") or "",
    ]


def export_xlsx(rows: list[dict[str, Any]]) -> bytes:
    """Exportación local a .xlsx (rediseño 2026): dos pestañas —«Pedidos»
    (ordenada por Situación, celda Situación coloreada, cabecera fija,
    autofiltro, Importe con formato €) e «Incidencias» (subconjunto de
    Situación=Incidencia)—. Respeta los filtros ya aplicados. Funciona sin
    Drive."""
    import io  # noqa: PLC0415

    from openpyxl import Workbook  # noqa: PLC0415
    from openpyxl.styles import Alignment, Font, PatternFill  # noqa: PLC0415
    from openpyxl.utils import get_column_letter  # noqa: PLC0415

    header_font = Font(bold=True)
    header_fill = PatternFill("solid", fgColor="F2F4F7")

    def _style_header(ws: Any, widths: list[int]) -> None:
        for cell in ws[1]:
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(vertical="center")
        for i, width in enumerate(widths, start=1):
            ws.column_dimensions[get_column_letter(i)].width = width
        ws.freeze_panes = "A2"  # cabecera fija

    wb = Workbook()
    ws = wb.active
    ws.title = "Pedidos"
    ws.append(SEGUIMIENTO_COLUMNS_V2)
    _style_header(ws, _PEDIDOS_WIDTHS)
    # La columna técnica «id» (última) va OCULTA también en el Excel: es la clave
    # de casado, no un dato que Bart mire. Sigue ahí para no perder la referencia.
    ws.column_dimensions[get_column_letter(ID_INDEX + 1)].hidden = True
    for r_i, row in enumerate(rows, start=2):
        ws.append(row_to_pedidos_values(row))
        situ = row.get("situacion") or "listo"
        bg, ink = SITUACION_FILL.get(situ, _SITUACION_FILL_FALLBACK)
        situ_cell = ws.cell(row=r_i, column=1)
        situ_cell.fill = PatternFill("solid", fgColor=bg)
        situ_cell.font = Font(bold=True, color=ink)
        ws.cell(row=r_i, column=7).number_format = "#,##0.00 €"  # Importe
        # Fechas como VALOR de fecha (ordenables), DD/MM/AAAA.
        for c in PEDIDOS_DATE_COLUMNS:
            ws.cell(row=r_i, column=c + 1).number_format = DATE_PATTERN
        if row.get("cobro") == "cobrado":  # «Cobrado ✓» en verde
            ws.cell(row=r_i, column=12).font = Font(bold=True, color="1F7A45")
    ws.auto_filter.ref = (
        f"A1:{get_column_letter(len(SEGUIMIENTO_COLUMNS_V2))}{ws.max_row}"
    )

    inc = wb.create_sheet("Incidencias")
    inc.append(INCIDENCIAS_COLUMNS)
    _style_header(inc, _INCIDENCIAS_WIDTHS)
    for r_i, row in enumerate(incidencia_rows(rows), start=2):
        inc.append(incidencia_values(row))
        for c in INCIDENCIAS_DATE_COLUMNS:
            inc.cell(row=r_i, column=c + 1).number_format = DATE_PATTERN
    inc.auto_filter.ref = (
        f"A1:{get_column_letter(len(INCIDENCIAS_COLUMNS))}{inc.max_row}"
    )

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
