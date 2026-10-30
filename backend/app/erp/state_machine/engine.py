"""BoHub ERP — motor de transiciones (Fase A PR 2).

`apply_transition` es la ÚNICA vía legítima de cambiar un estado de
dominio de un pedido. Hace, en orden:

  1. Valida que el arco (from → to) existe en `definitions.TRANSITIONS`.
  2. Valida el rol del actor (None = SYSTEM).
  3. Ejecuta los guards (cruces entre dominios).
  4. Exige las evidencias requeridas.
  5. Escribe `order_status_history` (con evidencias en metadata).
  6. Actualiza la columna del dominio en `orders`.
  7. Audit log (`erp.order_status_changed`) — nunca bloquea.
  8. Hook de notificaciones (no-op en Fase A; Fase B lo cablea).

NO hace commit — el caller (endpoint/worker) es dueño de la transacción.

Todo fallo es `TransitionError` con `code` estable para que la API lo
traduzca a 4xx sin parsear mensajes.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.erp.models.orders import Order, OrderStatusHistory, StatusDomain
from app.erp.state_machine.definitions import (
    DOMAIN_COLUMN,
    GUARD_INVOICEABLE,
    GUARD_PACKAGES_MEASURED,
    GUARD_PACKED,
    GUARD_TRACKING,
    SYSTEM,
    TransitionDef,
    find_transition,
    transitions_from,
)
from app.models.crm import User

logger = logging.getLogger(__name__)


class TransitionError(ValueError):
    """`code`: invalid_transition | role_forbidden | guard_failed |
    evidence_missing. `detail` lleva el contexto legible."""

    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


# --- guards ------------------------------------------------------------------
# Cada guard recibe (order, evidence) y devuelve None (ok) o el motivo del
# fallo. Los tests de mutación adversarial cubren que quitar cualquiera de
# estas comprobaciones rompe la suite.


def _guard_packed(order: Order, _evidence: dict[str, Any]) -> str | None:
    status = getattr(order.preparation_status, "value", order.preparation_status)
    if status != "packed":
        return f"crear envío exige preparación embalada; actual: {status!r}"
    return None


def _guard_tracking(order: Order, evidence: dict[str, Any]) -> str | None:
    tracking = (evidence.get("tracking_number") or order.tracking_number or "").strip()
    if tracking:
        return None
    # Fase D-1-fix1: recogida manual del taller (transportista sin tracking en
    # el sistema). El operativo confirma explícitamente que el paquete salió.
    if evidence.get("manual_pickup"):
        return None
    return "en tránsito exige tracking_number (o marcar recogida manual)"


def _guard_invoiceable(order: Order, _evidence: dict[str, Any]) -> str | None:
    """Facturar exige pago cobrado. **No** exige que los SKU estén mapeados.

    ERP-E2-fix1: el sub-requisito «todos los CODART mapeados» se retira. Una
    línea sin CODART se emite como texto libre (`ARTLFA=''`), que es lo que ya
    hacen las proformas (C-4-fix5) y lo que Bart validó en la emisión real: la
    factura sale bien y el artículo aparece por su descripción. Bloquear por
    mapeo dejaba pedidos sin facturar por un dato de catálogo que no impide
    emitir, y contradecía C-2-fix3 (que retiró el aviso `sku_unmapped`)."""
    status = getattr(order.payment_status, "value", order.payment_status)
    if status != "paid":
        return f"facturar exige pago 'paid'; actual: {status!r}"
    return None


def _guard_packages_measured(order: Order, _evidence: dict[str, Any]) -> str | None:
    """Fase D: embalar exige ≥1 bulto medido. Las columnas de
    `shipment_packages` son NOT NULL, así que basta con que exista una fila
    (la validación peso/medidas se aplica al crearlas)."""
    from sqlalchemy import func, select  # noqa: PLC0415
    from sqlalchemy.orm import object_session  # noqa: PLC0415

    from app.erp.models import ShipmentPackage  # noqa: PLC0415

    session = object_session(order)
    if session is None:
        # Orden desconectado (no debería en apply_transition) → no bloquear.
        return None
    n = session.scalar(
        select(func.count(ShipmentPackage.id)).where(
            ShipmentPackage.order_id == order.id
        )
    ) or 0
    if n < 1:
        return "embalar exige al menos un bulto con peso y medidas"
    return None


_GUARDS = {
    GUARD_PACKED: _guard_packed,
    GUARD_TRACKING: _guard_tracking,
    GUARD_INVOICEABLE: _guard_invoiceable,
    GUARD_PACKAGES_MEASURED: _guard_packages_measured,
}


# --- motor -------------------------------------------------------------------


def _actor_role(actor: User | None) -> str:
    if actor is None:
        return SYSTEM
    return getattr(actor.role, "value", str(actor.role))


def _actor_roles(actor: User | None) -> set[str]:
    """Roles EFECTIVOS del actor (rol principal + `erp_roles`), para el chequeo
    por arco. El actor `system` (automático) es solo {SYSTEM}."""
    if actor is None:
        return {SYSTEM}
    from app.erp.capabilities import effective_roles  # noqa: PLC0415

    return effective_roles(actor)


#: Dominios que no existen para una muestra: no se cobra ni se factura.
_DOMINIOS_FISCALES = (StatusDomain.PAYMENT, StatusDomain.INVOICE)


def _es_muestra(order: Order) -> bool:
    from app.erp.sample_orders import is_sample_order  # noqa: PLC0415

    return is_sample_order(order)


def _roles_del_arco(transition: TransitionDef, order: Order) -> frozenset[str]:
    """Los roles que pueden disparar el arco EN ESTE pedido: los de siempre y,
    si es una muestra, los que el arco abre además a las muestras."""
    if transition.sample_roles and _es_muestra(order):
        return transition.allowed_roles | transition.sample_roles
    return transition.allowed_roles


def apply_transition(
    session: Session,
    *,
    order: Order,
    domain: StatusDomain,
    to_status: str,
    actor: User | None,
    reason: str | None = None,
    evidence: dict[str, Any] | None = None,
) -> OrderStatusHistory:
    evidence = evidence or {}
    column = DOMAIN_COLUMN[domain]
    current = getattr(order, column)
    current_value = getattr(current, "value", current)

    if domain in _DOMINIOS_FISCALES and _es_muestra(order):
        raise TransitionError(
            "invalid_transition",
            f"una muestra no se cobra ni se factura: «{domain.value}» no aplica",
        )
    transition = find_transition(domain, current_value, to_status)
    if transition is None:
        raise TransitionError(
            "invalid_transition",
            f"{domain.value}: {current_value!r} → {to_status!r} no es un arco permitido",
        )

    role = _actor_role(actor)
    if not (_actor_roles(actor) & _roles_del_arco(transition, order)):
        raise TransitionError(
            "role_forbidden",
            f"rol {role!r} no puede «{transition.label}» "
            f"({domain.value}: {current_value} → {to_status})",
        )

    for guard_key in transition.guards:
        failure = _GUARDS[guard_key](order, evidence)
        if failure is not None:
            raise TransitionError("guard_failed", f"[{guard_key}] {failure}")

    missing = [k for k in transition.required_evidence if not str(evidence.get(k) or "").strip()]
    if missing:
        raise TransitionError(
            "evidence_missing",
            f"«{transition.label}» requiere evidencia: {', '.join(missing)}",
        )

    # Efectos laterales del arco ANTES de mutar el estado.
    if domain == StatusDomain.TRANSPORT and evidence.get("tracking_number"):
        order.tracking_number = str(evidence["tracking_number"]).strip()

    now = datetime.now(UTC)
    history = OrderStatusHistory(
        order_id=order.id,
        domain=domain,
        from_status=current_value,
        to_status=to_status,
        changed_at=now,
        changed_by_user_id=actor.id if actor else None,
        reason=reason,
        metadata_json=json.dumps(evidence, default=str) if evidence else None,
    )
    session.add(history)
    setattr(order, column, to_status)
    session.flush()

    _audit(session, order, transition, current_value, actor, reason)
    _notify_transition(order, transition, actor)
    return history


def available_transitions(
    order: Order, domain: StatusDomain, actor: User | None
) -> list[TransitionDef]:
    """Arcos que el actor puede disparar desde el estado actual — la UI
    pinta exactamente esto como botones (sin duplicar la matriz)."""
    if domain in _DOMINIOS_FISCALES and _es_muestra(order):
        return []        # una muestra no ofrece cobrar ni facturar
    current = getattr(order, DOMAIN_COLUMN[domain])
    current_value = getattr(current, "value", current)
    roles = _actor_roles(actor)
    return [
        t for t in transitions_from(domain, current_value)
        if roles & _roles_del_arco(t, order)
    ]


def _audit(
    session: Session, order: Order, transition: TransitionDef,
    from_value: str, actor: User | None, reason: str | None,
) -> None:
    try:
        from app.core.audit import record_event  # noqa: PLC0415

        record_event(
            session,
            action="erp.order_status_changed",
            target_type="order",
            target_id=order.id,
            actor=actor,
            metadata={
                "domain": transition.domain.value,
                "from": from_value,
                "to": transition.to_status,
                "label": transition.label,
                "reason": reason,
                "order_number": order.order_number,
            },
        )
    except Exception:  # noqa: BLE001 — audit nunca bloquea la transición
        logger.warning("erp.audit transition failed", exc_info=True)


def _notify_transition(order: Order, transition: TransitionDef, actor: User | None) -> None:
    """Hook de notificaciones. Fase A: no-op (log). Fase B cablea emails/
    avisos (p.ej. owner al pasar a incident, PEDIDOS al bloquearse)."""
    logger.info(
        "erp.transition %s %s→%s order=%s actor=%s",
        transition.domain.value, transition.from_status, transition.to_status,
        order.order_number, actor.email if actor else "system",
    )
