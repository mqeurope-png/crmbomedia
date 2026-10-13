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

Qué mueve el transporte SOLO (ver `tracking.transport_target`): el envío
TRAMITADO de un pedido aún «sin enviar» (→ «etiqueta creada», sin esperar a
que alguien descargue la etiqueta), una INCIDENCIA (→ «Incidencias») y el
«entregado» de un pedido ya marcado como recogido (sigue en «Enviados»). El
paso a «Enviados» lo hace una persona con «📤 Marcar recogido» en la Cola SAT.
El tracking DETALLADO (`GET /shipments/{code}/tracking`: los eventos del propio
transportista) se guarda y se ENSEÑA; no mueve nada.

Idempotente: recibir el mismo estado dos veces no descuadra — un arco que ya se
recorrió no vuelve a aplicarse (no es un arco válido desde el estado actual).
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.erp.integrations.genei.service import (
    genei_state_of,
    now_iso,
    record_cancelled_and_unlink,
    set_genei_state,
    summarize_shipment,
)
from app.erp.integrations.genei.status import is_cancelled, is_unknown_state
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

#: Motivo de las transiciones que aplica Genei (webhook / estado / arreglo). La
#: hoja las cuenta como hechos reales (`seguimiento._SYSTEM_REAL_REASONS`).
GENEI_REASON = "Genei (webhook/estado)"

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
    label_at: datetime | None = None,
) -> bool:
    """Avanza el `transport_status` del pedido hasta `target` recorriendo los
    arcos válidos como SYSTEM (`actor=None`). Solo aplica un arco si el pedido
    está justo en su origen, así que es idempotente (un estado ya pasado no
    tiene arco) y no retrocede (un `in_transit` tras `delivered` no hace nada).
    Un arco que no se puede (guard/rol) detiene el avance sin romper.

    `label_at` = cuándo se tramitó de verdad el envío: el arco «etiqueta
    creada» queda fechado entonces y no al aplicarlo (la hoja usa esa fecha
    como «Fecha recogido» mientras nadie marque recogido)."""
    if not target or target not in _STEPS_TO:
        return False
    applied = False
    if label_at is not None:
        # Nunca antes del último hecho del pedido (p. ej. pagado antes de
        # embalar: «etiqueta creada» no puede quedar antes de «embalado»).
        previas = [_aware(h.changed_at) for h in order.status_history if h.changed_at]
        label_at = max([_aware(label_at), *previas])
    for frm, to in _STEPS_TO[target]:
        if _transport_value(order) != frm:
            continue
        try:
            row = apply_transition(
                session, order=order, domain=StatusDomain.TRANSPORT, to_status=to,
                actor=None, reason=GENEI_REASON, evidence=evidence,
            )
            if to == "label_created" and label_at is not None and label_at < _aware(row.changed_at):
                row.changed_at = label_at
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
    bloque `genei` + tracking y mueve el `transport_status` si toca (tramitado
    → «etiqueta creada», incidencia, o «entregado» de un pedido ya marcado como
    recogido; ver `transport_target`). Devuelve
    `(summary, transporte_aplicado)`. La usan el webhook, «Actualizar estado»
    y el sondeo periódico.

    `tracking` = respuesta de `GET /shipments/{code}/tracking` (eventos del
    transportista): se guarda el último escaneo real para ENSEÑARLO; no mueve
    el pedido de pestaña. Idempotente."""
    summary = summarize_shipment(shipment)
    incidencia = str(_pick(shipment, _INCIDENCIA_KEYS) or "").strip()
    stored_before = genei_state_of(order)

    # Evento TARDÍO o repetido de un envío ya ANULADO y archivado: se ignora. Si
    # no, un aviso fuera de orden (p. ej. un estado 5 que llega después del 11)
    # volvería a vincular el envío muerto y a moverle el transporte: justo el
    # zombi que esto evita. Un envío nuevo (otro código) no se toca.
    code_in = summary["shipment_code"]
    anulados = {a.get("shipment_code") for a in (stored_before.get("anulados") or [])
                if isinstance(a, dict) and a.get("shipment_code")}
    if code_in and code_in in anulados:
        logger.info("genei: evento de un envío ya anulado (%s) en el pedido %s: ignorado",
                    code_in, order.id)
        return summary, False

    # Anulado con devolución (Genei 11/12): el envío NO saldrá. Se suelta el
    # vínculo (la referencia queda libre), se archiva el rastro y el pedido
    # vuelve a «sin enviar» para crear/vincular otro. Nunca queda «entregable».
    if is_cancelled(summary["state_bucket"]):
        aplicado = _apply_cancelled(session, order, summary, incidencia)
        return summary, aplicado

    # Estado que la tabla no contempla: NO se traga. Se guarda como «estado
    # desconocido» (rastro en la ficha + lo ve el Cuadre) y no se toca el
    # transporte (no sabemos qué significa; no vamos a darlo por entregable).
    if is_unknown_state(summary["state_bucket"]):
        _apply_unknown_state(order, summary, stored_before=stored_before)
        logger.warning("genei: estado desconocido %s en el envío %s (pedido %s): se marca y se "
                       "avisa, no se mueve el transporte", summary["state_code"],
                       summary["shipment_code"], order.id)
        return summary, False

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
    applied = advance_transport(session, order, target, evidence=evidence,
                                label_at=tramitado_at(stored_before))
    patch: dict[str, Any] = {
        # SOLO si el pedido aún no tenía envío (p. ej. uno creado a mano en el
        # panel de Genei del que llega aviso): el código lo deja VINCULADO, que
        # sin él la ficha seguiría diciendo «sin envío» con el estado puesto.
        # Si ya tenía, NO se pisa: el webhook empareja por referencia externa y
        # un aviso tardío de un envío borrado dejaría el pedido apuntando a un
        # código muerto (etiqueta y «Actualizar estado» fallando con 404).
        "shipment_code": (summary["shipment_code"] or None)
        if not stored_before.get("shipment_code") else None,
        "state_code": summary["state_code"],
        "state_bucket": summary["state_bucket"],
        "state_label": summary["state_label"],
        "tracking": summary["tracking"],
        "courier": summary["courier"],
        # Incidencia de TRANSPORTE (distinta de una incidencia de pedido/taller).
        "desc_incidencia": incidencia or None,
        "refreshed_at": now_iso(),
    }
    # Fallo 2: la referencia externa que vuelve (`codigo_envio_externo`) tiene
    # que ser la que BoHub mandó al crear (`external_sent`). Si no casan, se deja
    # el desajuste anotado (la ficha/Cuadre lo ven): un envío mal enlazado no se
    # reconcilia. Solo informa; no se toca el vínculo por un dato que vuelve.
    externo_in = str(_pick(shipment, _EXTERNAL_KEYS) or "").strip()
    enviado = str(stored_before.get("external_sent") or "").strip()
    if externo_in and enviado and externo_in.upper() != enviado.upper():
        patch["external_mismatch"] = {"enviado": enviado, "devuelto": externo_in,
                                      "visto_en": now_iso()}
        logger.warning("genei: referencia externa distinta en el pedido %s: mandó %s, el webhook "
                       "trae %s", order.id, enviado, externo_in)
    # Destinatario y web de seguimiento que ya trae el objeto de Genei (para el
    # aviso de envío al cliente, sobre todo en envíos creados antes de guardarlos).
    stored = genei_state_of(order)
    email_llegada = str(shipment.get("email_llegada") or "").strip()
    if email_llegada and not stored.get("dest_email"):
        patch["dest_email"] = email_llegada
    web = str(shipment.get("web_seguimiento") or "").strip()
    if web.startswith(("http://", "https://")) and not stored.get("tracking_url"):
        patch["tracking_url"] = web
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


#: Estados de transporte desde los que un envío anulado vuelve a «sin enviar»
#: (aún no había salido de verdad). Si ya estaba recogido/entregado, no se toca
#: (contradicción rara: se archiva el anulado pero se deja el transporte).
_TRANSPORTE_SIN_SALIR = ("not_shipped", "label_created")


def _apply_cancelled(
    session: Session, order: Order, summary: dict[str, Any], incidencia: str,
) -> bool:
    """Archiva el envío anulado y suelta el vínculo; si el pedido no había
    salido, el transporte vuelve a «sin enviar» (corrección de SYSTEM, auditada
    e idempotente)."""
    motivo = incidencia or summary.get("state_label") or "Envío anulado en Genei"
    cambiado = record_cancelled_and_unlink(order, summary, reason=motivo, when=now_iso())
    if not cambiado:
        return False
    # El nº de seguimiento del envío muerto deja de ser el del pedido.
    if summary.get("tracking") and order.tracking_number == summary["tracking"]:
        order.tracking_number = None
    actual = _transport_value(order)
    if actual in _TRANSPORTE_SIN_SALIR:
        _reset_transport_not_shipped(session, order, actual, summary, motivo)
    _audit_cancelled(session, order, summary, motivo)
    logger.info("genei: envío %s anulado (%s) en el pedido %s: vínculo suelto, referencia libre",
                summary.get("shipment_code"), summary.get("state_label"), order.id)
    return True


def _reset_transport_not_shipped(
    session: Session, order: Order, desde: str, summary: dict[str, Any], motivo: str,
) -> None:
    """El envío anulado no saldrá: el pedido vuelve a «sin enviar». Es una
    corrección hacia atrás (no un arco de la máquina de estados), así que se
    escribe la columna y el historial a mano, como SYSTEM."""
    import json  # noqa: PLC0415

    from app.erp.models.orders import OrderStatusHistory, TransportStatus  # noqa: PLC0415

    if desde == TransportStatus.NOT_SHIPPED.value:
        return
    order.transport_status = TransportStatus.NOT_SHIPPED.value
    session.add(OrderStatusHistory(
        order_id=order.id, domain=StatusDomain.TRANSPORT,
        from_status=desde, to_status=TransportStatus.NOT_SHIPPED.value,
        changed_at=datetime.now(UTC), changed_by_user_id=None,
        reason=f"Envío Genei anulado ({summary.get('state_label') or motivo}): no saldrá",
        metadata_json=json.dumps({"shipment_code": summary.get("shipment_code"),
                                  "state_code": summary.get("state_code")}, default=str),
    ))


def _audit_cancelled(session: Session, order: Order, summary: dict[str, Any], motivo: str) -> None:
    import json  # noqa: PLC0415

    from app.models.crm import AuditLog  # noqa: PLC0415

    session.add(AuditLog(
        actor_user_id=None, actor_email=None, action="erp.genei.shipment_cancelled",
        target_type="order", target_id=order.id,
        metadata_json=json.dumps({"shipment_code": summary.get("shipment_code"),
                                  "state_code": summary.get("state_code"),
                                  "motivo": motivo}, default=str),
    ))


def _apply_unknown_state(
    order: Order, summary: dict[str, Any], *, stored_before: dict[str, Any],
) -> None:
    """Un código de estado no contemplado: se guarda tal cual (bucket
    `unknown`, etiqueta «Estado desconocido (N)») para que la ficha lo enseñe y
    el Cuadre lo cace. No se toca el transporte. Si el pedido aún no tenía envío
    (p. ej. uno del panel del que llega el primer aviso en un estado raro), se
    vincula el código: si no, el Cuadre —que exige `shipment_code`— no lo vería."""
    patch: dict[str, Any] = {
        "state_code": summary.get("state_code"),
        "state_bucket": summary.get("state_bucket"),
        "state_label": summary.get("state_label"),
        "estado_desconocido_en": now_iso(),
        "refreshed_at": now_iso(),
    }
    if not stored_before.get("shipment_code") and summary.get("shipment_code"):
        patch["shipment_code"] = summary["shipment_code"]
    set_genei_state(order, patch)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def tramitado_at(state: dict[str, Any]) -> datetime | None:
    """Cuándo se tramitó el envío, lo mejor que se sabe: el pago desde BoHub
    (`paid_at`); si se pagó en la web de Genei y BoHub ya lo tenía tramitado
    (se pone al día tarde), la creación del envío. None = ahora."""
    pagado = _iso(state.get("paid_at"))
    if pagado is not None:
        return pagado
    from app.erp.integrations.genei.status import LABEL_CREATED_BUCKETS  # noqa: PLC0415

    if state.get("state_bucket") in LABEL_CREATED_BUCKETS:
        return _iso(state.get("created_at"))
    return None


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
