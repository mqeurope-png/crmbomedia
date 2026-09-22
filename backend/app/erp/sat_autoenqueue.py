"""BoHub ERP · Lote 4 — entrada automática en la Cola SAT al pagar.

Regla (decisión de Bart): TODO pedido PAGADO entra en la Cola SAT
(«Por embalar») en cuanto se cobra, SIN pasar antes por la aprobación de la
Cola PEDIDOS. Se DESACOPLA la entrada al taller de la aprobación: el taller
ve y prepara el pedido en cuanto está pagado, mientras la aprobación y la
facturación siguen en paralelo en la bandeja.

`enqueue_paid_order` es conservador e IDEMPOTENTE: SOLO promueve desde el
estado pre-cola `pending_review` → `in_queue`; nunca toca un pedido ya en
cola / preparándose / embalado / bloqueado / externalizado, así que no pisa
las salidas del taller ni las transiciones que este ya hizo. NO estampa
`approved_at` / `approved_by`: la aprobación sigue siendo un paso aparte
(esto NO aprueba el pedido, solo lo mete en el taller).

Deja la MISMA huella que una transición normal — una fila de
`order_status_history` en el dominio PREPARATION + un evento de auditoría —
igual que `approve_inline` / `_force_in_queue`, para que el timeline y el
historial del taller lo cuenten igual. NO hace commit: la transacción es del
caller (import Woo, conversión FACTUSOL, alta manual con pago).
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.erp.models.orders import (
    Order,
    OrderStatusHistory,
    PreparationStatus,
    StatusDomain,
)
from app.erp.state_machine.definitions import PAYMENT_OK_FOR_PREPARATION
from app.models.crm import User

logger = logging.getLogger(__name__)

#: Motivo con el que queda en el historial la entrada automática al pagar.
AUTO_ENQUEUE_REASON = "Entrada automática en la Cola SAT (pedido pagado)"

#: Acción de auditoría de la entrada automática (distinta de
#: `erp.order_status_changed` para poder auditar/medir aparte cuántos pedidos
#: entran solos vs. aprobados a mano).
AUDIT_ACTION = "erp.sat_auto_enqueued"


def _value(v: Any) -> Any:
    return getattr(v, "value", v)


def enqueue_paid_order(
    session: Session, order: Order, *, actor: User | None = None
) -> bool:
    """Mete un pedido PAGADO en la Cola SAT (`in_queue`) sin aprobación previa.

    Guards (todos deben cumplirse; si no, devuelve False y NO toca nada):
      - pago en {paid, credit_approved, partial_paid} (PAYMENT_OK_FOR_PREPARATION),
        SALVO en una MUESTRA / envío no facturable: no se cobra, así que no hay
        pago que esperar — entra a la cola al crearse;
      - no anulado (`cancelled_at is None`);
      - no completado (`completed_at is None`);
      - no quitado a mano (`seguimiento_excluded_at is None`);
      - no marcado «No requiere envío» (`shipping_not_required` False);
      - preparación == `pending_review` — SOLO se promueve desde la pre-cola;
        nunca toca in_queue / preparing / packed / blocked / externalizado, con
        lo que es idempotente y respeta las salidas ya existentes.

    NO hace commit. Devuelve True si lo encoló ahora, False si algún guard lo
    impidió (incl. el ya-en-cola: reintentar es un no-op)."""
    from app.erp.sample_orders import is_sample_order  # noqa: PLC0415

    # Una muestra no se cobra: el gate de pago no aplica (si no, no entraría
    # nunca a la Cola SAT, que es lo ÚNICO que hay que hacer con ella).
    if (
        not is_sample_order(order)
        and _value(order.payment_status) not in PAYMENT_OK_FOR_PREPARATION
    ):
        return False
    if order.cancelled_at is not None:
        return False
    if order.completed_at is not None:
        return False
    if order.seguimiento_excluded_at is not None:
        return False
    if getattr(order, "shipping_not_required", False):
        # «No requiere envío»: nunca entra (ni vuelve a entrar) a la Cola SAT.
        return False
    if _value(order.preparation_status) != PreparationStatus.PENDING_REVIEW.value:
        return False

    # La fila de historial referencia `order.id` (FK NOT NULL). En el camino de
    # creación Woo el pedido puede no estar aún flush-eado → asegúralo.
    if order.id is None:
        session.flush()

    now = datetime.now(UTC)
    actor_id = actor.id if actor else None
    session.add(OrderStatusHistory(
        order_id=order.id, domain=StatusDomain.PREPARATION,
        from_status=PreparationStatus.PENDING_REVIEW.value,
        to_status=PreparationStatus.IN_QUEUE.value,
        changed_at=now, changed_by_user_id=actor_id,
        reason=AUTO_ENQUEUE_REASON,
        metadata_json=json.dumps({"reason": AUTO_ENQUEUE_REASON, "auto": True}),
    ))
    order.preparation_status = PreparationStatus.IN_QUEUE.value
    session.flush()
    _audit(session, order, actor)
    return True


def _audit(session: Session, order: Order, actor: User | None) -> None:
    try:
        from app.core.audit import record_event  # noqa: PLC0415

        record_event(
            session, action=AUDIT_ACTION, target_type="order",
            target_id=order.id, actor=actor,
            metadata={
                "domain": StatusDomain.PREPARATION.value,
                "from": PreparationStatus.PENDING_REVIEW.value,
                "to": PreparationStatus.IN_QUEUE.value,
                "reason": AUTO_ENQUEUE_REASON,
                "order_number": order.order_number,
                "auto": True,
            },
        )
    except Exception:  # noqa: BLE001 — la auditoría nunca bloquea la entrada
        logger.warning("erp.sat_auto_enqueued audit failed", exc_info=True)
