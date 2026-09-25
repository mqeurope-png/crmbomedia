"""Webhook de estados de Genei (PR-2) + aplicación del estado al pedido.

Genei llama al `notificationUrl` (que BoHub envía al crear el envío) en cada
cambio de estado, con el MISMO objeto que `GET /shipments/{code}`. Aquí se:

1. localiza el pedido (por `codigo_envio_externo` = nº de pedido, o por el
   `codigo_envio` guardado en `packing_json.genei`);
2. guarda el estado del envío en `packing_json.genei` (código, tracking, etc.);
3. mueve el `transport_status` del pedido (como SYSTEM) según el estado.

El `transport_status` es la **fuente común**: la ficha, la Cola SAT y la hoja
de Seguimiento leen de ahí. La misma función (`apply_shipment_state`) la usa el
botón «Actualizar estado» (respaldo manual), así que funciona aunque el webhook
no esté desplegado.

Qué mueve el transporte SOLO (ver `tracking.transport_target`): únicamente una
INCIDENCIA (→ «Incidencias») y el «entregado» de un pedido ya marcado como
recogido (sigue en «Enviados»). El paso a «Enviados» lo hace una persona con
«📤 Marcar recogido» en la Cola SAT. El tracking DETALLADO (`GET
/shipments/{code}/tracking`: los eventos del propio transportista) se guarda y
se ENSEÑA; no mueve nada.

Idempotente: recibir el mismo estado dos veces no descuadra — un arco que ya se
recorrió no vuelve a aplicarse (no es un arco válido desde el estado actual).
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.erp.integrations.genei.service import (
    genei_state_of,
    now_iso,
    set_genei_state,
    summarize_shipment,
)
from app.erp.integrations.genei.tracking import (
    UNKNOWN,
    carrier_step_label,
    summarize_tracking,
    transport_target,
)
from app.erp.models import Order
from app.erp.state_machine.definitions import StatusDomain
from app.erp.state_machine.engine import TransitionError, apply_transition

logger = logging.getLogger(__name__)

#: Enlace pedido↔envío en el payload de Genei.
_EXTERNAL_KEYS = ("codigo_envio_externo", "externalShippingCode", "external_shipping_code")
_SHIP_CODE_KEYS = ("codigo_envio", "shipmentCode", "shipment_code", "reference", "code")
#: Texto de la incidencia de TRANSPORTE (para la evidencia del arco).
_INCIDENCIA_KEYS = ("desc_incidencia", "descripcion_incidencia", "incidencia", "nombre_estado")

#: Camino lineal del transporte hasta cada estado objetivo. El webhook avanza
#: el pedido paso a paso desde donde esté; `incident` cuelga de `in_transit`.
_STEPS_TO: dict[str, list[tuple[str, str]]] = {
    "label_created": [("not_shipped", "label_created")],
    "in_transit": [("not_shipped", "label_created"), ("label_created", "in_transit")],
    "delivered": [
        ("not_shipped", "label_created"), ("label_created", "in_transit"),
        ("in_transit", "delivered"),
    ],
    "incident": [
        ("not_shipped", "label_created"), ("label_created", "in_transit"),
        ("in_transit", "incident"),
    ],
}


def _shipment_data(payload: Any) -> dict[str, Any]:
    """El objeto del envío dentro del envoltorio `{status, message, data:{…}}`
    (o el propio dict si ya viene pelado)."""
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        return payload["data"]
    return payload if isinstance(payload, dict) else {}


def _pick(data: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value
    return None


def _transport_value(order: Order) -> str:
    return str(getattr(order.transport_status, "value", order.transport_status) or "")


def advance_transport(
    session: Session, order: Order, target: str | None, *, evidence: dict[str, Any],
) -> bool:
    """Avanza el `transport_status` del pedido hasta `target` recorriendo los
    arcos válidos como SYSTEM (`actor=None`). Solo aplica un arco si el pedido
    está justo en su origen, así que es idempotente (un estado ya pasado no
    tiene arco) y no retrocede (un `in_transit` tras `delivered` no hace nada).
    Un arco que no se puede (guard/rol) detiene el avance sin romper."""
    if not target or target not in _STEPS_TO:
        return False
    applied = False
    for frm, to in _STEPS_TO[target]:
        if _transport_value(order) != frm:
            continue
        try:
            apply_transition(
                session, order=order, domain=StatusDomain.TRANSPORT, to_status=to,
                actor=None, reason="Genei (webhook/estado)", evidence=evidence,
            )
            applied = True
        except TransitionError as exc:
            logger.info("genei transporte %s→%s no aplicado: %s", frm, to, exc)
            break
    return applied


def apply_shipment_state(
    session: Session, order: Order, shipment: dict[str, Any], *,
    tracking: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], bool]:
    """Aplica el estado de un envío Genei (objeto `data`) al pedido: guarda el
    bloque `genei` + tracking y, SOLO si es una incidencia (o un «entregado» de
    un pedido ya marcado como recogido), mueve el `transport_status`. Devuelve
    `(summary, transporte_aplicado)`. La usan el webhook, «Actualizar estado»
    y el sondeo periódico.

    `tracking` = respuesta de `GET /shipments/{code}/tracking` (eventos del
    transportista): se guarda el último escaneo real para ENSEÑARLO; no mueve
    el pedido de pestaña. Idempotente."""
    summary = summarize_shipment(shipment)
    incidencia = str(_pick(shipment, _INCIDENCIA_KEYS) or "").strip()
    if summary["tracking"]:
        order.tracking_number = summary["tracking"]
    carrier = summarize_tracking(tracking) if tracking is not None else None
    target = transport_target(summary["state_bucket"], _transport_value(order))
    evidence = {
        "tracking_number": summary["tracking"] or order.tracking_number or "",
        "description": incidencia or summary["state_label"] or "Incidencia de transporte",
    }
    if carrier is not None and carrier["carrier_status"]:
        evidence["carrier_status"] = carrier["carrier_status"]
    applied = advance_transport(session, order, target, evidence=evidence)
    patch: dict[str, Any] = {
        "state_code": summary["state_code"],
        "state_bucket": summary["state_bucket"],
        "state_label": summary["state_label"],
        "tracking": summary["tracking"],
        "courier": summary["courier"],
        # Incidencia de TRANSPORTE (distinta de una incidencia de pedido/taller).
        "desc_incidencia": incidencia or None,
        "refreshed_at": now_iso(),
    }
    if carrier is not None:
        step = carrier["carrier_step"]
        patch.update({
            "carrier_status": carrier["carrier_status"],
            "carrier_status_code": carrier["carrier_status_code"],
            "carrier_status_at": carrier["carrier_status_at"],
            "carrier_step": step,
            "carrier_step_label": carrier_step_label(step) if step != UNKNOWN else None,
            "carrier_events": carrier["carrier_events"],
            "tracking_url": carrier["tracking_url"],
            "tracking_checked_at": now_iso(),
        })
    set_genei_state(order, patch)
    return summary, applied


def safe_tracking(client: Any, shipment_code: str) -> dict[str, Any] | None:
    """`GET /shipments/{code}/tracking` sin romper nada: si Genei falla (aún sin
    eventos, caído, credenciales…), None y se sigue con el estado de Genei."""
    from app.erp.integrations.genei.client import GeneiError  # noqa: PLC0415

    try:
        return client.get_tracking(shipment_code)
    except GeneiError as exc:
        logger.info("genei tracking %s no disponible: %s", shipment_code, exc)
        return None


def find_order(session: Session, shipment: dict[str, Any]) -> Order | None:
    """Localiza el pedido del payload: por `codigo_envio_externo` (= nº de
    pedido, o su id) y, si no, por el `codigo_envio` guardado en el bloque
    genei del pedido."""
    external = _pick(shipment, _EXTERNAL_KEYS)
    if external is not None:
        ref = str(external).strip()
        order = session.scalar(select(Order).where(Order.order_number == ref))
        if order is None:
            order = session.get(Order, ref)
        if order is not None:
            return order
    code = _pick(shipment, _SHIP_CODE_KEYS)
    if code:
        ref = str(code).strip()
        for candidate in session.scalars(
            select(Order).where(Order.packing_json.like(f"%{ref}%"))
        ):
            if genei_state_of(candidate).get("shipment_code") == ref:
                return candidate
    return None


def process_webhook(
    session: Session, payload: Any, *,
    fetch_tracking: Callable[[str], dict[str, Any] | None] | None = None,
) -> dict[str, Any]:
    """Procesa una llamada del webhook de Genei (ya validada). Localiza el
    pedido, aplica el estado y devuelve el resultado. No hace commit (lo hace el
    endpoint) ni valida el secreto (lo hace el endpoint).

    El webhook trae solo el estado de Genei; con `fetch_tracking(código)` se
    leen además los eventos del transportista (para enseñar el estado real).
    Si falla, se sigue sin ellos."""
    shipment = _shipment_data(payload)
    order = find_order(session, shipment)
    if order is None:
        logger.warning("genei webhook: pedido no encontrado (externo=%s codigo=%s)",
                       _pick(shipment, _EXTERNAL_KEYS), _pick(shipment, _SHIP_CODE_KEYS))
        return {"matched": False}
    tracking = None
    code = genei_state_of(order).get("shipment_code") or _pick(shipment, _SHIP_CODE_KEYS)
    if fetch_tracking is not None and code:
        tracking = fetch_tracking(str(code))
    summary, applied = apply_shipment_state(session, order, shipment, tracking=tracking)
    set_genei_state(order, {"webhook_at": now_iso()})
    logger.info("genei webhook: pedido %s → %s (transporte %s)",
                order.id, summary["state_label"], "movido" if applied else "sin cambio")
    return {"matched": True, "order_id": order.id, "summary": summary,
            "transport_applied": applied}
