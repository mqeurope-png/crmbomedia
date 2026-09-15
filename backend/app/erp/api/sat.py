"""BoHub ERP — endpoints de la Cola SAT (Fase A PR 5).

  - GET  /api/erp/sat/queue           cola priorizada (in_queue/preparing/blocked)
                                      + filtros (fechas, tienda, estado, texto)
  - GET  /api/erp/sat/history         historial de «enviados al taller»
                                      (emails al SAT + aprobaciones)
  - GET  /api/erp/sat/find-order      nº de pedido → id (para «añadir a mano»)
  - POST /api/erp/orders/{id}/sat-enqueue        añade un pedido a la cola a mano
  - POST /api/erp/orders/{id}/report-exception   crea excepción + bloquea
  - POST /api/erp/orders/{id}/packing-info        peso/dimensiones/bultos
  - POST /api/erp/orders/{id}/attach-document     foto/PDF → DocumentStorage

La cola SAT lee directo de `orders` filtrando por estado de preparación
(decisión cerrada nº10: NO se reusa `tasks`). Reportar excepción cambia
preparation → blocked vía el engine (misma auditoría que cualquier
transición).

Regla del taller (Lote B6): «enviado al taller» = el pedido se mandó por
email al SAT O se aprobó. Por eso el historial une las dos fuentes y por eso
enviar por email aprueba (en `order_email.py`) si seguía pendiente.
"""
from __future__ import annotations

import json
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import not_found
from app.db.session import get_session
from app.erp.api.deps import require_erp_edit, require_erp_view
from app.erp.models import (
    EXCEPTION_SUBTYPES,
    KIND_ALBARAN,
    KIND_ETIQUETA,
    ErpException,
    ExceptionType,
    Order,
    OrderStatusHistory,
    PreparationStatus,
    ShipmentFile,
    StatusDomain,
)
from app.erp.state_machine import TransitionError, apply_transition
from app.erp.storage import get_document_storage
from app.models.crm import AuditLog, User

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

#: Filtro `estado` de la cola: qué estados de preparación entran en «Por
#: embalar». Los de «Listos» (`ready` / `packed`) se resuelven aparte.
_ESTADO_PREPARING: dict[str, tuple[str, ...]] = {
    "por_embalar": tuple(_QUEUE_ORDER),
    PreparationStatus.BLOCKED.value: (PreparationStatus.BLOCKED.value,),
    PreparationStatus.IN_QUEUE.value: (PreparationStatus.IN_QUEUE.value,),
    PreparationStatus.PREPARING.value: (PreparationStatus.PREPARING.value,),
}
_ESTADO_READY = ("ready", PreparationStatus.PACKED.value)
_ESTADO_PATTERN = "^(por_embalar|blocked|in_queue|preparing|ready|packed)$"

#: Motivo con el que queda en el historial la entrada manual a la cola.
_ENQUEUE_REASON = "añadido a mano a la Cola SAT"


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


def _prep(o: Order) -> str:
    return getattr(o.preparation_status, "value", o.preparation_status)


#: Estados de transporte que sacan un pedido de «Listos para envío» (ya salió).
_SHIPPED_TRANSPORT = ("in_transit", "delivered", "already_shipped_externally")


# --- filtros comunes (cola + historial) --------------------------------------


def _apply_filters(
    stmt: Any, *, desde: date | None, hasta: date | None,
    store_slug: str | None, q: str | None,
) -> Any:
    """Filtros compartidos por la cola y el historial: rango de fecha del
    pedido (`placed_at`, inclusivo), tienda por slug (artisjet / boprint /
    fluxlasers…) y texto (nº de pedido o cliente: empresa, contacto, email).
    Mismo criterio de fechas/tienda que la bandeja (`list_orders`)."""
    if desde:
        stmt = stmt.where(Order.placed_at >= datetime.combine(desde, time.min, tzinfo=UTC))
    if hasta:
        stmt = stmt.where(Order.placed_at < datetime.combine(
            hasta + timedelta(days=1), time.min, tzinfo=UTC,
        ))
    if store_slug and store_slug.strip():
        from app.models.integration_settings import IntegrationAccount  # noqa: PLC0415

        stmt = stmt.where(Order.store_id.in_(
            select(IntegrationAccount.id).where(
                func.lower(IntegrationAccount.account_id) == store_slug.strip().lower()
            )
        ))
    if q and q.strip():
        from app.models.crm import Company, Contact  # noqa: PLC0415

        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(
            Order.order_number.ilike(like),
            Order.company_id.in_(select(Company.id).where(Company.name.ilike(like))),
            Order.contact_id.in_(select(Contact.id).where(or_(
                Contact.first_name.ilike(like),
                Contact.last_name.ilike(like),
                Contact.email.ilike(like),
            ))),
        ))
    return stmt


def _files_by_order(session: Session, order_ids: list[str]) -> dict[str, set[str]]:
    """Presencia de albarán/etiqueta vigentes (Fase D) — una sola query."""
    files: dict[str, set[str]] = {}
    if order_ids:
        for oid, kind in session.execute(
            select(ShipmentFile.order_id, ShipmentFile.kind).where(
                ShipmentFile.order_id.in_(order_ids),
                ShipmentFile.replaced_at.is_(None),
            )
        ):
            files.setdefault(oid, set()).add(kind)
    return files


def _store_slugs(session: Session, orders: list[Order]) -> dict[str, str]:
    """`{store_id: slug}` de las tiendas de los pedidos dados (una query)."""
    from app.models.integration_settings import IntegrationAccount  # noqa: PLC0415

    ids = {o.store_id for o in orders if o.store_id}
    if not ids:
        return {}
    return {
        acc_id: slug for acc_id, slug in session.execute(
            select(IntegrationAccount.id, IntegrationAccount.account_id).where(
                IntegrationAccount.id.in_(ids)
            )
        )
    }


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _as_utc(dt: datetime) -> datetime:
    """SQLite devuelve naive; MySQL también puede. Para ordenar y serializar
    todo igual se asume UTC (es lo que se escribe)."""
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


# --- cola ----------------------------------------------------------------------


@router.get("/sat/queue")
def sat_queue(
    desde: date | None = Query(default=None),
    hasta: date | None = Query(default=None),
    store_slug: str | None = Query(default=None, max_length=64),
    estado: str | None = Query(default=None, pattern=_ESTADO_PATTERN),
    q: str | None = Query(default=None, max_length=120),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    """Cola táctil del taller en 2 secciones (D-1-fix1):

    - `preparing`: por embalar (in_queue / preparing / blocked), priorizados.
    - `ready_for_pickup`: embalados (`packed`) pero aún no salidos del taller
      (transporte NO en tránsito/entregado/externalizado) — falta imprimir
      albarán/etiqueta y marcar recogido.

    Filtros (Lote B6), todos opcionales y aplicados a las dos secciones:
    `desde`/`hasta` (fecha del pedido), `store_slug`, `estado`
    (`por_embalar` = las 3 de arriba · `blocked` · `in_queue` · `preparing` ·
    `ready`/`packed` = solo «Listos») y `q` (nº de pedido o cliente).
    """
    _ = current_user
    from app.erp.api.orders import worklist_visible  # noqa: PLC0415

    filters = {"desde": desde, "hasta": hasta, "store_slug": store_slug, "q": q}
    # Sin `estado` entran las dos secciones; con él, solo la que toca.
    prep_statuses = _ESTADO_PREPARING.get(estado or "por_embalar", ())
    want_ready = not estado or estado in _ESTADO_READY

    # Control manual (#388 + bandeja): los quitados a mano tampoco entran en el
    # taller (mismo flag que la bandeja y el seguimiento).
    prep_rows: list[Order] = []
    if prep_statuses:
        prep_rows = list(session.scalars(
            _apply_filters(worklist_visible(
                select(Order).where(Order.preparation_status.in_(list(prep_statuses)))
            ), **filters).options(selectinload(Order.lines))
        ))
        prep_rows.sort(key=lambda o: (
            _QUEUE_ORDER.get(_prep(o), 9),
            o.placed_at or o.created_at,
        ))
    ready_rows: list[Order] = []
    if want_ready:
        ready_rows = list(session.scalars(
            _apply_filters(worklist_visible(select(Order).where(
                Order.preparation_status == PreparationStatus.PACKED.value,
                Order.transport_status.notin_(_SHIPPED_TRANSPORT),
            )), **filters).options(selectinload(Order.lines))
            .order_by(Order.placed_at.asc())
        ))

    all_rows = [*prep_rows, *ready_rows]
    files_by_order = _files_by_order(session, [o.id for o in all_rows])
    stores = _store_slugs(session, all_rows)

    # D-2: nombre del cliente en las cards del taller (el número solo no basta).
    from app.erp.api.orders import customer_names  # noqa: PLC0415

    names = customer_names(session, all_rows)

    def _item(o: Order) -> dict[str, Any]:
        who = names.get(o.id) or {}
        return {
            "id": o.id,
            "order_number": o.order_number,
            "contact_name": who.get("contact_name"),
            "company_name": who.get("company_name"),
            "preparation_status": _prep(o),
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
            # Albarán que BoHub creó en FACTUSOL (Fase 2). Es la fuente
            # PREFERENTE del PDF en el taller: el mismo documento que el botón
            # de la ficha (#396) y el que adjunta el email al SAT (#407). El
            # fichero subido a mano (`has_albaran`) queda de alternativa para
            # los pedidos del flujo antiguo.
            "factusol_albaran_number": o.factusol_albaran_number or None,
            # Lote B6: la vista lista enseña tienda y fecha del pedido.
            "store_slug": stores.get(o.store_id or ""),
            "placed_at": _iso(o.placed_at),
        }

    return {
        "preparing": [_item(o) for o in prep_rows],
        "ready_for_pickup": [_item(o) for o in ready_rows],
    }


# --- historial de enviados al taller -----------------------------------------


@router.get("/sat/history")
def sat_history(
    desde: date | None = Query(default=None),
    hasta: date | None = Query(default=None),
    store_slug: str | None = Query(default=None, max_length=64),
    q: str | None = Query(default=None, max_length=120),
    limit: int = Query(default=100, ge=1, le=500),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    """Historial de «enviados al taller», más recientes primero: unión de

    - `email_sat`: envíos del pedido por email (auditoría `erp.order_emailed`:
      destinatarios, asunto, quién y cuándo);
    - `aprobado`: paso de preparación a `in_queue` (aprobación en Cola
      PEDIDOS, aprobación implícita al enviar al SAT, reapertura o entrada
      manual — el `reason` lo distingue).

    Mismos filtros que la cola (fecha del pedido, tienda, texto). No esconde
    anulados/quitados: es un registro de lo que pasó, y enseña el estado
    actual de cada pedido.
    """
    _ = current_user
    filters = {"desde": desde, "hasta": hasta, "store_slug": store_slug, "q": q}

    email_rows = session.execute(
        _apply_filters(
            select(AuditLog, Order).join(Order, Order.id == AuditLog.target_id).where(
                AuditLog.action == "erp.order_emailed",
                AuditLog.target_type == "order",
            ), **filters,
        ).order_by(AuditLog.created_at.desc()).limit(limit)
    ).all()
    approval_rows = session.execute(
        _apply_filters(
            select(OrderStatusHistory, Order).join(
                Order, Order.id == OrderStatusHistory.order_id,
            ).where(
                OrderStatusHistory.domain == StatusDomain.PREPARATION,
                OrderStatusHistory.to_status == PreparationStatus.IN_QUEUE.value,
            ), **filters,
        ).order_by(OrderStatusHistory.changed_at.desc()).limit(limit)
    ).all()

    orders: dict[str, Order] = {}
    for _row, o in (*email_rows, *approval_rows):
        orders[o.id] = o
    order_list = list(orders.values())

    from app.erp.api.orders import customer_names  # noqa: PLC0415

    names = customer_names(session, order_list)
    files_by_order = _files_by_order(session, list(orders))
    stores = _store_slugs(session, order_list)
    user_ids = {
        uid for uid in (
            *(a.actor_user_id for a, _o in email_rows),
            *(h.changed_by_user_id for h, _o in approval_rows),
        ) if uid
    }
    user_names: dict[str, str] = {}
    if user_ids:
        user_names = {
            u.id: u.full_name for u in session.scalars(
                select(User).where(User.id.in_(user_ids))
            )
        }

    def _base(o: Order, at: datetime) -> dict[str, Any]:
        who = names.get(o.id) or {}
        return {
            "order_id": o.id,
            "order_number": o.order_number,
            "contact_name": who.get("contact_name"),
            "company_name": who.get("company_name"),
            "at": _as_utc(at).isoformat(),
            "preparation_status": _prep(o),
            "transport_status": getattr(o.transport_status, "value", o.transport_status),
            "factusol_albaran_number": o.factusol_albaran_number or None,
            "has_albaran": KIND_ALBARAN in files_by_order.get(o.id, set()),
            "store_slug": stores.get(o.store_id or ""),
            "placed_at": _iso(o.placed_at),
            "cancelled": o.cancelled_at is not None,
            "excluded": o.seguimiento_excluded_at is not None,
        }

    items: list[dict[str, Any]] = []
    for a, o in email_rows:
        try:
            meta = json.loads(a.metadata_json) if a.metadata_json else {}
        except (TypeError, ValueError):
            meta = {}
        if not isinstance(meta, dict):
            meta = {}
        items.append({
            **_base(o, a.created_at),
            "kind": "email_sat",
            "actor_user_id": a.actor_user_id,
            "actor_name": user_names.get(a.actor_user_id or "") or a.actor_email,
            "to": [str(x) for x in (meta.get("to") or [])],
            "cc": [str(x) for x in (meta.get("cc") or [])],
            "subject": meta.get("subject"),
            "attachment_kinds": list(meta.get("attachment_kinds") or []),
            "reason": None,
            "from_status": None,
        })
    for h, o in approval_rows:
        items.append({
            **_base(o, h.changed_at),
            "kind": "aprobado",
            "actor_user_id": h.changed_by_user_id,
            "actor_name": user_names.get(h.changed_by_user_id or ""),
            "to": [],
            "cc": [],
            "subject": None,
            "attachment_kinds": [],
            "reason": h.reason,
            "from_status": h.from_status,
        })
    items.sort(key=lambda it: it["at"], reverse=True)
    return {"items": items[:limit], "limit": limit}


# --- añadir a mano ------------------------------------------------------------


@router.get("/sat/find-order")
def sat_find_order(
    number: str = Query(min_length=1, max_length=64),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    """Resuelve un nº de pedido (exacto, sin distinguir mayúsculas) a su id,
    para «Añadir pedido a la cola». Devuelve también en qué situación está
    (ya en cola / anulado / quitado) para que la UI avise antes de intentar."""
    _ = current_user
    wanted = number.strip().lower()
    order = session.scalar(
        select(Order).where(func.lower(Order.order_number) == wanted)
        .order_by(Order.created_at.desc()).limit(1)
    )
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, {
            "code": "order_not_found",
            "detail": f"No hay ningún pedido con el número «{number.strip()}».",
        })
    from app.erp.api.orders import customer_names  # noqa: PLC0415

    who = customer_names(session, [order]).get(order.id) or {}
    return {
        "id": order.id,
        "order_number": order.order_number,
        "contact_name": who.get("contact_name"),
        "company_name": who.get("company_name"),
        "preparation_status": _prep(order),
        "transport_status": getattr(order.transport_status, "value", order.transport_status),
        "already_queued": _prep(order) in _QUEUE_ORDER,
        "cancelled": order.cancelled_at is not None,
        "excluded": order.seguimiento_excluded_at is not None,
    }


def _force_in_queue(session: Session, order: Order, actor: User, reason: str) -> None:
    """Sin arco en la máquina de estados (p.ej. externalizado) o arco reservado
    a otro rol: se fuerza `in_queue` dejando la MISMA huella que una transición
    normal (fila de historial + auditoría), para que el timeline y el
    historial del taller lo cuenten igual."""
    from app.core.audit import record_event  # noqa: PLC0415

    current = _prep(order)
    session.add(OrderStatusHistory(
        order_id=order.id, domain=StatusDomain.PREPARATION,
        from_status=current, to_status=PreparationStatus.IN_QUEUE.value,
        changed_at=datetime.now(UTC), changed_by_user_id=actor.id,
        reason=reason, metadata_json=json.dumps({"reason": reason, "forced": True}),
    ))
    order.preparation_status = PreparationStatus.IN_QUEUE.value
    session.flush()
    record_event(
        session, action="erp.order_status_changed", target_type="order",
        target_id=order.id, actor=actor,
        metadata={
            "domain": StatusDomain.PREPARATION.value, "from": current,
            "to": PreparationStatus.IN_QUEUE.value,
            "label": "Añadir a mano a la Cola SAT", "reason": reason,
            "order_number": order.order_number, "forced": True,
        },
    )


@router.post("/orders/{order_id}/sat-enqueue")
def sat_enqueue(
    order_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_edit),
) -> dict[str, Any]:
    """Añade un pedido a la Cola SAT a mano (Lote B6).

    - pendiente de revisión → misma lógica que aprobar (bloqueos → 409,
      transición a in_queue, approved_at/by);
    - embalado / externalizado / otros → in_queue por la máquina de estados
      si hay arco (packed → in_queue «Reabrir»), y si no, forzado con fila de
      historial + auditoría;
    - ya en cola (in_queue / preparing / blocked) → idempotente;
    - anulado → 409 `cancelled`; quitado de las listas → 409 `excluded`.
    """
    from app.erp.api.orders import _blockers, approve_inline  # noqa: PLC0415

    order = _get_order(session, order_id)
    if order.cancelled_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, {
            "code": "cancelled",
            "detail": (
                f"El pedido {order.order_number} está anulado: "
                "no se puede añadir a la Cola SAT."
            ),
        })
    if order.seguimiento_excluded_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, {
            "code": "excluded",
            "detail": (
                f"El pedido {order.order_number} está quitado de las listas de trabajo: "
                "reinclúyelo desde la bandeja antes de añadirlo a la Cola SAT."
            ),
        })

    current = _prep(order)
    result: dict[str, Any] = {
        "order_id": order.id, "order_number": order.order_number,
        "already_queued": False, "approved": False, "via": None,
    }
    if current in _QUEUE_ORDER:
        return {**result, "preparation_status": current, "already_queued": True}

    if current == PreparationStatus.PENDING_REVIEW.value:
        blockers = _blockers(session, order)
        if blockers:
            raise HTTPException(status.HTTP_409_CONFLICT, {
                "code": "blocked", "blockers": blockers,
                "detail": (
                    f"El pedido {order.order_number} tiene excepciones sin resolver: "
                    "resuélvelas antes de añadirlo a la Cola SAT."
                ),
            })
        try:
            approve_inline(session, order, current_user, reason=_ENQUEUE_REASON)
        except TransitionError as exc:
            raise HTTPException(409, {"code": exc.code, "detail": exc.detail}) from exc
        result.update(approved=True, via="approve")
    else:
        try:
            apply_transition(
                session, order=order, domain=StatusDomain.PREPARATION,
                to_status=PreparationStatus.IN_QUEUE.value, actor=current_user,
                reason=_ENQUEUE_REASON, evidence={"reason": _ENQUEUE_REASON},
            )
            result["via"] = "transition"
        except TransitionError as exc:
            if exc.code not in ("invalid_transition", "role_forbidden"):
                raise HTTPException(409, {"code": exc.code, "detail": exc.detail}) from exc
            _force_in_queue(session, order, current_user, _ENQUEUE_REASON)
            result["via"] = "direct"
    session.commit()
    return {**result, "preparation_status": _prep(order)}


# --- reportar excepción ------------------------------------------------------


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
