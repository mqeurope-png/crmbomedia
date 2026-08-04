"""BoHub ERP — API de pedidos: bandeja, ficha, Cola PEDIDOS, transiciones.

Fase A PR 3. Los pedidos se crean a mano (external_source='manual') para
probar el flujo end-to-end; la ingesta Woo llega en Fase B. Toda mutación
de estado pasa por el engine (PR 2) — este router no toca columnas de
estado directamente.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import not_found
from app.db.session import get_session
from app.erp.api.deps import require_erp_approve, require_erp_edit, require_erp_view
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
    product_sku: str = Field(min_length=1, max_length=128)
    product_codart: str | None = Field(default=None, max_length=13)
    description: str = Field(default="", max_length=255)
    quantity: float = Field(default=1, gt=0)
    unit_price: float = Field(default=0, ge=0)
    tax_rate: float = Field(default=21, ge=0, le=100)
    notes: str | None = None


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
    return {
        o.id: {
            "contact_name": _contact_label(contacts.get(o.contact_id)),
            "company_name": companies.get(o.company_id) if o.company_id else None,
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
        "approved_at": o.approved_at.isoformat() if o.approved_at else None,
        "placed_at": o.placed_at.isoformat() if o.placed_at else None,
        "created_at": o.created_at.isoformat(),
        "externally_processed_at": (
            o.externally_processed_at.isoformat()
            if o.externally_processed_at else None
        ),
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
    number = (payload.order_number or "").strip() or _next_manual_number(session)
    if session.scalar(select(Order.id).where(Order.order_number == number)):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"order_number ya existe: {number!r}",
        )
    total = 0.0
    order = Order(
        external_source=OrderSource.MANUAL,
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
        total += line_total
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
        reason="Pedido manual creado desde el ERP",
        metadata_json=json.dumps({
            "event": "order_created_manual",
            "origin_source": "manual",
            "created_by_user_id": current_user.id,
        }),
    ))
    session.commit()
    return _serialise_detail(session, _get_order(session, order.id), current_user)


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
    return json.dumps(data) if data else None


@router.get("")
def list_orders(
    payment: str | None = Query(default=None),
    preparation: str | None = Query(default=None),
    transport: str | None = Query(default=None),
    invoice: str | None = Query(default=None),
    store: str | None = Query(default=None),
    show_external: bool = Query(default=False),
    sort: str = Query(default="placed_desc"),
    limit: int = Query(default=100, ge=1, le=500),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    _ = current_user
    stmt = select(Order)
    if payment:
        stmt = stmt.where(Order.payment_status == payment)
    if preparation:
        stmt = stmt.where(Order.preparation_status == preparation)
    if transport:
        stmt = stmt.where(Order.transport_status == transport)
    if invoice:
        stmt = stmt.where(Order.invoice_status == invoice)
    if store:
        stmt = stmt.where(Order.store_id == store)
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
        select(Order).where(Order.preparation_status == "pending_review")
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

    tipfac: str = Field(default="1", max_length=4)
    serfac: str | None = Field(default=None, max_length=10)
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
