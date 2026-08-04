"""BoHub ERP — endpoints de la Cola SAT (Fase A PR 5).

  - GET  /api/erp/sat/queue           cola priorizada (in_queue/preparing/blocked)
  - POST /api/erp/orders/{id}/report-exception   crea excepción + bloquea
  - POST /api/erp/orders/{id}/packing-info        peso/dimensiones/bultos
  - POST /api/erp/orders/{id}/attach-document     foto/PDF → DocumentStorage

La cola SAT lee directo de `orders` filtrando por estado de preparación
(decisión cerrada nº10: NO se reusa `tasks`). Reportar excepción cambia
preparation → blocked vía el engine (misma auditoría que cualquier
transición).
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import not_found
from app.db.session import get_session
from app.erp.api.deps import require_erp_view
from app.erp.models import (
    EXCEPTION_SUBTYPES,
    KIND_ALBARAN,
    KIND_ETIQUETA,
    ErpException,
    ExceptionType,
    Order,
    PreparationStatus,
    ShipmentFile,
    StatusDomain,
)
from app.erp.state_machine import TransitionError, apply_transition
from app.erp.storage import get_document_storage
from app.models.crm import User

router = APIRouter(prefix="/api/erp", tags=["erp-sat"])

#: Máximo por documento (foto de móvil ~ pocos MB; PDF de etiqueta pequeño).
MAX_DOC_BYTES = 15 * 1024 * 1024

#: Orden de prioridad de la cola SAT (bloqueados arriba para resolverlos ya,
#: luego los que están preparándose, luego los recién aprobados).
_QUEUE_ORDER = {
    PreparationStatus.BLOCKED.value: 0,
    PreparationStatus.PREPARING.value: 1,
    PreparationStatus.IN_QUEUE.value: 2,
}


class ReportExceptionIn(BaseModel):
    type: str
    subtype: str | None = None
    description: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class PackingInfoIn(BaseModel):
    weight_kg: float | None = Field(default=None, ge=0)
    dimensions_cm: str | None = Field(default=None, max_length=64)
    packages: int | None = Field(default=None, ge=1)


def _get_order(session: Session, order_id: str) -> Order:
    order = session.scalar(
        select(Order).where(Order.id == order_id).options(selectinload(Order.lines))
    )
    if order is None:
        raise not_found("Order")
    return order


def _packing(order: Order) -> dict[str, Any]:
    if not order.packing_json:
        return {}
    try:
        data = json.loads(order.packing_json)
        return data if isinstance(data, dict) else {}
    except (TypeError, ValueError):
        return {}


#: Estados de transporte que sacan un pedido de «Listos para envío» (ya salió).
_SHIPPED_TRANSPORT = ("in_transit", "delivered", "already_shipped_externally")


@router.get("/sat/queue")
def sat_queue(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    """Cola táctil del taller en 2 secciones (D-1-fix1):

    - `preparing`: por embalar (in_queue / preparing / blocked), priorizados.
    - `ready_for_pickup`: embalados (`packed`) pero aún no salidos del taller
      (transporte NO en tránsito/entregado/externalizado) — falta imprimir
      albarán/etiqueta y marcar recogido.
    """
    _ = current_user
    prep_rows = list(session.scalars(
        select(Order).where(Order.preparation_status.in_(list(_QUEUE_ORDER)))
        .options(selectinload(Order.lines))
    ))
    prep_rows.sort(key=lambda o: (
        _QUEUE_ORDER.get(getattr(o.preparation_status, "value", o.preparation_status), 9),
        o.placed_at or o.created_at,
    ))
    ready_rows = list(session.scalars(
        select(Order).where(
            Order.preparation_status == PreparationStatus.PACKED.value,
            Order.transport_status.notin_(_SHIPPED_TRANSPORT),
        ).options(selectinload(Order.lines))
        .order_by(Order.placed_at.asc())
    ))

    # Presencia de albarán/etiqueta vigentes (Fase D) — una sola query para ambos.
    files_by_order: dict[str, set[str]] = {}
    order_ids = [o.id for o in (*prep_rows, *ready_rows)]
    if order_ids:
        for oid, kind in session.execute(
            select(ShipmentFile.order_id, ShipmentFile.kind).where(
                ShipmentFile.order_id.in_(order_ids),
                ShipmentFile.replaced_at.is_(None),
            )
        ):
            files_by_order.setdefault(oid, set()).add(kind)

    # D-2: nombre del cliente en las cards del taller (el número solo no basta).
    from app.erp.api.orders import customer_names  # noqa: PLC0415

    names = customer_names(session, [*prep_rows, *ready_rows])

    def _item(o: Order) -> dict[str, Any]:
        who = names.get(o.id) or {}
        return {
            "id": o.id,
            "order_number": o.order_number,
            "contact_name": who.get("contact_name"),
            "company_name": who.get("company_name"),
            "preparation_status": getattr(o.preparation_status, "value", o.preparation_status),
            "transport_status": getattr(o.transport_status, "value", o.transport_status),
            "payment_status": getattr(o.payment_status, "value", o.payment_status),
            "total_amount": float(o.total_amount or 0),
            "currency": o.currency,
            "lines": [
                {"sku": line.product_sku, "description": line.description,
                 "quantity": float(line.quantity)}
                for line in o.lines
            ],
            "has_albaran": KIND_ALBARAN in files_by_order.get(o.id, set()),
            "has_etiqueta": KIND_ETIQUETA in files_by_order.get(o.id, set()),
        }

    return {
        "preparing": [_item(o) for o in prep_rows],
        "ready_for_pickup": [_item(o) for o in ready_rows],
    }


@router.post("/orders/{order_id}/report-exception", status_code=201)
def report_exception(
    order_id: str,
    payload: ReportExceptionIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    """SAT reporta un problema: crea la excepción y bloquea la preparación.
    VER es suficiente para reportar (SAT puede) — el bloqueo lo aplica el
    engine, que permite blocked a admin/pedidos/sat."""
    order = _get_order(session, order_id)
    try:
        etype = ExceptionType(payload.type)
    except ValueError as exc:
        raise HTTPException(400, f"type de excepción inválido: {payload.type!r}") from exc
    valid_subtypes = EXCEPTION_SUBTYPES.get(etype, set())
    if payload.subtype and valid_subtypes and payload.subtype not in valid_subtypes:
        raise HTTPException(
            400, f"subtype inválido para {etype.value}: {payload.subtype!r}"
        )

    metadata = dict(payload.metadata)
    if payload.description:
        metadata["description"] = payload.description

    exc_row = ErpException(
        type=etype, subtype=payload.subtype,
        metadata_json=json.dumps(metadata, default=str) if metadata else None,
        order_id=order.id, reported_by_user_id=current_user.id,
    )
    session.add(exc_row)

    # Bloquea la preparación si el pedido está en un estado bloqueable
    # (in_queue/preparing). Si ya está packed/blocked, se registra la
    # excepción sin forzar transición inválida.
    current = getattr(order.preparation_status, "value", order.preparation_status)
    if current in (PreparationStatus.IN_QUEUE.value, PreparationStatus.PREPARING.value):
        try:
            apply_transition(
                session, order=order, domain=StatusDomain.PREPARATION,
                to_status=PreparationStatus.BLOCKED.value, actor=current_user,
                reason=payload.description or f"Excepción: {etype.value}",
                evidence={"reason": payload.description or etype.value},
            )
        except TransitionError as exc:
            raise HTTPException(
                409, {"code": exc.code, "detail": exc.detail}
            ) from exc
    session.commit()
    session.refresh(exc_row)
    return {
        "id": exc_row.id,
        "type": etype.value,
        "subtype": exc_row.subtype,
        "order_id": order.id,
        "preparation_status": getattr(order.preparation_status, "value", order.preparation_status),
    }


@router.post("/orders/{order_id}/packing-info")
def packing_info(
    order_id: str,
    payload: PackingInfoIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    """SAT introduce peso/dimensiones/bultos. Se guarda en packing_json
    (junto a los documentos adjuntos)."""
    _ = current_user
    order = _get_order(session, order_id)
    packing = _packing(order)
    if payload.weight_kg is not None:
        packing["weight_kg"] = payload.weight_kg
    if payload.dimensions_cm is not None:
        packing["dimensions_cm"] = payload.dimensions_cm
    if payload.packages is not None:
        packing["packages"] = payload.packages
    order.packing_json = json.dumps(packing, default=str)
    session.commit()
    return {"order_id": order.id, "packing": packing}


@router.post("/orders/{order_id}/attach-document", status_code=201)
async def attach_document(
    order_id: str,
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    """Sube una foto/PDF a HiDrive (o disco local si no hay creds) vía la
    interfaz DocumentStorage y guarda su referencia en packing_json."""
    _ = current_user
    order = _get_order(session, order_id)
    data = await file.read()
    if not data:
        raise HTTPException(400, "Archivo vacío.")
    if len(data) > MAX_DOC_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="El documento supera el máximo de 15 MB.",
        )
    stored = get_document_storage().save(
        order_id=order.id, filename=file.filename or "documento",
        content_type=file.content_type, data=data,
    )
    packing = _packing(order)
    docs = packing.get("documents")
    if not isinstance(docs, list):
        docs = []
    doc = {**stored.as_dict(), "uploaded_by_user_id": current_user.id}
    docs.append(doc)
    packing["documents"] = docs
    order.packing_json = json.dumps(packing, default=str)
    session.commit()
    return {"order_id": order.id, "document": doc}
