"""BoHub ERP — «Marcar como procesado externamente» (B-2-fix4).

Un pedido gestionado fuera del ERP (Excel/proceso anterior, o previo a la
fecha de corte de la tienda) se lleva a estados terminales «externally»,
sale de las colas activas y se marca con quién/cuándo/motivo.

Usado por el endpoint manual/bulk y por el import (auto-marca los pedidos
Woo anteriores a `metadata_json.external_cutoff_date` de la tienda).
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.erp.models import (
    ErpException,
    ExceptionStatus,
    InvoiceStatus,
    Order,
    OrderStatusHistory,
    PaymentStatus,
    PreparationStatus,
    StatusDomain,
    TransportStatus,
)
from app.models.crm import User

logger = logging.getLogger(__name__)

#: Nota con la que se auto-resuelven las excepciones al externalizar.
_AUTO_RESOLVE_NOTE = "Auto-resuelta: pedido marcado como procesado externamente"

#: Estados terminales «externally» por dominio.
_EXTERNAL_STATES = {
    "preparation_status": PreparationStatus.ALREADY_COMPLETED_EXTERNALLY.value,
    "transport_status": TransportStatus.ALREADY_SHIPPED_EXTERNALLY.value,
    "invoice_status": InvoiceStatus.ALREADY_INVOICED_EXTERNALLY.value,
}
_DOMAIN_BY_COLUMN = {
    "preparation_status": StatusDomain.PREPARATION,
    "transport_status": StatusDomain.TRANSPORT,
    "invoice_status": StatusDomain.INVOICE,
}


def is_externally_processed(order: Order) -> bool:
    return order.externally_processed_at is not None


def mark_order_externally_processed(
    session: Session, *, order: Order, actor: User | None, note: str | None = None,
) -> bool:
    """Lleva el pedido a estados terminales externos + estampa la marca +
    escribe historial + audit. Idempotente: si ya estaba externalizado no
    hace nada y devuelve False. Devuelve True si lo marcó ahora.

    NO hace commit (transacción del caller)."""
    if is_externally_processed(order):
        return False

    now = datetime.now(UTC)
    actor_id = actor.id if actor else None

    # Pago: un pedido gestionado fuera se considera cobrado (si seguía
    # pendiente) — no degradamos un refunded/failed explícito.
    payment = getattr(order.payment_status, "value", order.payment_status)
    if payment in (PaymentStatus.PENDING.value, PaymentStatus.PARTIAL_PAID.value):
        _history(session, order, StatusDomain.PAYMENT, payment,
                 PaymentStatus.PAID.value, now, actor_id, note)
        order.payment_status = PaymentStatus.PAID.value

    for column, to_status in _EXTERNAL_STATES.items():
        current = getattr(order, column)
        current_value = getattr(current, "value", current)
        if current_value == to_status:
            continue
        _history(session, order, _DOMAIN_BY_COLUMN[column], current_value,
                 to_status, now, actor_id, note)
        setattr(order, column, to_status)

    order.externally_processed_at = now
    order.externally_processed_by_user_id = actor_id
    order.externally_processed_note = note

    # Las excepciones abiertas del pedido (SAT antiguas, etc.) quedarían
    # huérfanas en la bandeja — se auto-resuelven al externalizar (B-2-fix5).
    resolved = _resolve_open_exceptions(session, order.id, actor_id, now)
    session.flush()
    if resolved:
        logger.info(
            "erp.external_processed auto-resolved %d exception(s) for order %s",
            resolved, order.id,
        )
    _audit(session, order, actor, note)
    return True


def _resolve_open_exceptions(
    session: Session, order_id: str, actor_id: str | None, when: datetime,
) -> int:
    """Cierra las excepciones ABIERTAS del pedido con la nota estándar.
    Devuelve cuántas resolvió."""
    result = session.execute(
        update(ErpException)
        .where(
            ErpException.order_id == order_id,
            ErpException.status == ExceptionStatus.OPEN,
        )
        .values(
            status=ExceptionStatus.RESOLVED,
            resolved_at=when,
            resolved_by_user_id=actor_id,
            resolution_note=_AUTO_RESOLVE_NOTE,
        )
    )
    return result.rowcount or 0


def _history(
    session: Session, order: Order, domain: StatusDomain, from_status: str,
    to_status: str, when: datetime, actor_id: str | None, note: str | None,
) -> None:
    session.add(OrderStatusHistory(
        order_id=order.id, domain=domain, from_status=from_status,
        to_status=to_status, changed_at=when, changed_by_user_id=actor_id,
        reason="Procesado externamente",
        metadata_json=json.dumps({"note": note}) if note else None,
    ))


def _audit(session: Session, order: Order, actor: User | None, note: str | None) -> None:
    try:
        from app.core.audit import record_event  # noqa: PLC0415

        record_event(
            session, action="erp.order_externally_processed",
            target_type="order", target_id=order.id, actor=actor,
            metadata={"order_number": order.order_number, "note": note},
        )
    except Exception:  # noqa: BLE001 — audit nunca bloquea
        logger.warning("erp.external_processed audit failed", exc_info=True)
