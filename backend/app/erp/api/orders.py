"""BoHub ERP — API de pedidos: bandeja, ficha, Cola PEDIDOS, transiciones.

Fase A PR 3. Los pedidos se crean a mano (external_source='manual') para
probar el flujo end-to-end; la ingesta Woo llega en Fase B. Toda mutación
de estado pasa por el engine (PR 2) — este router no toca columnas de
estado directamente.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import not_found
from app.db.session import get_session
from app.erp.api.deps import require_erp_approve, require_erp_edit, require_erp_view
from app.erp.factusol_albaran import PaymentIn
from app.erp.models import (
    ErpException,
    ExceptionStatus,
    InvoiceStatus,
    Order,
    OrderLine,
    OrderSource,
    OrderStatusHistory,
    StatusDomain,
)
from app.erp.state_machine import TransitionError, apply_transition, available_transitions
from app.models.crm import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/erp/orders", tags=["erp-orders"])


# --- schemas -----------------------------------------------------------------


class OrderLineIn(BaseModel):
    # C-4: el SKU es OPCIONAL. En pedidos manuales (servicios, reparaciones,
    # muestras) muchas veces no hay SKU real; lo que identifica la línea es la
    # descripción. La columna en BD es NOT NULL, así que se guarda "" — sin
    # migración.
    product_sku: str = Field(default="", max_length=128)
    product_codart: str | None = Field(default=None, max_length=13)
    description: str = Field(default="", max_length=255)
    quantity: float = Field(default=1, gt=0)
    unit_price: float = Field(default=0, ge=0)
    tax_rate: float = Field(default=21, ge=0, le=100)
    notes: str | None = None

    @model_validator(mode="after")
    def _require_sku_or_description(self) -> OrderLineIn:
        """Una línea en blanco no es facturable ni preparable: al menos uno de
        los dos campos identificativos tiene que venir."""
        if not (self.product_sku or "").strip() and not (self.description or "").strip():
            raise ValueError("Cada línea necesita al menos SKU o descripción.")
        return self


class AddressIn(BaseModel):
    """Dirección de envío/facturación de un pedido manual (D-2). Vive en
    `packing_json` — el pedido no tiene columnas de dirección y esta fase no
    lleva migración."""

    address_line: str | None = Field(default=None, max_length=500)
    city: str | None = Field(default=None, max_length=200)
    postal_code: str | None = Field(default=None, max_length=20)
    state: str | None = Field(default=None, max_length=200)
    country: str | None = Field(default="España", max_length=120)

    def is_empty(self) -> bool:
        return not any([self.address_line, self.city, self.postal_code, self.state])


class FactusolSourceIn(BaseModel):
    """Fase 1 — el pedido se crea a mano PERO a partir de un documento de
    FACTUSOL (el alta lo precargó): queda marcado con ese origen y nº."""

    doc_type: Literal["presupuestos", "pedidos"]
    serie: int = Field(ge=1, le=9)
    codigo: int = Field(ge=1)
    referencia: str | None = Field(default=None, max_length=250)
    forma_pago: str | None = Field(default=None, max_length=10)
    forma_pago_nombre: str | None = Field(default=None, max_length=120)


class OrderCreate(BaseModel):
    # D-2: opcional — si no llega, se genera `MANUAL-000001` (secuencial).
    order_number: str | None = Field(default=None, max_length=32)
    contact_id: str | None = None
    company_id: str | None = None
    currency: str = Field(default="EUR", max_length=3)
    notes: str | None = None
    placed_at: datetime | None = None
    lines: list[OrderLineIn] = Field(default_factory=list, min_length=1)
    # Extras del alta manual (D-2) — se guardan en `packing_json`.
    tax_id: str | None = Field(default=None, max_length=64)
    pickup_in_store: bool = False
    shipping_address: AddressIn | None = None
    billing_address: AddressIn | None = None
    # Fase 1: origen FACTUSOL (presupuesto / pedido de cliente) del alta manual.
    factusol_source: FactusolSourceIn | None = None
    # Fase 2 (solo con `factusol_source`): paso de confirmación de pago
    # (opción B: se apunta; el cobro F-4-B se registra al existir la factura)
    # y creación del albarán en FACTUSOL (encolada en `factusol:writes`).
    payment: PaymentIn | None = None
    create_albaran: bool = True

    @model_validator(mode="after")
    def _require_customer(self) -> OrderCreate:
        # Un pedido sin cliente no es accionable (ni facturable ni enviable).
        # La dirección de envío NO se valida aquí: es requisito del formulario
        # (se puede marcar «Recogida en tienda»), no del contrato de la API.
        if not self.contact_id and not self.company_id:
            raise ValueError("El pedido necesita un contacto o una empresa.")
        return self


class TransitionIn(BaseModel):
    domain: str
    to_status: str
    reason: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)


# --- helpers -----------------------------------------------------------------


def _get_order(session: Session, order_id: str) -> Order:
    order = session.scalar(
        select(Order).where(Order.id == order_id)
        .options(selectinload(Order.lines), selectinload(Order.status_history))
    )
    if order is None:
        raise not_found("Order")
    return order


def _status_value(v: Any) -> str:
    return getattr(v, "value", v)


def _contact_label(contact: Any) -> str | None:
    """«Nombre Apellido» del contacto (D-2). None si no hay contacto."""
    if contact is None:
        return None
    parts = [contact.first_name or "", contact.last_name or ""]
    return " ".join(p for p in parts if p).strip() or None


def customer_names(
    session: Session, orders: list[Order],
) -> dict[str, dict[str, str | None]]:
    """D-2: `{order_id: {contact_name, company_name}}` en 2 queries (sin N+1).

    El ERP muestra el cliente junto al número de pedido en TODAS las vistas
    (bandeja, colas, ficha, excepciones) — el número solo no basta para saber
    a quién va dirigido."""
    from app.models.crm import Company, Contact  # noqa: PLC0415

    contact_ids = {o.contact_id for o in orders if o.contact_id}
    company_ids = {o.company_id for o in orders if o.company_id}
    contacts: dict[str, Any] = {}
    companies: dict[str, str] = {}
    if contact_ids:
        contacts = {
            c.id: c for c in session.scalars(
                select(Contact).where(Contact.id.in_(contact_ids))
            )
        }
    if company_ids:
        companies = {
            c.id: c.name for c in session.scalars(
                select(Company).where(Company.id.in_(company_ids))
            )
        }
    # Control manual (#388 + bandeja) y «Completado»: nombres de quien QUITÓ o
    # COMPLETÓ el pedido (vista «Ver ocultados», badge «Completado»), en 1 query.
    user_ids = {
        uid for o in orders
        for uid in (o.seguimiento_excluded_by_user_id, o.completed_by_user_id)
        if uid
    }
    user_names: dict[str, str] = {}
    if user_ids:
        user_names = {
            u.id: u.full_name for u in session.scalars(
                select(User).where(User.id.in_(user_ids))
            )
        }
    return {
        o.id: {
            "contact_name": _contact_label(contacts.get(o.contact_id)),
            "company_name": companies.get(o.company_id) if o.company_id else None,
            "excluded_by_name": user_names.get(o.seguimiento_excluded_by_user_id or ""),
            "completed_by_name": user_names.get(o.completed_by_user_id or ""),
        }
        for o in orders
    }


def _serialise_summary(
    o: Order, names: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    names = names or {}
    return {
        "id": o.id,
        "order_number": o.order_number,
        # D-2: nombre del cliente para no tener que abrir el pedido.
        "contact_name": names.get("contact_name"),
        "company_name": names.get("company_name"),
        "external_source": _status_value(o.external_source),
        "store_id": o.store_id,
        "contact_id": o.contact_id,
        "company_id": o.company_id,
        "total_amount": float(o.total_amount or 0),
        "currency": o.currency,
        "payment_status": _status_value(o.payment_status),
        "preparation_status": _status_value(o.preparation_status),
        "transport_status": _status_value(o.transport_status),
        "invoice_status": _status_value(o.invoice_status),
        "tracking_number": o.tracking_number,
        "factusol_invoice_number": o.factusol_invoice_number,
        # Fase 2: nº del albarán FACTUSOL creado por BoHub al convertir.
        "factusol_albaran_number": o.factusol_albaran_number,
        # Cobro manual: estado de cobro EN FACTUSOL de la factura del pedido
        # («cobrada» / «pendiente» / null = sin factura o sin comprobar),
        # distinto del «Pagado» del CRM, + serie resuelta y último detalle.
        "factusol_invoice_serie": o.factusol_invoice_serie,
        "factusol_cobro_status": o.factusol_cobro_status,
        "factusol_cobro_checked_at": (
            o.factusol_cobro_checked_at.isoformat()
            if o.factusol_cobro_checked_at else None
        ),
        "factusol_cobro": _factusol_cobro(o),
        # ERP-F6 — campos del seguimiento (Excel de Bart): nº de serie (texto
        # libre, también notas), licencia WhiteRIP y origen del envío.
        "serial_number": o.serial_number,
        "whiterip_license": o.whiterip_license,
        "shipping_origin": o.shipping_origin,
        # E4-fix1: idioma del pedido (detectado en la importación Woo o
        # corregido a mano). Alimenta la cascada de idioma de los PDF.
        "language": o.language,
        "approved_at": o.approved_at.isoformat() if o.approved_at else None,
        "placed_at": o.placed_at.isoformat() if o.placed_at else None,
        "created_at": o.created_at.isoformat(),
        "externally_processed_at": (
            o.externally_processed_at.isoformat()
            if o.externally_processed_at else None
        ),
        # Control manual (#388 + bandeja): QUITADO de las listas de trabajo
        # (bandeja, Cola PEDIDOS, colas SAT y seguimiento/Drive) con el mismo
        # flag reversible de F6-fix7. Quién, cuándo y motivo para la vista
        # «Ver ocultados».
        "excluded": o.seguimiento_excluded_at is not None,
        "seguimiento_excluded_at": (
            o.seguimiento_excluded_at.isoformat()
            if o.seguimiento_excluded_at else None
        ),
        "seguimiento_excluded_reason": o.seguimiento_excluded_reason,
        "seguimiento_excluded_by_user_id": o.seguimiento_excluded_by_user_id,
        "seguimiento_excluded_by_name": names.get("excluded_by_name"),
        # «Marcar completado» (solo BoHub, reversible): estado FINAL manual.
        # Quién y cuándo; nunca se propaga a WooCommerce.
        "completed": o.completed_at is not None,
        "completed_at": o.completed_at.isoformat() if o.completed_at else None,
        "completed_by_user_id": o.completed_by_user_id,
        "completed_by_name": names.get("completed_by_name"),
    }


def _serialise_detail(session: Session, o: Order, actor: User) -> dict[str, Any]:
    exceptions = list(session.scalars(
        select(ErpException).where(ErpException.order_id == o.id)
        .order_by(ErpException.created_at.desc())
    ))
    return {
        **_serialise_summary(o, customer_names(session, [o]).get(o.id)),
        "notes": o.notes,
        "packing": json.loads(o.packing_json) if o.packing_json else None,
        # Fase 2: paso de pago apuntado al convertir (opción B) y su cobro.
        "factusol_payment": _factusol_payment(o),
        # Documento de ORIGEN imprimible en FACTUSOL («PDF del pedido
        # (FACTUSOL)»); None = sin documento → la ficha deshabilita el botón.
        "factusol_document": _factusol_document(session, o),
        "lines": [
            {
                "id": line.id, "position": line.position,
                "product_sku": line.product_sku,
                "product_codart": line.product_codart,
                "description": line.description,
                "quantity": float(line.quantity),
                "unit_price": float(line.unit_price),
                "tax_rate": float(line.tax_rate),
                "line_total": float(line.line_total),
                "notes": line.notes,
            }
            for line in o.lines
        ],
        "status_history": [
            {
                "id": h.id, "domain": _status_value(h.domain),
                "from_status": h.from_status, "to_status": h.to_status,
                "changed_at": h.changed_at.isoformat(),
                "changed_by_user_id": h.changed_by_user_id,
                "reason": h.reason,
                "metadata": json.loads(h.metadata_json) if h.metadata_json else {},
            }
            for h in o.status_history
        ],
        "exceptions": [
            {
                "id": e.id, "type": _status_value(e.type), "subtype": e.subtype,
                "status": _status_value(e.status),
                "metadata": json.loads(e.metadata_json) if e.metadata_json else {},
                "created_at": e.created_at.isoformat(),
            }
            for e in exceptions
        ],
        "available_transitions": {
            domain.value: [
                {"to_status": t.to_status, "label": t.label,
                 "required_evidence": list(t.required_evidence)}
                for t in available_transitions(o, domain, actor)
            ]
            for domain in StatusDomain
        },
        "blockers": _blockers(session, o),
        "warnings": _warnings(session, o),
        "externally_processed_note": o.externally_processed_note,
        "externally_processed_by_user_id": o.externally_processed_by_user_id,
    }


def _factusol_payment(o: Order) -> dict[str, Any] | None:
    from app.erp.factusol_albaran import payment_intent  # noqa: PLC0415

    return payment_intent(o)


def _factusol_cobro(o: Order) -> dict[str, Any] | None:
    from app.erp.factusol_cobro import cobro_info  # noqa: PLC0415

    return cobro_info(o)


#: Etiqueta del documento de origen (para la UI y los mensajes).
FACTUSOL_DOC_LABEL: dict[str, str] = {
    "presupuestos": "presupuesto", "pedidos": "pedido de cliente",
}


def _factusol_document(session: Session, o: Order) -> dict[str, Any] | None:
    """Documento de ORIGEN del pedido en FACTUSOL, el que imprime «PDF del
    pedido (FACTUSOL)»:

    - creado desde un presupuesto / pedido de cliente (Fase 1): el propio
      documento, con tipo, serie y número guardados en
      `packing_json.factusol_source` (`by_ref=False`);
    - pedido web: el F_PCL que crea la app Woo→FACTUSOL, que solo se puede
      localizar por su referencia común `REFPCL` al descargar (`by_ref=True`,
      con la referencia `ref` que se buscará — sin consultar FACTUSOL en cada
      carga de la ficha: el botón siempre intenta la descarga y solo un 404
      controlado enseña el aviso);
    - alta manual sin origen: `None` — no hay nada que imprimir y la ficha
      deshabilita el botón en vez de fallar."""
    from app.erp.factusol_albaran import is_web_order, order_source  # noqa: PLC0415
    from app.integrations.factusol.documents import visible_number  # noqa: PLC0415
    from app.integrations.factusol.service import pcl_ref_for_order  # noqa: PLC0415

    src = order_source(o)
    if src is not None:
        serie, codigo = int(src["serie"]), int(src["codigo"])
        return {
            "doc_type": src["doc_type"], "serie": serie, "codigo": codigo,
            "numero": visible_number(serie, codigo),
            "label": FACTUSOL_DOC_LABEL[src["doc_type"]], "by_ref": False,
            "ref": None,
        }
    if is_web_order(o):
        return {
            "doc_type": "pedidos", "serie": None, "codigo": None, "numero": None,
            "label": FACTUSOL_DOC_LABEL["pedidos"], "by_ref": True,
            "ref": pcl_ref_for_order(session, o),
        }
    return None


def _factusol_live(session: Session) -> bool:
    # Toggle de Fase C: gobierna SOLO la consulta en vivo del estado de factura
    # (endpoint factusol-status, C-2-fix2). NO interviene ya en el cálculo de
    # bloqueos/warnings: el ERP confía en la fuente y no valida SKU ni empresas
    # contra FACTUSOL (B-2-fix5, reforzado en C-2-fix3).
    from app.erp.models import ERP_SETTINGS_SINGLETON_ID, ErpSettings  # noqa: PLC0415

    cfg = session.get(ErpSettings, ERP_SETTINGS_SINGLETON_ID)
    return bool(cfg and cfg.factusol_live)


def _open_exception_blocker(session: Session, o: Order) -> dict[str, str] | None:
    """Excepciones abiertas de tipos operativos reales (SAT/transporte/
    facturación) — bloqueo permanente de la Cola PEDIDOS (B-2-fix5)."""
    n = session.scalar(
        select(func.count(ErpException.id)).where(
            ErpException.order_id == o.id,
            ErpException.status.in_(
                [ExceptionStatus.OPEN, ExceptionStatus.IN_PROGRESS]
            ),
        )
    ) or 0
    if n:
        return {
            "code": "open_exceptions",
            "detail": f"{n} excepción(es) sin resolver",
        }
    return None


def _blockers(session: Session, o: Order) -> list[dict[str, str]]:
    """Bloqueos que impiden aprobar en la Cola PEDIDOS.

    Solo excepciones abiertas de tipos operativos reales (SAT/transporte/
    facturación). El ERP confía en el pedido tal como llega de la fuente:
    no valida SKU ni el vínculo empresa→FACTUSOL (filosofía B-2-fix5,
    reforzada en C-2-fix3; esa validación es responsabilidad de la app
    externa WooCommerce→FACTUSOL)."""
    real = _open_exception_blocker(session, o)
    return [real] if real else []


def _warnings(session: Session, o: Order) -> list[dict[str, str]]:
    """Sin warnings automáticos: el ERP confía en la fuente (B-2-fix5,
    reforzada en C-2-fix3)."""
    return []


def worklist_visible(stmt):  # noqa: ANN001, ANN201 — Select[Order]
    """Control manual (#388 + bandeja): los pedidos QUITADOS a mano
    (`seguimiento_excluded_at`) salen de TODAS las listas de trabajo — bandeja,
    Cola PEDIDOS, colas SAT y seguimiento/Drive — con un solo flag. «Quitar»
    significa «este pedido fuera de mis listas»; «Reincluir» lo devuelve a
    todas. La ficha del pedido (`/{order_id}`) sigue accesible."""
    return stmt.where(Order.seguimiento_excluded_at.is_(None))


# --- endpoints ---------------------------------------------------------------


@router.post("", status_code=201)
def create_order(
    payload: OrderCreate,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_edit),
) -> dict[str, Any]:
    """Alta manual (external_source='manual') — encargos por teléfono,
    muestras y reparaciones sin ticket Woo.

    D-2: `order_number` es opcional (se genera `MANUAL-000001`); la dirección
    de envío/facturación y el NIF viven en `packing_json` (sin migración)."""
    # Fase 1: si el alta parte de un documento FACTUSOL, el pedido lleva ese
    # origen (nunca `woocommerce`), su nº como external_id (dedup) y un nº de
    # pedido PRO-/PCL- con el nº del documento.
    fs = payload.factusol_source
    # Fase 2: el paso de pago se valida ANTES de crear nada (400 si la cuenta
    # no está en el catálogo). Solo tiene sentido con origen FACTUSOL.
    resolved_payment = (
        _resolve_payment_or_400(session, payload.payment) if fs is not None else None
    )
    if fs is not None:
        from app.erp.orders_from_factusol import (  # noqa: PLC0415
            SOURCE_BY_DOC_TYPE,
            AlreadyImported,
            external_id_for,
            find_existing,
            order_number_for,
        )

        source = SOURCE_BY_DOC_TYPE[fs.doc_type]
        external_id = external_id_for(fs.doc_type, fs.serie, fs.codigo)
        existing = find_existing(session, source, external_id)
        if existing is not None:
            raise _factusol_order_http_error(AlreadyImported(existing))
        default_number = order_number_for(fs.doc_type, fs.serie, fs.codigo)
    else:
        source, external_id, default_number = OrderSource.MANUAL, None, None
    number = (
        (payload.order_number or "").strip() or default_number
        or _next_manual_number(session)
    )
    if session.scalar(select(Order.id).where(Order.order_number == number)):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"order_number ya existe: {number!r}",
        )
    total = 0.0
    order = Order(
        external_source=source,
        external_id=external_id,
        order_number=number,
        contact_id=payload.contact_id,
        company_id=payload.company_id,
        currency=payload.currency,
        notes=payload.notes,
        placed_at=payload.placed_at or datetime.now(UTC),
        packing_json=_manual_packing_json(payload),
    )
    session.add(order)
    session.flush()
    for i, line in enumerate(payload.lines):
        line_total = round(line.quantity * line.unit_price, 2)
        # `total_amount` = importe FINAL (con el IVA de cada línea); la suma
        # de líneas a secas era la base y la bandeja enseñaba el importe sin
        # impuestos.
        total += line_total * (1 + float(line.tax_rate or 0) / 100)
        session.add(OrderLine(
            order_id=order.id, position=i,
            product_sku=line.product_sku, product_codart=line.product_codart,
            description=line.description or line.product_sku,
            quantity=line.quantity, unit_price=line.unit_price,
            tax_rate=line.tax_rate, line_total=line_total, notes=line.notes,
        ))
    order.total_amount = round(total, 2)
    # D-2: traza del alta manual en el historial (quién y desde dónde).
    session.add(OrderStatusHistory(
        order_id=order.id, domain=StatusDomain.PREPARATION,
        from_status=None,
        to_status=_status_value(order.preparation_status),
        changed_at=datetime.now(UTC), changed_by_user_id=current_user.id,
        reason=(
            "Pedido manual creado desde el ERP" if fs is None else
            f"Pedido creado desde el "
            f"{'presupuesto' if fs.doc_type == 'presupuestos' else 'pedido de cliente'} "
            f"FACTUSOL {fs.serie}-{fs.codigo:06d} (alta manual)"
        ),
        metadata_json=json.dumps({
            "event": "order_created_manual",
            "origin_source": source.value,
            "created_by_user_id": current_user.id,
        }),
    ))
    session.commit()
    extra: dict[str, Any] = {}
    if fs is not None:
        extra = _fase2_after_create(
            session, order, resolved_payment=resolved_payment,
            create_albaran=payload.create_albaran, actor=current_user,
        )
        session.commit()
    return {
        **_serialise_detail(session, _get_order(session, order.id), current_user),
        **extra,
    }


#: Prefijo + ancho del secuencial de los pedidos manuales (D-2).
MANUAL_ORDER_PREFIX = "MANUAL-"
MANUAL_ORDER_PAD = 6


def _next_manual_number(session: Session) -> str:
    """Siguiente `MANUAL-000001` libre: max(secuencial) + 1 sobre los que ya
    siguen el patrón. El choque real lo corta el 409 del endpoint."""
    rows = session.scalars(
        select(Order.order_number).where(
            Order.order_number.like(f"{MANUAL_ORDER_PREFIX}%")
        )
    )
    top = 0
    for number in rows:
        suffix = (number or "")[len(MANUAL_ORDER_PREFIX):]
        if suffix.isdigit():
            top = max(top, int(suffix))
    return f"{MANUAL_ORDER_PREFIX}{top + 1:0{MANUAL_ORDER_PAD}d}"


def _manual_packing_json(payload: OrderCreate) -> str | None:
    """Direcciones + NIF del alta manual → `packing_json` (el pedido no tiene
    columnas de dirección y D-2 no lleva migración)."""
    data: dict[str, Any] = {}
    if payload.tax_id:
        data["tax_id"] = payload.tax_id
    if payload.pickup_in_store:
        data["pickup_in_store"] = True
    if payload.shipping_address and not payload.shipping_address.is_empty():
        data["shipping_address"] = payload.shipping_address.model_dump()
    if payload.billing_address and not payload.billing_address.is_empty():
        data["billing_address"] = payload.billing_address.model_dump()
    if payload.factusol_source is not None:
        from app.erp.orders_from_factusol import factusol_source_block  # noqa: PLC0415

        fs = payload.factusol_source
        data["factusol_source"] = factusol_source_block(
            doc_type=fs.doc_type, serie=fs.serie, codigo=fs.codigo,
            referencia=fs.referencia, forma_pago=fs.forma_pago,
            forma_pago_nombre=fs.forma_pago_nombre,
        )
    return json.dumps(data) if data else None


@router.get("")
def list_orders(
    payment: str | None = Query(default=None),
    preparation: str | None = Query(default=None),
    transport: str | None = Query(default=None),
    invoice: str | None = Query(default=None),
    store: str | None = Query(default=None),
    show_external: bool = Query(default=False),
    # Control manual — «Ver ocultados»: SOLO los quitados a mano (para
    # revisarlos y reincluirlos). Por defecto la bandeja los esconde.
    show_excluded: bool = Query(default=False),
    # «Completado»: true = solo completados, false = solo sin completar,
    # ausente = todos (el badge distingue).
    completed: bool | None = Query(default=None),
    # Cobro FACTUSOL (estado contable, no el «Pagado» del CRM): «cobrada»,
    # «pendiente» (factura sin cobro completo) o «sin_comprobar» (con factura
    # pero aún sin consultar FACTUSOL).
    cobro: str | None = Query(default=None, pattern="^(cobrada|pendiente|sin_comprobar)$"),
    sort: str = Query(default="placed_desc"),
    limit: int = Query(default=100, ge=1, le=500),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    _ = current_user
    stmt = select(Order)
    if payment:
        stmt = stmt.where(Order.payment_status == payment)
    if cobro == "sin_comprobar":
        stmt = stmt.where(
            Order.factusol_invoice_number.isnot(None),
            Order.factusol_cobro_status.is_(None),
        )
    elif cobro:
        stmt = stmt.where(Order.factusol_cobro_status == cobro)
    if preparation:
        stmt = stmt.where(Order.preparation_status == preparation)
    if transport:
        stmt = stmt.where(Order.transport_status == transport)
    if invoice:
        stmt = stmt.where(Order.invoice_status == invoice)
    if store:
        stmt = stmt.where(Order.store_id == store)
    if completed is True:
        stmt = stmt.where(Order.completed_at.isnot(None))
    elif completed is False:
        stmt = stmt.where(Order.completed_at.is_(None))
    if show_excluded:
        # Vista de revisión: solo los quitados a mano (con motivo y quién).
        stmt = stmt.where(Order.seguimiento_excluded_at.isnot(None))
    else:
        # Control manual: los quitados no aparecen en la bandeja.
        stmt = worklist_visible(stmt)
        # B-2-fix4: por defecto la bandeja esconde los procesados externamente.
        if not show_external:
            stmt = stmt.where(Order.externally_processed_at.is_(None))
    order_by = {
        "placed_desc": Order.placed_at.desc(),
        "placed_asc": Order.placed_at.asc(),
        "total_desc": Order.total_amount.desc(),
        "created_desc": Order.created_at.desc(),
    }.get(sort, Order.placed_at.desc())
    rows = list(session.scalars(
        stmt.options(selectinload(Order.lines)).order_by(order_by).limit(limit)
    ))
    names = customer_names(session, rows)
    return {"items": [_serialise_summary(o, names.get(o.id)) for o in rows]}


@router.get("/pending-approval")
def pending_approval(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    """Cola PEDIDOS: pendientes de revisión con sus bloqueos calculados."""
    _ = current_user
    rows = list(session.scalars(
        worklist_visible(select(Order).where(Order.preparation_status == "pending_review"))
        .options(selectinload(Order.lines))
        .order_by(Order.placed_at.asc())
    ))
    names = customer_names(session, rows)
    return {
        "items": [
            {
                **_serialise_summary(o, names.get(o.id)),
                "blockers": _blockers(session, o),
                "warnings": _warnings(session, o),
            }
            for o in rows
        ],
    }


# --- Fase 1: pedido desde un documento de FACTUSOL (solo lectura allí) --------


class OrderFromFactusolIn(BaseModel):
    doc_type: Literal["presupuestos", "pedidos"]
    serie: int = Field(ge=1, le=9)
    codigo: int = Field(ge=1)
    #: Empresa/contacto explícitos (si el cliente FACTUSOL no está vinculado o
    #: se quiere otro). Sin ellos, se resuelve por el vínculo CODCLI ↔ CRM.
    company_id: str | None = None
    contact_id: str | None = None
    # Fase 2: paso de pago (opción B) + albarán en FACTUSOL.
    payment: PaymentIn | None = None
    create_albaran: bool = True


def _resolve_payment_or_400(
    session: Session, payment: PaymentIn | None,
) -> dict[str, Any] | None:
    """Valida el paso de pago ANTES de crear nada: cuenta desconocida o fecha
    ilegible → 400 (nunca se apunta un cobro contra una cuenta adivinada)."""
    if payment is None:
        return None
    from app.erp.factusol_albaran import PaymentError, resolve_payment  # noqa: PLC0415

    try:
        return resolve_payment(session, payment)
    except PaymentError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, {
            "code": exc.code, "detail": str(exc),
        }) from exc


def _fase2_after_create(
    session: Session, order: Order, *, resolved_payment: dict[str, Any] | None,
    create_albaran: bool, actor: User,
) -> dict[str, Any]:
    """Fase 2, tras crear el pedido desde un documento FACTUSOL: apunta el
    pago (opción B) y encola el albarán en `factusol:writes`. Nada de esto
    escribe en FACTUSOL desde la API; el worker serial lo hace y la ficha
    hace polling del job. Un fallo al encolar no deshace el pedido."""
    from app.erp.factusol_albaran import albaran_blocker, record_payment_intent  # noqa: PLC0415

    extra: dict[str, Any] = {
        "albaran_job_id": None, "albaran_error": None, "albaran_skipped": None,
    }
    if resolved_payment is not None:
        record_payment_intent(session, order, resolved_payment, actor_user_id=actor.id)
    if create_albaran:
        blocker = albaran_blocker(order)
        if blocker is not None:
            extra["albaran_skipped"] = blocker[1]
        else:
            from app.integrations.factusol.jobs import (  # noqa: PLC0415
                enqueue_create_order_albaran,
            )

            try:
                extra["albaran_job_id"] = enqueue_create_order_albaran(
                    order.id, actor.id,
                )
            except Exception as exc:  # noqa: BLE001 — Redis caído, etc.
                extra["albaran_error"] = (
                    "No se pudo encolar el albarán en FACTUSOL "
                    f"(reintenta desde la ficha): {str(exc)[:200]}"
                )
                logger.warning("fase2: no se pudo encolar el albarán del pedido %s",
                               order.order_number, exc_info=True)
    _audit_fase2(session, order, actor, extra, resolved_payment)
    return extra


def _audit_fase2(
    session: Session, order: Order, actor: User, extra: dict[str, Any],
    resolved_payment: dict[str, Any] | None,
) -> None:
    from app.core.audit import record_event  # noqa: PLC0415

    record_event(
        session, action="erp.factusol_albaran_requested", target_type="order",
        target_id=order.id, actor=actor,
        metadata={
            "order_number": order.order_number,
            "albaran_job_id": extra.get("albaran_job_id"),
            "albaran_skipped": extra.get("albaran_skipped"),
            "albaran_error": extra.get("albaran_error"),
            "payment": resolved_payment,
        },
        message=(
            f"Pedido {order.order_number}: "
            + ("albarán FACTUSOL encolado" if extra.get("albaran_job_id")
               else "sin albarán")
            + (
                "; pago apuntado" if resolved_payment and resolved_payment.get("paid")
                else "; sin pago" if resolved_payment else ""
            )
        ),
    )


def _factusol_order_http_error(exc: Exception) -> HTTPException:
    from app.erp.orders_from_factusol import (  # noqa: PLC0415
        AlreadyImported,
        CustomerUnlinked,
        DocumentNotFound,
        UnsupportedDocumentType,
    )

    if isinstance(exc, DocumentNotFound):
        return HTTPException(status.HTTP_404_NOT_FOUND,
                             {"code": exc.code, "detail": str(exc)})
    if isinstance(exc, UnsupportedDocumentType):
        return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                             {"code": exc.code, "detail": str(exc)})
    if isinstance(exc, CustomerUnlinked):
        return HTTPException(status.HTTP_409_CONFLICT, {
            "code": exc.code, "detail": str(exc),
            "codcli": exc.codcli, "cliente_nombre": exc.nombre,
        })
    if isinstance(exc, AlreadyImported):
        return HTTPException(status.HTTP_409_CONFLICT, {
            "code": exc.code, "detail": str(exc),
            "order_id": exc.order_id, "order_number": exc.order_number,
        })
    return HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc)[:200])


def _forma_pago_nombre(client, ejercicio: str, codigo: Any) -> str | None:  # noqa: ANN001
    """Nombre de la forma de pago (catálogo F_FPA), best-effort."""
    if not str(codigo or "").strip():
        return None
    try:
        from app.erp.api.factusol import _fop_names  # noqa: PLC0415
        from app.integrations.factusol.catalogs import resolve_name  # noqa: PLC0415

        return resolve_name(_fop_names(client, ejercicio), codigo)
    except Exception:  # noqa: BLE001 — informativo, nunca tumba el alta
        return None


@router.get("/from-factusol/preview")
def preview_order_from_factusol(
    doc_type: str = Query(pattern="^(presupuestos|pedidos)$"),
    serie: int = Query(ge=1, le=9),
    codigo: int = Query(ge=1),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_edit),
) -> dict[str, Any]:
    """Fase 1 — lee el presupuesto / pedido de cliente de FACTUSOL y devuelve
    cómo quedaría el pedido (cliente resuelto contra el CRM, líneas, importes,
    forma de pago, nº de pedido, si ya se importó). No escribe nada."""
    _ = current_user
    from app.erp.api.factusol import _client_and_ejercicio  # noqa: PLC0415
    from app.erp.orders_from_factusol import (  # noqa: PLC0415
        FactusolOrderError,
        preview_factusol_document,
    )
    from app.integrations.factusol.client import FactusolError  # noqa: PLC0415

    client, ejercicio = _client_and_ejercicio(session)
    try:
        preview = preview_factusol_document(
            session, client, doc_type=doc_type, serie=serie, codigo=codigo,
            ejercicio=ejercicio,
        )
    except FactusolOrderError as exc:
        raise _factusol_order_http_error(exc) from exc
    except FactusolError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, {
            "code": "factusol_detail_failed", "detail": str(exc)[:200],
        }) from exc
    preview["forma_pago_nombre"] = _forma_pago_nombre(
        client, ejercicio, preview.get("forma_pago"),
    )
    return preview


@router.post("/from-factusol", status_code=201)
def create_order_from_factusol(
    payload: OrderFromFactusolIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_edit),
) -> dict[str, Any]:
    """Fase 1 — crea el pedido de BoHub a partir de un presupuesto o pedido de
    cliente que YA existe en FACTUSOL: cliente (por el vínculo CODCLI ↔ CRM o
    `company_id`), líneas con artículo/descripción/cantidad/precio/dto,
    importes y forma de pago (informativa). Origen `factusol_proforma` /
    `factusol_pedido` (nunca Woo). SOLO LECTURA en FACTUSOL: ni albarán ni
    pedido ni pago (Fase 2). Dedup por nº de documento (409)."""
    from app.erp.api.factusol import _client_and_ejercicio  # noqa: PLC0415
    from app.erp.orders_from_factusol import (  # noqa: PLC0415
        FactusolOrderError,
        create_order_from_factusol_document,
        preview_factusol_document,
    )
    from app.integrations.factusol.client import FactusolError  # noqa: PLC0415

    client, ejercicio = _client_and_ejercicio(session)
    resolved_payment = _resolve_payment_or_400(session, payload.payment)
    try:
        header = preview_factusol_document(
            session, client, doc_type=payload.doc_type, serie=payload.serie,
            codigo=payload.codigo, ejercicio=ejercicio,
        )
        order = create_order_from_factusol_document(
            session, client, doc_type=payload.doc_type, serie=payload.serie,
            codigo=payload.codigo, ejercicio=ejercicio,
            actor_user_id=current_user.id, company_id=payload.company_id,
            contact_id=payload.contact_id,
            forma_pago_nombre=_forma_pago_nombre(
                client, ejercicio, header.get("forma_pago"),
            ),
        )
    except FactusolOrderError as exc:
        raise _factusol_order_http_error(exc) from exc
    except FactusolError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, {
            "code": "factusol_detail_failed", "detail": str(exc)[:200],
        }) from exc
    session.commit()
    extra = _fase2_after_create(
        session, order, resolved_payment=resolved_payment,
        create_albaran=payload.create_albaran, actor=current_user,
    )
    session.commit()
    return {
        **_serialise_detail(session, _get_order(session, order.id), current_user),
        **extra,
    }


@router.post("/{order_id}/albaran", status_code=202)
def create_order_albaran(
    order_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_edit),
) -> dict[str, Any]:
    """Fase 2 — (re)encola la creación del albarán FACTUSOL del pedido en
    `factusol:writes` (202 + job_id; estado por `/factusol/quotes/status/`).
    Idempotente: 409 si ya tiene albarán; 409 si es un pedido web (el albarán
    lo crea WooCommerce) o no procede de un documento de FACTUSOL."""
    from app.erp.factusol_albaran import albaran_blocker  # noqa: PLC0415
    from app.integrations.factusol.jobs import enqueue_create_order_albaran  # noqa: PLC0415

    order = _get_order(session, order_id)
    # Tarea A: un pedido MANUAL también puede tenerlo (desde sus líneas) si
    # tiene líneas y una empresa vinculada a F_CLI; los web nunca.
    blocker = albaran_blocker(order, session)
    if blocker is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, {
            "code": blocker[0], "detail": blocker[1],
        })
    if order.factusol_albaran_number:
        raise HTTPException(status.HTTP_409_CONFLICT, {
            "code": "already_has_albaran",
            "detail": f"El pedido ya tiene el albarán {order.factusol_albaran_number}.",
            "numero": order.factusol_albaran_number,
        })
    try:
        job_id = enqueue_create_order_albaran(order.id, current_user.id)
    except Exception as exc:  # noqa: BLE001 — Redis caído, etc.
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, {
            "code": "queue_unavailable", "detail": str(exc)[:200],
        }) from exc
    _audit_fase2(
        session, order, current_user,
        {"albaran_job_id": job_id, "albaran_skipped": None, "albaran_error": None},
        None,
    )
    session.commit()
    return {"job_id": job_id, "order_id": order.id, "status": "queued"}


@router.get("/{order_id}")
def get_order(
    order_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    return _serialise_detail(session, _get_order(session, order_id), current_user)


@router.post("/{order_id}/transitions")
def fire_transition(
    order_id: str,
    payload: TransitionIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    """Dispara una transición vía engine. El gate aquí es de VISTA — la
    matriz fina por arco la aplica el engine (role_forbidden → 403)."""
    order = _get_order(session, order_id)
    try:
        domain = StatusDomain(payload.domain)
    except ValueError as exc:
        raise HTTPException(400, f"domain inválido: {payload.domain!r}") from exc
    try:
        apply_transition(
            session, order=order, domain=domain, to_status=payload.to_status,
            actor=current_user, reason=payload.reason, evidence=payload.evidence,
        )
    except TransitionError as exc:
        http = {
            "invalid_transition": 409,
            "role_forbidden": 403,
            "guard_failed": 409,
            "evidence_missing": 422,
        }.get(exc.code, 400)
        raise HTTPException(http, {"code": exc.code, "detail": exc.detail}) from exc
    session.commit()
    return _serialise_detail(session, _get_order(session, order_id), current_user)


@router.post("/{order_id}/approve")
def approve_order(
    order_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_approve),
) -> dict[str, Any]:
    """Cola PEDIDOS: valida que no hay bloqueos, marca approved_at y pasa
    preparation pending_review → in_queue (vía engine)."""
    order = _get_order(session, order_id)
    blockers = _blockers(session, order)
    if blockers:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "blocked", "blockers": blockers},
        )
    try:
        apply_transition(
            session, order=order, domain=StatusDomain.PREPARATION,
            to_status="in_queue", actor=current_user,
            reason="aprobado en Cola PEDIDOS",
        )
    except TransitionError as exc:
        raise HTTPException(
            409, {"code": exc.code, "detail": exc.detail}
        ) from exc
    order.approved_at = datetime.now(UTC)
    order.approved_by_user_id = current_user.id
    session.commit()
    return _serialise_detail(session, _get_order(session, order_id), current_user)


# --- completado (solo BoHub, reversible) --------------------------------------

#: Estados que cuentan como «facturado» / «enviado» para los AVISOS de completar.
_COMPLETION_INVOICED = {"generated", "invoiced_by_erp", "already_invoiced_externally"}
_COMPLETION_SHIPPED = {"in_transit", "delivered", "already_shipped_externally"}


def completion_avisos(order: Order) -> list[str]:
    """Avisos NO bloqueantes al marcar completado (Bart manda): no se exige que
    Transporte esté «enviado» en BoHub (el envío puede tramitarse fuera) ni que
    haya factura; solo se avisa para que no sea por error."""
    avisos: list[str] = []
    if not (
        _status_value(order.invoice_status) in _COMPLETION_INVOICED
        or order.factusol_invoice_number
    ):
        avisos.append("aún sin facturar")
    if _status_value(order.transport_status) not in _COMPLETION_SHIPPED:
        avisos.append("el envío no consta como enviado en BoHub")
    return avisos


def mark_order_completed(session: Session, order: Order, actor: User) -> bool:
    """La lógica de «Marcar completado» (#390), compartida por el botón
    individual y el masivo: sella `completed_at` + `completed_by_user_id`
    SOLO en BoHub (no toca WooCommerce ni FACTUSOL ni los 4 estados).
    Idempotente: el ya completado conserva fecha y quién. Devuelve si YA lo
    estaba. No hace commit (lo decide el caller)."""
    if order.completed_at is not None:
        return True
    order.completed_at = datetime.now(UTC)
    order.completed_by_user_id = actor.id
    return False


@router.post("/{order_id}/complete")
def complete_order(
    order_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_edit),
) -> dict[str, Any]:
    """«Marcar completado»: estado FINAL del pedido (ya facturado y enviado,
    aunque el envío se tramite fuera de BoHub). SOLO BoHub: no toca WooCommerce
    ni FACTUSOL ni los 4 estados. Manual, reversible (`/uncomplete`) e
    idempotente: el ya completado conserva fecha y quién. Devuelve la ficha +
    `completion_avisos` (no bloqueantes)."""
    order = _get_order(session, order_id)
    already = mark_order_completed(session, order, current_user)
    if not already:
        session.commit()
    order = _get_order(session, order_id)
    return {
        **_serialise_detail(session, order, current_user),
        "already_completed": already,
        "completion_avisos": completion_avisos(order),
    }


class BulkCompleteIn(BaseModel):
    order_ids: list[str] = Field(min_length=1, max_length=500)


@router.post("/bulk-complete")
def bulk_complete_orders(
    payload: BulkCompleteIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_edit),
) -> dict[str, Any]:
    """«Completar seleccionados» de la bandeja: la MISMA lógica que
    `/complete` (`mark_order_completed`) aplicada a cada pedido seleccionado.
    Solo BoHub, reversible (`/uncomplete` uno a uno), idempotente (los ya
    completados se cuentan aparte y conservan fecha y quién). No exige factura
    ni envío: cada fila trae sus `completion_avisos` («aún sin facturar»…).
    Cada pedido se confirma por separado: si uno falla (no existe, error al
    guardar) se informa en `failed` y se sigue con el resto — nunca se aborta
    todo por uno."""
    completed: list[str] = []
    already: list[str] = []
    failed: list[dict[str, str]] = []
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for order_id in payload.order_ids:
        if order_id in seen:
            continue
        seen.add(order_id)
        try:
            order = session.get(Order, order_id)
            if order is None:
                failed.append({"order_id": order_id, "error": "El pedido no existe."})
                continue
            was_already = mark_order_completed(session, order, current_user)
            session.commit()
        except Exception as exc:  # noqa: BLE001 — se informa y se sigue
            session.rollback()
            logger.warning("bulk-complete: pedido %s KO: %s", order_id, exc)
            failed.append({"order_id": order_id, "error": str(exc)[:200]})
            continue
        order = _get_order(session, order_id)
        (already if was_already else completed).append(order.id)
        items.append({
            **_serialise_summary(order, customer_names(session, [order]).get(order.id)),
            "already_completed": was_already,
            "completion_avisos": completion_avisos(order),
        })
    return {
        "ok": not failed,
        "completed": len(completed),
        "already_completed": len(already),
        "failed": failed,
        "sin_facturar": sum(
            1 for it in items if "aún sin facturar" in it["completion_avisos"]
        ),
        "items": items,
    }


@router.post("/{order_id}/uncomplete")
def uncomplete_order(
    order_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_edit),
) -> dict[str, Any]:
    """«Desmarcar completado»: revierte `/complete`. Idempotente. Solo BoHub."""
    order = _get_order(session, order_id)
    already = order.completed_at is None
    if not already:
        order.completed_at = None
        order.completed_by_user_id = None
        session.commit()
    return {
        **_serialise_detail(session, _get_order(session, order_id), current_user),
        "already_uncompleted": already,
    }


# --- procesado externamente (B-2-fix4) --------------------------------------


class MarkExternalIn(BaseModel):
    note: str | None = Field(default=None, max_length=2000)


class BulkMarkExternalIn(BaseModel):
    order_ids: list[str] = Field(default_factory=list)
    # Alternativa al listado explícito: marcar por tienda (+ opcional
    # `before_date` ISO) — la limpieza one-off de los pedidos migrados.
    store_id: str | None = None
    before_date: datetime | None = None
    note: str | None = Field(default=None, max_length=2000)


@router.post("/{order_id}/mark-externally-processed")
def mark_externally_processed(
    order_id: str,
    payload: MarkExternalIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_edit),
) -> dict[str, Any]:
    from app.erp.external_processing import mark_order_externally_processed  # noqa: PLC0415

    order = _get_order(session, order_id)
    mark_order_externally_processed(
        session, order=order, actor=current_user, note=payload.note,
    )
    session.commit()
    return _serialise_detail(session, _get_order(session, order_id), current_user)


@router.post("/bulk-mark-externally-processed")
def bulk_mark_externally_processed(
    payload: BulkMarkExternalIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_edit),
) -> dict[str, Any]:
    """Marca en bloque. Acepta `order_ids` explícitos O un filtro
    (`store_id` + opcional `before_date`) para la limpieza one-off de los
    pedidos migrados de Excel/proceso anterior."""
    from app.erp.external_processing import mark_order_externally_processed  # noqa: PLC0415

    stmt = select(Order).options(selectinload(Order.lines))
    if payload.order_ids:
        stmt = stmt.where(Order.id.in_(payload.order_ids))
    elif payload.store_id:
        stmt = stmt.where(Order.store_id == payload.store_id)
        if payload.before_date:
            stmt = stmt.where(Order.placed_at < payload.before_date)
    else:
        raise HTTPException(400, "Indica `order_ids` o `store_id`.")
    marked = 0
    for order in session.scalars(stmt):
        if mark_order_externally_processed(
            session, order=order, actor=current_user, note=payload.note,
        ):
            marked += 1
    session.commit()
    return {"ok": True, "marked": marked}


# --- FACTUSOL: emisión de factura (Fase C · C-2) ----------------------------


def _rq_job_status(job_id: str) -> dict[str, Any] | None:
    """Estado del job RQ de emisión (best-effort). None si no se puede
    consultar (sin Redis, en local/tests)."""
    try:
        from redis import Redis  # noqa: PLC0415
        from rq.job import Job  # noqa: PLC0415

        from app.core.config import get_settings  # noqa: PLC0415

        conn = Redis.from_url(get_settings().redis_url)
        job = Job.fetch(job_id, connection=conn)
        rq_status = job.get_status(refresh=True)
        if rq_status == "failed":
            return {"status": "failed",
                    "error": (job.exc_info or "emisión fallida")[-400:]}
        return {"status": "pending"}
    except Exception:  # noqa: BLE001 — sin Redis o job caducado → desconocido
        return None


class EmitFactusolInvoicePayload(BaseModel):
    """Opciones de emisión elegidas en el modal (como el diálogo «Nueva
    factura» del escritorio FACTUSOL). Todas opcionales con defaults sensatos:
    un POST sin cuerpo emite con tipo '1' y fecha de hoy."""

    #: ERP-E2-fix2 — empresa emisora. Se escribe en `TIPFAC` y decide el
    #: contador del CODFAC. None → la hereda el service del pedido en FACTUSOL
    #: (`TIPPCL`) y, si no está allí, de los ajustes.
    #:
    #: Ya NO existe un campo `tipfac`: era un malentendido («1 = factura
    #: ordinaria») que sellaba TODAS las facturas como serie 1 = Bomedia.
    serie: int | None = Field(default=None, ge=1, le=9)
    #: Fecha de emisión ISO (`YYYY-MM-DD`); None → hoy (lo pone el service).
    fecfac: str | None = Field(default=None, max_length=10)
    fopfac: str | None = Field(default=None, max_length=10)
    comfac: str | None = Field(default=None, max_length=500)


@router.post("/{order_id}/emit-factusol-invoice", status_code=202)
def emit_factusol_invoice(
    order_id: str,
    payload: EmitFactusolInvoicePayload | None = Body(default=None),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_edit),
) -> dict[str, Any]:
    """Encola la emisión REAL de la factura en FACTUSOL (cola serializada
    `factusol:writes`). Rechaza doble facturación. El cuerpo es opcional: sin
    él se emite con las opciones por defecto (tipo '1', fecha de hoy)."""
    order = _get_order(session, order_id)
    inv = _status_value(order.invoice_status)
    if order.factusol_invoice_number or inv == InvoiceStatus.INVOICED_BY_ERP.value:
        raise HTTPException(status.HTTP_409_CONFLICT, {
            "code": "already_invoiced_by_erp",
            "detail": "El pedido ya tiene factura en FACTUSOL",
            "codfac": order.factusol_invoice_number,
        })
    if inv == InvoiceStatus.ALREADY_INVOICED_EXTERNALLY.value:
        raise HTTPException(status.HTTP_409_CONFLICT, {
            "code": "already_invoiced_externally",
            "detail": "El pedido está marcado como facturado fuera del ERP",
        })

    from app.integrations.factusol.jobs import enqueue_emit_invoice  # noqa: PLC0415

    options = (payload or EmitFactusolInvoicePayload()).model_dump()
    try:
        job_id = enqueue_emit_invoice(order.id, current_user.id, options=options)
    except Exception as exc:  # noqa: BLE001 — Redis caído, etc.
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, {
            "code": "queue_unavailable", "detail": str(exc)[:200],
        }) from exc

    _audit_factusol(session, order, current_user, job_id)
    session.commit()
    return {"job_id": job_id, "order_id": order.id, "status": "queued"}


# --- cobro manual (F-4-B desde la app): ficha y bandeja ---------------------------


class CobrosRefreshIn(BaseModel):
    """«Actualizar cobros FACTUSOL» de la bandeja: pedidos a comprobar. Vacío
    = todos los visibles con factura (hasta 500)."""

    ids: list[str] = Field(default_factory=list, max_length=500)


def _factusol_cobro_client(session: Session):  # noqa: ANN202
    from app.erp.api.factusol import _fop_names  # noqa: PLC0415
    from app.integrations.factusol.client import FactusolClient  # noqa: PLC0415
    from app.integrations.factusol.service import ejercicio_for  # noqa: PLC0415

    client = FactusolClient.from_settings()
    ejercicio = ejercicio_for(session)
    return client, ejercicio, _fop_names(client, ejercicio)


@router.post("/factusol-cobros/refresh")
def refresh_factusol_cobros(
    payload: CobrosRefreshIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    """Comprueba EN VIVO el estado de cobro FACTUSOL (ESTFAC / saldo en F_LCO)
    de los pedidos con factura y lo deja persistido para la bandeja
    («Cobrado FACTUSOL» / «Pendiente de cobro»). F_FAC y F_LCO se leen UNA
    sola vez para todos. Solo lectura en FACTUSOL."""
    _ = current_user
    from app.erp.factusol_cobro import refresh_orders_cobro  # noqa: PLC0415
    from app.integrations.factusol.client import FactusolError  # noqa: PLC0415

    stmt = select(Order).where(Order.factusol_invoice_number.isnot(None))
    if payload.ids:
        stmt = stmt.where(Order.id.in_(payload.ids))
    else:
        stmt = worklist_visible(stmt).where(Order.externally_processed_at.is_(None))
    orders = list(session.scalars(stmt.order_by(Order.placed_at.desc()).limit(500)))
    try:
        client, ejercicio, fop_names = _factusol_cobro_client(session)
        results = refresh_orders_cobro(
            session, client, orders, ejercicio, fop_names=fop_names,
        )
    except FactusolError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, {
            "code": "factusol_unreachable", "detail": str(exc)[:200],
        }) from exc
    except Exception as exc:  # noqa: BLE001 — sin credenciales / config
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, {
            "code": "factusol_unavailable", "detail": str(exc)[:200],
        }) from exc
    session.commit()
    names = customer_names(session, orders)
    return {
        "checked": len(results),
        "items": [
            {**_serialise_summary(o, names.get(o.id)), "cobro": results.get(o.id)}
            for o in orders
        ],
    }


@router.get("/{order_id}/factusol-cobro")
def order_factusol_cobro(
    order_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    """Estado de cobro EN VIVO de la factura del pedido, para la ficha y el
    modal «Registrar cobro en FACTUSOL»: clave compuesta de la factura,
    total / cobrado / saldo / ESTFAC, nº de líneas de cobro (aviso de posible
    doble cobro), forma de pago y cuenta sugerida. Queda persistido en el
    pedido (bandeja). Sin factura → 200 `sin_factura` (el botón se
    deshabilita, sin error). El cobro en sí se registra con el endpoint F-4-B
    de la factura (`POST /factusol/documents/facturas/{serie}/{codigo}/collection`)."""
    _ = current_user
    from app.erp.factusol_cobro import order_cobro_info  # noqa: PLC0415
    from app.integrations.factusol.client import FactusolError  # noqa: PLC0415

    order = _get_order(session, order_id)
    base = {"order_id": order.id, "order_number": order.order_number}
    if not order.factusol_invoice_number:
        return {
            **base, "status": "sin_factura", "invoice": None,
            "detail": "El pedido aún no tiene factura en FACTUSOL: emite la factura primero.",
        }
    try:
        client, ejercicio, fop_names = _factusol_cobro_client(session)
        info = order_cobro_info(session, client, order, ejercicio, fop_names=fop_names)
    except FactusolError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, {
            "code": "factusol_unreachable", "detail": str(exc)[:200],
        }) from exc
    except Exception as exc:  # noqa: BLE001 — sin credenciales / config
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, {
            "code": "factusol_unavailable", "detail": str(exc)[:200],
        }) from exc
    session.commit()
    return {**base, **info, "persisted_status": order.factusol_cobro_status}


@router.get("/{order_id}/factusol-status")
def factusol_status(
    order_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    """Estado del pedido frente a FACTUSOL (C-2-fix2): ¿ya tiene factura o
    albarán? Consulta EN VIVO F_FAC (por REFFAC) y F_ALB (por REFALB). Si la
    factura ya existe, la **auto-vincula** al pedido para evitar duplicados.

    Solo hace la consulta en vivo si `factusol_live` está activo; si no, o si
    FACTUSOL no responde, devuelve `status: "unknown"` y el frontend cae al
    botón de emisión manual (cuyo worker reconfirma antes de escribir)."""
    order = _get_order(session, order_id)
    if order.factusol_invoice_number:
        return {"status": "invoiced", "codfac": order.factusol_invoice_number,
                "auto_linked": False}
    inv = _status_value(order.invoice_status)
    if inv == InvoiceStatus.ALREADY_INVOICED_EXTERNALLY.value:
        return {"status": "already_invoiced_externally"}
    if not _factusol_live(session):
        return {"status": "unknown", "reason": "factusol_live_off"}

    from app.integrations.factusol.client import FactusolClient  # noqa: PLC0415
    from app.integrations.factusol.service import (  # noqa: PLC0415
        _store_ref_prefix,
        ejercicio_for,
        get_and_link_factusol_status,
    )

    try:
        client = FactusolClient.from_settings()
        ejercicio = ejercicio_for(session)
        ref_prefix = _store_ref_prefix(session, order)
        return get_and_link_factusol_status(
            session, order, client, ejercicio,
            ref_prefix=ref_prefix, actor=current_user,
        )
    except Exception as exc:  # noqa: BLE001 — FACTUSOL caído / sin credenciales
        logger.warning("factusol status check falló order=%s: %s", order_id, exc)
        return {"status": "unknown", "reason": "factusol_unreachable"}


class OrderLanguageIn(BaseModel):
    """E4-fix1 — idioma del pedido, editable en la ficha (corrige una
    detección fallida y queda persistido)."""

    language: str | None = Field(default=None, pattern="^(es|en|de|fr|nl)$")


@router.patch("/{order_id}/language")
def update_order_language(
    order_id: str,
    payload: OrderLanguageIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_edit),
) -> dict[str, Any]:
    _ = current_user
    order = _get_order(session, order_id)
    order.language = payload.language
    session.commit()
    return {"id": order.id, "language": order.language}


class SeguimientoFieldsIn(BaseModel):
    """ERP-F6 — campos del seguimiento editables desde la ficha. `orden` NO
    es un campo del modelo: su contenido (los comentarios de la columna
    «Orden» del Excel) se AÑADE a las observaciones del pedido."""

    serial_number: str | None = Field(default=None, max_length=2000)
    whiterip_license: str | None = Field(default=None, max_length=64)
    shipping_origin: str | None = Field(default=None, max_length=40)
    orden: str | None = Field(default=None, max_length=2000)


@router.patch("/{order_id}/seguimiento")
def update_seguimiento_fields(
    order_id: str,
    payload: SeguimientoFieldsIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_edit),
) -> dict[str, Any]:
    _ = current_user
    order = _get_order(session, order_id)
    data = payload.model_dump(exclude_unset=True)
    for field in ("serial_number", "whiterip_license", "shipping_origin"):
        if field in data:
            value = (data[field] or "").strip()
            setattr(order, field, value or None)
    if "orden" in data and (data["orden"] or "").strip():
        nota = data["orden"].strip()
        order.notes = f"{order.notes}\n{nota}" if order.notes else nota
    session.commit()
    return {
        "id": order.id,
        "serial_number": order.serial_number,
        "whiterip_license": order.whiterip_license,
        "shipping_origin": order.shipping_origin,
        "notes": order.notes,
    }


@router.get("/{order_id}/factusol-invoice-ref")
def order_factusol_invoice_ref(
    order_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    """ERP-F1 — localiza en FACTUSOL la factura del pedido y devuelve su
    clave compuesta `{serie, codigo}`, para que la ficha del pedido use el
    MISMO flujo de email que el detalle de la factura (sin duplicarlo). El
    pedido solo guarda el CODFAC; la serie (TIPFAC) sale de F_FAC por REFFAC.
    404 si el pedido aún no tiene factura en FACTUSOL."""
    _ = current_user
    from app.integrations.factusol.client import (  # noqa: PLC0415
        FactusolClient,
        FactusolError,
    )
    from app.integrations.factusol.service import (  # noqa: PLC0415
        _store_ref_prefix,
        check_factusol_status,
        coerce_serie,
        ejercicio_for,
    )

    order = _get_order(session, order_id)
    try:
        client = FactusolClient.from_settings()
        ejercicio = ejercicio_for(session)
        status_info = check_factusol_status(
            client, order, ejercicio, ref_prefix=_store_ref_prefix(session, order),
        )
    except FactusolError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, {
            "code": "factusol_unreachable", "detail": str(exc)[:200],
        }) from exc
    except Exception as exc:  # noqa: BLE001 — sin credenciales / config
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, {
            "code": "factusol_unavailable", "detail": str(exc)[:200],
        }) from exc
    factura = status_info.get("factura")
    if not factura:
        raise HTTPException(status.HTTP_404_NOT_FOUND, {
            "code": "invoice_not_in_factusol",
            "detail": "Este pedido aún no tiene factura en FACTUSOL.",
        })
    serie = coerce_serie(factura.get("TIPFAC"))
    codigo = _int_or_none_local(factura.get("CODFAC"))
    if serie is None or codigo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, {
            "code": "invoice_key_unresolved",
            "detail": "La factura en FACTUSOL no trae serie/número utilizables.",
        })
    return {"serie": serie, "codigo": codigo,
            "numero": f"{serie}-{codigo:06d}"}


def _int_or_none_local(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _web_pcl_missing_detail(
    session: Session, client: Any, order: Order, *, ref: str, ejercicio: str,
    probe: Callable[[Any, str, str], list[str]],
) -> dict[str, Any]:
    """Cuerpo del 404 `pedido_not_in_factusol` de un pedido WEB cuyo F_PCL no
    aparece por `REFPCL`. Dice QUÉ referencia se buscó (el prefijo de tienda
    o el derivado del número: `FLU-005789`) y, si en F_PCL hay ese mismo nº
    Woo bajo otro prefijo (`FLE-005789`), qué prefijo configurar. El sondeo
    es solo diagnóstico: nunca elige el documento por el operador."""
    from app.models.integration_settings import IntegrationAccount  # noqa: PLC0415

    candidates = probe(client, order.order_number, ejercicio)
    detail = (
        f"Este pedido aún no existe en FACTUSOL: ningún pedido de cliente con "
        f"referencia {ref} (ejercicio {ejercicio})."
    )
    if candidates:
        store = session.get(IntegrationAccount, order.store_id) if order.store_id else None
        store_label = (
            store.display_name or store.account_id if store is not None
            else (order.order_number or "").split("-")[0]
        )
        prefix = candidates[0].rpartition("-")[0]
        detail += (
            f" Sí existe {', '.join(candidates)}: si es este pedido, configura el "
            f"prefijo de referencia «{prefix}» de la tienda {store_label} en "
            "Ajustes ERP (Serie de facturación → Prefijo referencia FACTUSOL)."
        )
    return {
        "code": "pedido_not_in_factusol", "detail": detail,
        "ref": ref, "ejercicio": ejercicio, "candidates": candidates,
    }


@router.get("/{order_id}/factusol-pedido-pdf")
def order_factusol_pedido_pdf(
    order_id: str,
    lang: str = Query(default="es", pattern="^(es|en|de|fr|nl)$"),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
):
    """ERP-E4 / Fase 2 — PDF del documento de ORIGEN del pedido en FACTUSOL,
    con el motor E4 genérico por tipo (solo lectura):

    - origen proforma → el presupuesto F_PRE (serie + nº guardados en el
      pedido al crearlo, Fase 1);
    - origen pedido de cliente → el F_PCL (serie + nº guardados);
    - pedido web → el F_PCL que crea la app Woo→FACTUSOL, localizado por su
      referencia común `REFPCL` (`find_pcl_by_order`, como en E2/E4): el
      pedido web NO guarda `factusol_source`, ese es su único enlace. Si no
      aparece, el 404 dice QUÉ referencia se buscó y, si en F_PCL existe el
      mismo número bajo otro prefijo (`FLE-005789` vs `FLU-005789`), qué
      prefijo hay que configurar para la tienda — nunca se adivina el
      documento (un homónimo de otra tienda comparte número);
    - sin documento de origen (alta manual) → 404 `pedido_not_in_factusol`
      sin consultar FACTUSOL (la ficha ya deshabilita el botón).

    Un pedido creado desde proforma NO tiene F_PCL: buscarlo por REFPCL era
    lo que fallaba con «No se pudo generar el PDF del pedido FACTUSOL»."""
    _ = current_user
    from fastapi import Response  # noqa: PLC0415

    from app.erp.api.factusol import _fop_names  # noqa: PLC0415
    from app.erp.factusol_pdf import (  # noqa: PLC0415
        company_for_serie,
        extract_document_data,
        generate_document_pdf,
        load_raw_document,
        logo_path_for_serie,
        pdf_filename,
    )
    from app.integrations.factusol.client import (  # noqa: PLC0415
        FactusolClient,
        FactusolError,
    )
    from app.integrations.factusol.documents import visible_number  # noqa: PLC0415
    from app.integrations.factusol.service import (  # noqa: PLC0415
        _store_ref_prefix,
        ejercicio_for,
        find_pcl_by_order,
        probe_pcl_refs_by_number,
        serie_of_row,
    )

    order = _get_order(session, order_id)
    document = _factusol_document(session, order)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, {
            "code": "pedido_not_in_factusol",
            "detail": (
                "Este pedido no procede de ningún documento de FACTUSOL: "
                "no hay PDF que generar."
            ),
        })
    doc_type = document["doc_type"]
    try:
        client = FactusolClient.from_settings()
        ejercicio = ejercicio_for(session)
        if document["by_ref"]:
            pcl = find_pcl_by_order(
                client, order, ejercicio,
                ref_prefix=_store_ref_prefix(session, order),
            )
            if pcl is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, _web_pcl_missing_detail(
                    session, client, order, ref=document["ref"], ejercicio=ejercicio,
                    probe=probe_pcl_refs_by_number,
                ))
            serie = serie_of_row(pcl, "TIPPCL")
            codigo = int(str(pcl.get("CODPCL")).strip())
            if serie is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, {
                    "code": "pedido_sin_serie",
                    "detail": "El pedido en FACTUSOL no trae serie utilizable.",
                })
        else:
            serie, codigo = int(document["serie"]), int(document["codigo"])
        raw = load_raw_document(
            client, doc_type, serie=serie, codigo=codigo, ejercicio=ejercicio,
        )
        if raw is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, {
                "code": "document_not_in_factusol",
                "detail": (
                    f"El {document['label']} {visible_number(serie, codigo)} ya "
                    f"no existe en FACTUSOL (ejercicio {ejercicio})."
                ),
            })
        data = extract_document_data(
            client, doc_type, raw[0], raw[1], ejercicio=ejercicio,
            fop_names=_fop_names(client, ejercicio),
        )
    except FactusolError as exc:
        logger.warning("factusol pedido-pdf KO order=%s: %s", order_id, exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, {
            "code": "factusol_pdf_failed", "detail": str(exc)[:200],
        }) from exc
    pdf = generate_document_pdf(
        data, company=company_for_serie(session, serie), lang=lang,
        logo=logo_path_for_serie(serie),
    )
    filename = pdf_filename(doc_type, data, lang)
    return Response(
        content=pdf, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{order_id}/factusol-albaran-pdf")
def order_factusol_albaran_pdf(
    order_id: str,
    lang: str = Query(default="es", pattern="^(es|en|de|fr|nl)$"),
    variant: str | None = Query(default=None, pattern="^(valorado|devolucion)$"),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
):
    """Fase 2 — PDF del albarán que BoHub creó en FACTUSOL para este pedido
    (`orders.factusol_albaran_number`, `serie-código`), con el MISMO motor E4
    que «PDF del pedido (FACTUSOL)» y los PDF de factura: la API de DELSOL no
    imprime, BoHub compone el PDF a partir de F_ALB + F_LAL (solo lectura,
    clave compuesta serie+código, ejercicio activo). `variant` opcional:
    albarán valorado / de devolución. 404 con código propio si el pedido no
    tiene albarán o ese albarán ya no existe en FACTUSOL; 502 si FACTUSOL no
    responde. Nunca escribe nada."""
    _ = current_user
    from fastapi import Response  # noqa: PLC0415

    from app.erp.api.factusol import _fop_names  # noqa: PLC0415
    from app.erp.factusol_pdf import (  # noqa: PLC0415
        company_for_serie,
        extract_document_data,
        generate_document_pdf,
        load_raw_document,
        logo_path_for_serie,
        pdf_filename,
    )
    from app.integrations.factusol.client import (  # noqa: PLC0415
        FactusolClient,
        FactusolError,
    )
    from app.integrations.factusol.service import (  # noqa: PLC0415
        coerce_serie,
        ejercicio_for,
    )

    order = _get_order(session, order_id)
    numero = str(order.factusol_albaran_number or "").strip()
    if not numero:
        raise HTTPException(status.HTTP_404_NOT_FOUND, {
            "code": "albaran_not_in_bohub",
            "detail": "Este pedido no tiene albarán FACTUSOL creado por BoHub.",
        })
    head, _sep, tail = numero.partition("-")
    serie, codigo = coerce_serie(head), _int_or_none_local(tail)
    if serie is None or codigo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, {
            "code": "albaran_number_invalid",
            "detail": f"El nº de albarán del pedido no es válido: {numero!r}.",
        })
    try:
        client = FactusolClient.from_settings()
        ejercicio = ejercicio_for(session)
        raw = load_raw_document(
            client, "albaranes", serie=serie, codigo=codigo, ejercicio=ejercicio,
        )
        if raw is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, {
                "code": "albaran_not_in_factusol",
                "detail": (
                    f"El albarán {numero} ya no existe en FACTUSOL "
                    f"(ejercicio {ejercicio})."
                ),
            })
        data = extract_document_data(
            client, "albaranes", raw[0], raw[1], ejercicio=ejercicio,
            fop_names=_fop_names(client, ejercicio),
        )
    except FactusolError as exc:
        logger.warning("factusol albaran-pdf KO order=%s: %s", order_id, exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, {
            "code": "factusol_pdf_failed", "detail": str(exc)[:200],
        }) from exc
    pdf = generate_document_pdf(
        data, company=company_for_serie(session, serie), lang=lang,
        logo=logo_path_for_serie(serie), variant=variant,
    )
    filename = pdf_filename("albaranes", data, lang, variant)
    return Response(
        content=pdf, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{order_id}/factusol-invoice-status")
def factusol_invoice_status(
    order_id: str,
    job_id: str | None = Query(default=None),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    """Estado de la facturación del pedido para el polling del frontend."""
    _ = current_user
    order = _get_order(session, order_id)
    if order.factusol_invoice_number:
        return {"status": "invoiced",
                "codfac": order.factusol_invoice_number}
    if job_id:
        info = _rq_job_status(job_id)
        if info is not None:
            return info
    return {"status": "pending"}


def _audit_factusol(
    session: Session, order: Order, actor: User, job_id: str,
) -> None:
    try:
        from app.core.audit import record_event  # noqa: PLC0415

        record_event(
            session, action="erp.factusol_invoice_requested",
            target_type="order", target_id=order.id, actor=actor,
            metadata={"order_number": order.order_number, "job_id": job_id},
        )
    except Exception:  # noqa: BLE001 — audit nunca bloquea
        pass
