"""BoHub ERP — API de pedidos: bandeja, ficha, Cola PEDIDOS, transiciones.

Fase A PR 3. Los pedidos se crean a mano (external_source='manual') para
probar el flujo end-to-end; la ingesta Woo llega en Fase B. Toda mutación
de estado pasa por el engine (PR 2) — este router no toca columnas de
estado directamente.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import not_found
from app.db.session import get_session
from app.erp.api.deps import require_erp_approve, require_erp_edit, require_erp_view
from app.erp.models import (
    ErpException,
    ExceptionStatus,
    Order,
    OrderLine,
    OrderSource,
    StatusDomain,
)
from app.erp.state_machine import TransitionError, apply_transition, available_transitions
from app.models.crm import User

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


class OrderCreate(BaseModel):
    order_number: str = Field(min_length=1, max_length=32)
    contact_id: str | None = None
    company_id: str | None = None
    currency: str = Field(default="EUR", max_length=3)
    notes: str | None = None
    placed_at: datetime | None = None
    lines: list[OrderLineIn] = Field(default_factory=list, min_length=1)


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


def _serialise_summary(o: Order) -> dict[str, Any]:
    return {
        "id": o.id,
        "order_number": o.order_number,
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
        **_serialise_summary(o),
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
    # Helper conservado por si la Fase C lo aprovecha. Tras B-2-fix5 ya no
    # se llama desde el cálculo de bloqueos/warnings: el ERP confía en la
    # fuente y no valida SKU ni empresas contra FACTUSOL.
    from app.erp.models import ERP_SETTINGS_SINGLETON_ID, ErpSettings  # noqa: PLC0415

    cfg = session.get(ErpSettings, ERP_SETTINGS_SINGLETON_ID)
    return bool(cfg and cfg.factusol_live)


def _open_exception_blocker(session: Session, o: Order) -> dict[str, str] | None:
    """Excepciones abiertas de tipos operativos reales (SAT/transporte/
    facturación) — el único bloqueo de la Cola PEDIDOS (B-2-fix5)."""
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
    """Bloqueos que impiden aprobar en la Cola PEDIDOS. Solo excepciones
    abiertas de tipos operativos reales (SAT/transporte/facturación).
    El ERP confía en el pedido tal como llega de la fuente (B-2-fix5)."""
    real = _open_exception_blocker(session, o)
    return [real] if real else []


def _warnings(session: Session, o: Order) -> list[dict[str, str]]:
    """Sin warnings automáticos: el ERP confía en la fuente (B-2-fix5)."""
    return []


# --- endpoints ---------------------------------------------------------------


@router.post("", status_code=201)
def create_order(
    payload: OrderCreate,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_edit),
) -> dict[str, Any]:
    """Alta manual (external_source='manual') — el caso de prueba de la
    Fase A y los pedidos telefónicos/B2B de siempre."""
    if session.scalar(select(Order.id).where(
        Order.order_number == payload.order_number
    )):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"order_number ya existe: {payload.order_number!r}",
        )
    total = 0.0
    order = Order(
        external_source=OrderSource.MANUAL,
        order_number=payload.order_number,
        contact_id=payload.contact_id,
        company_id=payload.company_id,
        currency=payload.currency,
        notes=payload.notes,
        placed_at=payload.placed_at or datetime.now(UTC),
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
    session.commit()
    return _serialise_detail(session, _get_order(session, order.id), current_user)


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
    return {"items": [_serialise_summary(o) for o in rows]}


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
    return {
        "items": [
            {
                **_serialise_summary(o),
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
