"""ERP · Fase 1 — pedido de BoHub a partir de un documento que YA existe en
FACTUSOL (presupuesto/proforma `F_PRE`+`F_LPS`, pedido de cliente
`F_PCL`+`F_LPC`).

SOLO LECTURA en FACTUSOL: se lee el documento con el lector genérico de E3
(`documents.get_document`, clave compuesta serie+código, gotcha nº1 cubierto) y
se crea el `Order` en BoHub reflejando cliente, líneas (artículo, descripción,
cantidad, precio, descuento), importes y forma de pago (informativa, se guarda
en `packing_json.factusol_source`; la captura de pago es Fase 2). NO se escribe
albarán ni pedido en FACTUSOL (Fase 2).

Origen del pedido: `factusol_proforma` (presupuestos) o `factusol_pedido`
(pedidos de cliente), nunca `woocommerce` — así el filtro «solo processing» de
#387, que vive en la ingesta Woo, ni lo ve. `external_id` es el nº del
documento (CODPRE a secas para proformas, `serie-código` para pedidos) y sirve
de dedup: el mismo documento no se importa dos veces.

Este módulo es también el que usa «Convertir en pedido» de las proformas
(`quotes.convert_quote_to_order`), para que ambos caminos creen el mismo tipo
de pedido.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.erp.models import (
    Order,
    OrderLine,
    OrderSource,
    OrderStatusHistory,
    StatusDomain,
)
from app.integrations.factusol.client import FactusolClient
from app.integrations.factusol.documents import get_document, visible_number
from app.models.crm import Company

logger = logging.getLogger(__name__)

#: Tipo de documento FACTUSOL → origen del pedido en BoHub.
SOURCE_BY_DOC_TYPE: dict[str, OrderSource] = {
    "presupuestos": OrderSource.FACTUSOL_PROFORMA,
    "pedidos": OrderSource.FACTUSOL_PEDIDO,
}
#: Prefijo del nº de pedido en BoHub. El nº desnudo (último grupo de dígitos)
#: sigue siendo el del documento, que es lo que casa el seguimiento/Drive.
PREFIX_BY_DOC_TYPE = {"presupuestos": "PRO", "pedidos": "PCL"}
DOC_LABEL = {"presupuestos": "presupuesto", "pedidos": "pedido de cliente"}
#: IVA cuando la línea no trae uno (>0). En FACTUSOL `IVALPS`/`IVALPC` suele ir
#: a 0 con el IVA en la cabecera (banda 1), así que 0 NO significa exento.
DEFAULT_IVA_PCT = 21.0


class FactusolOrderError(Exception):
    """Base: la API la traduce a un HTTP concreto por `code`."""

    code = "factusol_order_error"


class UnsupportedDocumentType(FactusolOrderError):
    code = "unsupported_doc_type"


class DocumentNotFound(FactusolOrderError):
    code = "factusol_document_not_found"


class CustomerUnlinked(FactusolOrderError):
    """El cliente del documento no está vinculado a ninguna empresa CRM."""

    code = "factusol_customer_unlinked"

    def __init__(self, codcli: str | None, nombre: str | None):
        self.codcli = codcli
        self.nombre = nombre
        super().__init__(
            f"El cliente FACTUSOL {codcli or '?'} ({nombre or 'sin nombre'}) no está "
            "vinculado a ninguna empresa del CRM. Vincúlalo (o elige la empresa) "
            "antes de crear el pedido."
        )


class AlreadyImported(FactusolOrderError):
    """El documento ya se importó: se devuelve el pedido existente."""

    code = "already_imported"

    def __init__(self, order: Order):
        self.order_id = order.id
        self.order_number = order.order_number
        super().__init__(
            f"Este documento ya se importó como el pedido {order.order_number}."
        )


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError, AttributeError):
        return default


def external_id_for(doc_type: str, serie: int, codigo: int) -> str:
    """Proformas: el CODPRE a secas (TIPPRE es siempre '1' y así lo enseña la
    columna «Proforma» del seguimiento). Pedidos de cliente: `serie-código`."""
    if doc_type == "presupuestos":
        return str(int(codigo))
    return visible_number(serie, codigo)


def order_number_for(doc_type: str, serie: int, codigo: int) -> str:
    if doc_type == "presupuestos":
        return f"{PREFIX_BY_DOC_TYPE[doc_type]}-{int(codigo):06d}"
    return f"{PREFIX_BY_DOC_TYPE[doc_type]}-{visible_number(serie, codigo)}"


def find_existing(session: Session, source: OrderSource, external_id: str) -> Order | None:
    return session.scalar(select(Order).where(
        Order.external_source == source, Order.external_id == external_id,
    ))


def resolve_company_id(session: Session, codcli: Any) -> str | None:
    """Empresa CRM vinculada al cliente FACTUSOL (vínculo de C-3)."""
    code = str(codcli or "").strip()
    if not code:
        return None
    company_id = session.scalar(
        select(Company.id).where(Company.factusol_company_id == code)
    )
    if company_id is None and code.isdigit():
        # '0055' y '55' son el mismo CODCLI según cómo viaje por la API.
        company_id = session.scalar(
            select(Company.id).where(Company.factusol_company_id == str(int(code)))
        )
    return company_id


def build_order(
    session: Session, *, source: OrderSource, external_id: str, order_number: str,
    company_id: str | None, contact_id: str | None, placed_at: datetime | None,
    lines: list[dict[str, Any]], notes: str | None,
    packing_extra: dict[str, Any] | None, actor_user_id: str | None,
    history_reason: str,
) -> Order:
    """Crea el `Order` + líneas + historial (sin commit). Las líneas vienen en
    la forma del lector de documentos / `quote_lines_for_order`: `codart`,
    `description`, `quantity`, `unit_price`, `discount_pct`, `iva_pct`."""
    order = Order(
        external_source=source,
        external_id=external_id,
        order_number=order_number,
        company_id=company_id,
        contact_id=contact_id,
        currency="EUR",
        notes=notes,
        placed_at=placed_at or datetime.now(UTC),
        packing_json=json.dumps(packing_extra) if packing_extra else None,
    )
    session.add(order)
    session.flush()

    total = 0.0
    for i, line in enumerate(lines):
        quantity = _f(line.get("quantity"), 1.0) or 1.0
        unit_price = _f(line.get("unit_price"))
        discount = _f(line.get("discount_pct"))
        line_total = round(quantity * unit_price * (1 - discount / 100), 2)
        total += line_total
        codart = str(line.get("codart") or "").strip() or None
        description = str(line.get("description") or "").strip() or codart or "Línea"
        iva = _f(line.get("iva_pct"))
        session.add(OrderLine(
            order_id=order.id, position=i,
            product_sku=(codart or "")[:128],
            product_codart=(codart or None) and codart[:13],
            description=description[:255],
            quantity=quantity, unit_price=unit_price,
            tax_rate=iva if iva > 0 else DEFAULT_IVA_PCT,
            line_total=line_total,
            notes=f"dto. {discount:g}%" if discount else None,
        ))
    order.total_amount = round(total, 2)

    session.add(OrderStatusHistory(
        order_id=order.id, domain=StatusDomain.PREPARATION,
        from_status=None,
        to_status=getattr(order.preparation_status, "value", order.preparation_status),
        changed_at=datetime.now(UTC), changed_by_user_id=actor_user_id,
        reason=history_reason,
        metadata_json=json.dumps({
            "event": "order_created_from_factusol",
            "origin_source": source.value,
            "external_id": external_id,
            "created_by_user_id": actor_user_id,
        }),
    ))
    return order


def preview_factusol_document(
    session: Session, client: FactusolClient, *, doc_type: str, serie: int,
    codigo: int, ejercicio: str,
) -> dict[str, Any]:
    """Lee el documento (solo lectura) y dice cómo quedaría el pedido: cliente
    resuelto contra el CRM, líneas, nº de pedido y si ya se importó."""
    if doc_type not in SOURCE_BY_DOC_TYPE:
        raise UnsupportedDocumentType(
            f"Solo se importan presupuestos y pedidos de cliente (no {doc_type!r})."
        )
    doc = get_document(
        client, doc_type, serie=int(serie), codigo=int(codigo), ejercicio=ejercicio,
    )
    if doc is None:
        raise DocumentNotFound(
            f"No existe el {DOC_LABEL[doc_type]} {visible_number(serie, codigo)} "
            f"en FACTUSOL (ejercicio {ejercicio})."
        )
    source = SOURCE_BY_DOC_TYPE[doc_type]
    external_id = external_id_for(doc_type, serie, codigo)
    existing = find_existing(session, source, external_id)
    company_id = resolve_company_id(session, doc.get("cliente_codigo"))
    company_name = (
        session.scalar(select(Company.name).where(Company.id == company_id))
        if company_id else None
    )
    return {
        "doc_type": doc_type,
        "serie": int(serie),
        "codigo": int(codigo),
        "numero": doc["numero"],
        "fecha": doc.get("fecha"),
        "total": doc.get("total"),
        "referencia": doc.get("referencia"),
        "estado": doc.get("estado"),
        "estado_label": doc.get("estado_label"),
        "forma_pago": doc.get("forma_pago"),
        "cliente_codigo": doc.get("cliente_codigo"),
        "cliente_nombre": doc.get("cliente_nombre"),
        "company_id": company_id,
        "company_name": company_name,
        "company_linked": company_id is not None,
        "lines": doc.get("lines") or [],
        "order_number": order_number_for(doc_type, serie, codigo),
        "external_id": external_id,
        "already_imported": (
            {"order_id": existing.id, "order_number": existing.order_number}
            if existing is not None else None
        ),
    }


def _placed_at_from(fecha: str | None) -> datetime | None:
    if not fecha:
        return None
    try:
        return datetime.fromisoformat(str(fecha)[:10]).replace(tzinfo=UTC)
    except ValueError:
        return None


def factusol_source_block(
    *, doc_type: str, serie: int, codigo: int, referencia: str | None,
    forma_pago: str | None, forma_pago_nombre: str | None,
    cliente_codigo: str | None = None, total: float | None = None,
) -> dict[str, Any]:
    """Lo que queda en `packing_json.factusol_source` (trazabilidad + forma de
    pago informativa para Fase 2)."""
    return {
        "doc_type": doc_type,
        "serie": int(serie),
        "codigo": int(codigo),
        "numero": visible_number(serie, codigo),
        "referencia": referencia,
        "forma_pago": forma_pago,
        "forma_pago_nombre": forma_pago_nombre,
        "cliente_codigo": cliente_codigo,
        "total": total,
    }


def create_order_from_factusol_document(
    session: Session, client: FactusolClient, *, doc_type: str, serie: int,
    codigo: int, ejercicio: str, actor_user_id: str | None = None,
    company_id: str | None = None, contact_id: str | None = None,
    forma_pago_nombre: str | None = None,
) -> Order:
    """Lee el documento y crea el pedido en BoHub (sin commit; el caller decide).

    - Cliente: `company_id` explícito > empresa vinculada al CODCLI del
      documento. Sin ninguno de los dos (y sin `contact_id`) → `CustomerUnlinked`.
    - Dedup por (origen, nº de documento) → `AlreadyImported`.
    - Nada se escribe en FACTUSOL."""
    preview = preview_factusol_document(
        session, client, doc_type=doc_type, serie=serie, codigo=codigo,
        ejercicio=ejercicio,
    )
    if preview["already_imported"]:
        existing = find_existing(
            session, SOURCE_BY_DOC_TYPE[doc_type], preview["external_id"],
        )
        raise AlreadyImported(existing)  # type: ignore[arg-type]
    company_id = company_id or preview["company_id"]
    if not company_id and not contact_id:
        raise CustomerUnlinked(preview["cliente_codigo"], preview["cliente_nombre"])

    label = DOC_LABEL[doc_type]
    numero = preview["numero"]
    referencia = preview["referencia"]
    lines = list(preview["lines"])
    if not lines:
        # Documento sin líneas (edge case): una línea con el total para no
        # dejar el pedido vacío — mismo criterio que `quote_lines_for_order`.
        lines = [{
            "codart": None,
            "description": referencia or f"{label.capitalize()} {numero}",
            "quantity": 1.0, "unit_price": preview["total"] or 0.0,
            "discount_pct": 0.0, "iva_pct": None,
        }]
    notes = f"Creado desde el {label} FACTUSOL {numero}" + (
        f" · ref. {referencia}" if referencia else ""
    )
    order = build_order(
        session,
        source=SOURCE_BY_DOC_TYPE[doc_type],
        external_id=preview["external_id"],
        order_number=preview["order_number"],
        company_id=company_id,
        contact_id=contact_id,
        placed_at=_placed_at_from(preview["fecha"]),
        lines=lines,
        notes=notes,
        packing_extra={"factusol_source": factusol_source_block(
            doc_type=doc_type, serie=serie, codigo=codigo, referencia=referencia,
            forma_pago=preview["forma_pago"], forma_pago_nombre=forma_pago_nombre,
            cliente_codigo=preview["cliente_codigo"], total=preview["total"],
        )},
        actor_user_id=actor_user_id,
        history_reason=f"Pedido creado desde el {label} FACTUSOL {numero}",
    )
    logger.info(
        "erp: pedido %s creado desde %s FACTUSOL %s (%d líneas, %.2f €)",
        order.order_number, label, numero, len(lines), float(order.total_amount),
    )
    return order
