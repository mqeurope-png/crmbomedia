"""Albarán FACTUSOL a demanda desde las LÍNEAS de un pedido MANUAL de BoHub.

Un pedido tecleado en BoHub (origen `manual`) no tiene documento en FACTUSOL,
así que la cadena E3-B (que COPIA un origen por sufijo) no tiene de dónde
partir. Aquí el «origen» se fabrica en memoria con los mismos builders que
«Nueva proforma» (C-4: `build_quote_payload` / `build_quote_line_payload`,
cabecera con el cliente de F_CLI y las bandas de IVA, líneas con el CODART
interno o texto libre) y se retaga a `F_ALB`/`F_LAL` con la maquinaria de la
Fase 2: `COD*` entero, `ESTALB=0`, tipos contrastados con la fila REAL de la
misma serie, guard de esquema ESTRICTO (si no cuadra, no se escribe nada) y
el registro exacto en el log. El albarán es AUTÓNOMO: sus líneas se enlazan a
sí mismo (`DOCLAL='A'`, `DTPLAL`/`DCOLAL` = su propia serie/número).

Solo lectura hasta el `EscribirRegistro` final; compensación por clave
compuesta si falla una línea (lección E2). Corre en `factusol:writes`.
"""
from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.erp.models import Order
from app.integrations.factusol.chain import (
    STRICT_SCHEMA_TARGETS,
    build_target_header,
    build_target_line,
    format_record,
    live_columns,
    log_chain_sync,
    next_doc_code,
    pick_template_row,
    schema_problems,
)
from app.integrations.factusol.client import FactusolClient, FactusolError
from app.integrations.factusol.customers import get_customer
from app.integrations.factusol.documents import DOC_SPECS, visible_number
from app.integrations.factusol.quotes import (
    build_quote_line_payload,
    build_quote_payload,
    resolve_codarts,
)
from app.integrations.factusol.vat_regime import REGIME_LABELS, REGIME_NACIONAL

logger = logging.getLogger(__name__)

#: Código de enlace de un albarán autónomo: `DOCLAL='A'` (albarán) apuntando a
#: su propia clave. Confirmado en vivo que 'A' es el código «albarán» en las
#: líneas hijas (discovery 2026-08-20, §F_LAL); el auto-enlace es la decisión
#: de Bart para los albaranes sin origen.
SELF_LINK_CODE = "A"

_DTO_RE = re.compile(r"dto\.\s*([0-9]+(?:[.,][0-9]+)?)\s*%")


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def order_lines_for_document(order: Order) -> list[dict[str, Any]]:
    """Líneas del pedido de BoHub en la forma de los builders de C-4
    (`codart`, `description`, `quantity`, `unit_price`, `discount_pct`,
    `iva_pct`). El SKU (o CODART) se traduce después a CODART interno; el
    descuento se recupera de la nota «dto. X%» que deja la Fase 1."""
    out: list[dict[str, Any]] = []
    for line in sorted(order.lines, key=lambda ln: ln.position):
        m = _DTO_RE.search(line.notes or "")
        out.append({
            "codart": (line.product_codart or line.product_sku or "").strip(),
            "description": (line.description or line.product_sku or "").strip(),
            "quantity": _num(line.quantity, 1.0) or 1.0,
            "unit_price": _num(line.unit_price),
            "discount_pct": _num(m.group(1).replace(",", ".")) if m else 0.0,
            "iva_pct": _num(line.tax_rate, 21.0),
        })
    return out


def customer_for_albaran(
    client: FactusolClient, *, codcli: Any, ejercicio: str,
) -> dict[str, Any]:
    """Cliente de F_CLI (el vínculo de la empresa) en la forma que esperan los
    builders de C-4: lo que el escritorio copia a la cabecera al elegirlo.
    `FactusolError` si el CODCLI ya no existe."""
    row = get_customer(client, codcli, ejercicio=ejercicio)
    if row is None:
        raise FactusolError(
            f"El cliente {codcli} vinculado a la empresa ya no existe en F_CLI "
            f"(ejercicio {ejercicio})."
        )
    return {
        "codcli": str(row.get("codcli") or codcli),
        "nombre": row.get("nombre") or row.get("nofcli") or "",
        "nif": row.get("nif") or "",
        "direccion": row.get("domcli") or "",
        "ciudad": row.get("pobcli") or "",
        "cp": row.get("cpocli") or "",
        "provincia": row.get("procli") or "",
        "pais": str(row.get("paicli") or "").strip(),
        "telefono": row.get("telcli") or "",
        "email": row.get("emacli") or "",
        # Régimen que codifica la ficha F_CLI (IVACLI), o None si no es
        # ninguno de los confirmados (Tarea C).
        "regime": row.get("regime"),
    }


def apply_regime(
    customer: dict[str, Any], lines: list[dict[str, Any]], *,
    regime: str | None, numero: str,
) -> tuple[str, list[dict[str, Any]], str | None]:
    """Régimen EFECTIVO del albarán y las líneas con el IVA que toca.

    Manda el régimen que sale de la empresa CRM (país + NIF-IVA, `regime`);
    sin país en el CRM, el que codifica la ficha F_CLI; y si tampoco, nacional.
    Intracomunitario / exportación → todas las líneas al 0 %. Si la ficha
    F_CLI dice otra cosa que el CRM se avisa (la ficha se corrige desde la
    empresa, «Régimen de IVA en FACTUSOL»); nunca se escribe F_CLI desde aquí.
    Devuelve `(régimen, líneas, aviso)`."""
    fcli_regime = customer.get("regime")
    effective = regime or fcli_regime or REGIME_NACIONAL
    warning = None
    if regime and fcli_regime and fcli_regime != regime:
        warning = (
            f"La ficha F_CLI del cliente {customer.get('codcli')} está como "
            f"{REGIME_LABELS.get(fcli_regime, fcli_regime)} y BoHub aplica "
            f"{REGIME_LABELS.get(effective, effective)} por el país / NIF-IVA de la "
            "empresa. Corrige la ficha desde la empresa («Régimen de IVA en FACTUSOL»)."
        )
        logger.warning("factusol albarán %s: %s", numero, warning)
    if effective != REGIME_NACIONAL:
        logger.info("factusol albarán %s: régimen %s → líneas al 0 %% de IVA",
                    numero, effective)
        lines = [{**line, "iva_pct": 0.0} for line in lines]
    return effective, lines, warning


def _coerce(real: Any, val: Any) -> Any:
    """`val` con el tipo JSON de la fila real: texto ↔ número cuando el valor
    lo permite (`'2458'` → 2458 para `CLIALB` entero; 724 → `'724'` para
    `CPAALB` texto). `int` y `float` se consideran equivalentes (como el
    guard) y NUNCA se trunca un decimal. Lo que no se puede convertir se
    deja tal cual: el guard lo caza y no se escribe nada."""
    if real is None or val is None or isinstance(real, bool) or isinstance(val, bool):
        return val
    if isinstance(real, str):
        return val if isinstance(val, str) else str(val)
    if isinstance(real, int | float) and isinstance(val, str):
        try:
            number = float(val.strip())
        except ValueError:
            return val
        return int(number) if number.is_integer() and isinstance(real, int) else number
    return val


def coerce_like_template(
    payload: dict[str, Any], template: dict[str, Any] | None,
) -> dict[str, Any]:
    """Cada columna con el MISMO tipo JSON que la fila real (`CLIALB` entero,
    `CPAALB` texto…): DELSOL quiere de vuelta el tipo que devuelve (dry-run de
    la Fase 2). Sin plantilla se deja tal cual."""
    if not template:
        return payload
    return {
        col: (_coerce(template[col], val) if col in template else val)
        for col, val in payload.items()
    }


def resolve_line_articles(
    client: FactusolClient, lines: list[dict[str, Any]], *, ejercicio: str,
    numero: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    """SKU → CODART interno (F_ART por CODART o EQUART, como las proformas);
    el que no case va como TEXTO LIBRE (`ART*=''`) para no romper el
    escritorio. Devuelve las líneas resueltas y las que fueron a texto libre."""
    try:
        codarts = resolve_codarts(
            client, [line.get("codart") for line in lines], ejercicio=ejercicio,
        )
    except FactusolError:
        logger.error(
            "factusol albarán %s: no se pudieron resolver los CODART; las líneas "
            "irán como texto libre", numero, exc_info=True,
        )
        codarts = {}
    resolved: list[dict[str, Any]] = []
    free_text: list[str] = []
    for i, line in enumerate(lines, start=1):
        sku = str(line.get("codart") or "").strip()
        codart = codarts.get(sku, "")
        if sku and not codart:
            free_text.append(sku)
            logger.warning(
                "factusol albarán %s: el SKU %r no casa con ningún CODART/EQUART "
                "de F_ART; la línea %d va como texto libre", numero, sku, i,
            )
        elif codart and codart != sku:
            logger.info("factusol albarán %s: SKU %r → CODART %r", numero, sku, codart)
        resolved.append({**line, "codart": codart})
    return resolved, free_text


def build_standalone_albaran(
    *, serie: int, codigo: str, ejercicio: str, customer: dict[str, Any],
    lines: list[dict[str, Any]], fecha: str, referencia: str | None,
    fopalb: str | None, allowed_header: frozenset[str],
    allowed_lines: frozenset[str],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Cabecera `F_ALB` + líneas `F_LAL` de un albarán SIN origen: el
    «origen» es un F_PRE virtual construido con los builders de C-4 y se
    retaga por sufijo con la Fase 2 (clave `(TIP, COD)` entero, fecha,
    `ESTALB=0`, allowlist viva). Las líneas se enlazan al propio albarán."""
    src, dst = DOC_SPECS["presupuestos"], DOC_SPECS["albaranes"]
    virtual_header = build_quote_payload(
        codigo, ejercicio=ejercicio, customer=customer,
        refpre=(referencia or "").strip(), lines=lines, fecha=fecha, fopfac=fopalb,
    )
    cabecera = build_target_header(
        virtual_header, src=src, dst=dst, serie=serie, codigo=codigo,
        fecha=fecha, allowed=allowed_header,
    )
    lineas = [
        build_target_line(
            build_quote_line_payload(codigo, i, line), src=src, dst=dst,
            serie=serie, codigo=codigo, posicion=i, origin_code=SELF_LINK_CODE,
            origin_tip=serie, origin_cod=int(codigo), allowed=allowed_lines,
        )
        for i, line in enumerate(lines, start=1)
    ]
    return cabecera, lineas


def create_standalone_albaran(
    session: Session, client: FactusolClient, *, order: Order,
    codcli: Any, serie: int, ejercicio: str, fecha: str | None = None,
    fopalb: str | None = None, actor_user_id: str | None = None,
    regime: str | None = None,
) -> dict[str, Any]:
    """Crea en FACTUSOL el albarán del pedido manual desde sus líneas.

    Orden: cliente de F_CLI → régimen de IVA (Tarea C: intracomunitario /
    exportación → líneas al 0 %) → líneas con CODART resuelto → cabecera/líneas
    retagadas → tipos como la fila real → guard de esquema ESTRICTO (no
    cuadra ⇒ no se escribe nada, error legible) → log del registro exacto →
    `F_ALB` y después `F_LAL` (compensación por clave compuesta si falla una
    línea). No hace commit: lo hace el caller junto con el vínculo al pedido.
    `regime` es el que sale de la empresa CRM (país + NIF-IVA), o None para
    usar el de la ficha F_CLI."""
    _ = actor_user_id
    dst = DOC_SPECS["albaranes"]
    lines = order_lines_for_document(order)
    if not lines:
        raise FactusolError(
            f"El pedido {order.order_number} no tiene líneas: no hay nada que "
            "poner en el albarán."
        )
    customer = customer_for_albaran(client, codcli=codcli, ejercicio=ejercicio)
    allowed_header = live_columns(client, dst.table, ejercicio=ejercicio)
    allowed_lines = live_columns(client, dst.lines_table, ejercicio=ejercicio)
    codigo = next_doc_code(
        client, dst.table, tip_col=dst.tip, cod_col=dst.cod, serie=serie,
        ejercicio=ejercicio,
    )
    numero = visible_number(serie, int(codigo))
    fecha_doc = fecha or datetime.now(UTC).date().isoformat()
    effective_regime, lines, regime_warning = apply_regime(
        customer, lines, regime=regime, numero=numero,
    )
    # Cabecera coherente con las líneas: `_totals` también aplica el régimen.
    customer = {**customer, "regime": effective_regime}
    resolved, free_text = resolve_line_articles(
        client, lines, ejercicio=ejercicio, numero=numero,
    )
    cabecera, lineas = build_standalone_albaran(
        serie=serie, codigo=codigo, ejercicio=ejercicio, customer=customer,
        lines=resolved, fecha=fecha_doc, referencia=order.order_number,
        fopalb=fopalb, allowed_header=allowed_header, allowed_lines=allowed_lines,
    )
    # Tipos como la fila REAL más reciente de la misma serie (la plantilla del
    # guard): `CLIALB` entero, `CPAALB` texto, importes float…
    header_template = pick_template_row(
        client.load_table(dst.table, filtro="1=1", ejercicio=ejercicio),
        tip_col=dst.tip, cod_col=dst.cod, serie=serie,
    )
    line_template = pick_template_row(
        client.load_table(dst.lines_table, filtro="1=1", ejercicio=ejercicio),
        tip_col=dst.line_tip, cod_col=dst.line_fk, serie=serie,
    )
    cabecera = coerce_like_template(cabecera, header_template)
    lineas = [coerce_like_template(linea, line_template) for linea in lineas]

    problems = schema_problems(
        client, dst=dst, cabecera=cabecera, lineas=lineas, serie=serie,
        ejercicio=ejercicio,
    )
    if problems:
        detail = (
            f"El esquema real de {dst.table}/{dst.lines_table} no cuadra con el "
            f"registro del albarán {numero} del pedido {order.order_number}: "
            + "; ".join(problems)
        )
        if "albaranes" in STRICT_SCHEMA_TARGETS:
            logger.error("factusol albarán manual: NO se escribe nada — %s", detail)
            raise FactusolError(detail + ". No se ha escrito nada.")
        logger.warning("factusol albarán manual: %s (se escribe igualmente)", detail)

    logger.info(
        "factusol albarán manual: EscribirRegistro %s %s ejercicio=%s registro=%s",
        dst.table, numero, ejercicio, format_record(cabecera),
    )
    for linea in lineas:
        logger.info(
            "factusol albarán manual: EscribirRegistro %s %s POS=%s registro=%s",
            dst.lines_table, numero, linea.get(f"POS{dst.lines_suffix}"),
            format_record(linea),
        )
    client.write_record(dst.table, cabecera, ejercicio=ejercicio)
    try:
        for linea in lineas:
            client.write_record(dst.lines_table, linea, ejercicio=ejercicio)
    except FactusolError:
        try:
            client.delete_records(
                dst.lines_table,
                f"{dst.line_tip}='{serie}' AND {dst.line_fk}='{codigo}'",
                ejercicio=ejercicio,
            )
            client.delete_records(
                dst.table, f"{dst.tip}='{serie}' AND {dst.cod}='{codigo}'",
                ejercicio=ejercicio,
            )
        except FactusolError:
            logger.warning(
                "factusol albarán manual: no se pudo limpiar %s %s a medias",
                dst.table, numero, exc_info=True,
            )
        raise
    log_chain_sync(
        session,
        message=(
            f"pedido manual {order.order_number} → {dst.table} {numero} "
            f"({len(lineas)} líneas desde BoHub, enlace DOC='{SELF_LINK_CODE}' "
            f"a sí mismo, régimen {effective_regime}"
            + (f"; texto libre: {', '.join(free_text)}" if free_text else "")
            + ")"
        ),
    )
    logger.info(
        "factusol albarán manual: creado %s %s desde las líneas del pedido %s",
        dst.table, numero, order.order_number,
    )
    return {
        "target_type": "albaranes", "serie": serie, "codigo": int(codigo),
        "numero": numero, "lines": len(lineas), "source": None,
        "standalone": True, "free_text_lines": free_text,
        "regime": effective_regime, "regime_warning": regime_warning,
        "origin_marked": True, "origin_mark_warning": None, "order": None,
    }
