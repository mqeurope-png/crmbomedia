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
    "sociedadlimitada", "slne", "sarl", "sccl", "scoop", "coop",
    "slu", "sau", "sas", "srl", "sll", "scp", "sl", "sa", "sc", "cb",
)


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


_SHEET_DATE_FORMATS = ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d-%m-%Y")


def parse_sheet_date(value: Any) -> date | None:
    """Fecha de una celda de la hoja (`24/07/2026`, `24/7/2026`, ISO…) → date,
    o None si no se reconoce."""
    s = _TRAILING_DECIMAL.sub("", str(value or "").strip())
    if not s:
        return None
    for fmt in _SHEET_DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()  # noqa: DTZ007 — fecha civil
        except ValueError:
            continue
    return None


def order_match_numbers(row: dict[str, Any]) -> set[str]:
    """Números por los que un pedido puede localizarse en la hoja: el del
    pedido web/albarán propio y, si se conoce, el de albarán de FACTUSOL."""
    out: set[str] = set()
    for candidate in (row.get("order_number"), row.get("albaran_pedido"),
                      row.get("albaran_number")):
        num = extract_order_number(candidate)
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


def _serie_of_invoice(invoice_number: Any) -> int | None:
    """Serie del número de factura de FACTUSOL (`5-260123` → 5)."""
    num = str(invoice_number or "")
    head = num.split("-", 1)[0].strip() if "-" in num else ""
    return int(head) if head.isdigit() else None


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
    for key in (store_slug, store_id, source):
        if not key:
            continue
        raw = by_source.get(key)
        if raw is not None and str(raw).strip().isdigit():
            return int(str(raw).strip())
    return None

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


def _first_transition(order: Order, domain: str, to_statuses: set[str]) -> datetime | None:
    for h in order.status_history:
        if getattr(h.domain, "value", h.domain) == domain and h.to_status in to_statuses:
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


def build_rows(
    session: Session,
    *,
    customer_names: dict[str, dict[str, str | None]],
) -> list[dict[str, Any]]:
    """Todas las filas del seguimiento (sin filtrar). Carga pedidos, líneas e
    historial en 3 queries y los transportistas en 1 — sin N+1."""
    from app.erp.api.factusol import FALLBACK_SERIES_NAMES  # noqa: PLC0415
    from app.integrations.factusol.service import series_config  # noqa: PLC0415
    from app.models.integration_settings import IntegrationAccount  # noqa: PLC0415

    orders = list(session.scalars(
        select(Order).options(
            selectinload(Order.lines), selectinload(Order.status_history)
        )
    ))
    carriers = {c.id: c.name for c in session.scalars(select(Carrier))}
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
        cliente = names.get("company_name") or names.get("contact_name")
        source = getattr(o.external_source, "value", o.external_source)
        serie = resolve_empresa_serie(
            invoice_number=o.factusol_invoice_number,
            store_slug=store_slugs.get(o.store_id) if o.store_id else None,
            store_id=o.store_id,
            source=source,
            by_source=by_source,
        )
        estado = _estado(o)
        productos = " · ".join(
            f"{float(line.quantity):g}× {line.description or line.product_sku}"
            for line in o.lines
        )
        rows.append({
            "id": o.id,
            "order_number": o.order_number,
            "serie": serie,
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
            # Sin campo de agente en el pedido (fuera del alcance de F6): los
            # pedidos web son «WEB» (3.605 de 7.743 en el Excel); el resto
            # queda vacío hasta que Bart decida añadir el agente al modelo.
            "vendedor": "WEB" if o.external_source == OrderSource.WOOCOMMERCE else "",
            "origen": o.shipping_origin,
            "transportista": carriers.get(o.carrier_id) if o.carrier_id else None,
            "preparado": _iso_date(_first_transition(o, "preparation", {"packed"})),
            "recogido": _iso_date(_first_transition(
                o, "transport", {"in_transit", "delivered"},
            )),
            "fecha_envio_factura": _iso_date(_first_transition(
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
    sort: str = "fecha",
    direction: str = "desc",
) -> list[dict[str, Any]]:
    """Filtros + búsqueda + orden de la vista, en Python (mismo patrón que el
    explorador de documentos). Por defecto: solo pedidos EN CURSO — la parte
    de arriba del Excel, lo que Bart mira a diario."""
    out = rows
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
