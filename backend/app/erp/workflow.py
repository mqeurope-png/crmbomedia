"""ERP · «lo que toca hacer» de un pedido (rediseño de flujo, Fase 1).

El operador leía cuatro estados (pago, preparación, transporte, factura) más
el bloque FACTUSOL y deducía el siguiente paso. Aquí se calcula UNA vez, en el
backend, y lo consumen igual la bandeja y la ficha: misma cola, misma acción
sugerida y las mismas alertas en los dos sitios.

**No cambia ninguna regla de negocio**: solo LEE el estado que ya existe
(`Order` + su bloque FACTUSOL + sus líneas + la empresa) y lo traduce a:

- `queue`: en qué cola de trabajo cae (`por_revisar`, `por_facturar`,
  `por_cobrar`, `por_enviar`, `incidencias`, `listo`);
- `next_action`: la acción concreta que toca, con su etiqueta;
- `alerts[]`: incidencias accionables (cada una con `code`, texto y la acción
  que las resuelve).

Un pedido con alerta BLOQUEANTE cae en `incidencias` aunque su siguiente paso
sea otro: es lo que hay que mirar primero. Las alertas informativas (el aviso
de IVA de un intracomunitario, por ejemplo) se enseñan sin sacarlo de su cola.

Se calcula sin tocar FACTUSOL: todo sale de la BD (el estado de cobro es el
último conocido, `factusol_cobro_status`). Las comprobaciones que exigen leer
F_CLI en vivo (datos CRM ≠ FACTUSOL) las hace la ficha de empresa, que ya las
tiene, y llegarán al bloque en su fase.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.erp.models import (
    InvoiceStatus,
    Order,
    PaymentStatus,
    PreparationStatus,
    TransportStatus,
)

# --- colas -------------------------------------------------------------------

QUEUE_POR_REVISAR = "por_revisar"
QUEUE_POR_FACTURAR = "por_facturar"
QUEUE_POR_COBRAR = "por_cobrar"
QUEUE_POR_ENVIAR = "por_enviar"
QUEUE_INCIDENCIAS = "incidencias"
QUEUE_LISTO = "listo"

#: Orden de las colas en la bandeja (el de la maqueta).
QUEUES: tuple[str, ...] = (
    QUEUE_POR_REVISAR, QUEUE_POR_FACTURAR, QUEUE_POR_COBRAR,
    QUEUE_POR_ENVIAR, QUEUE_INCIDENCIAS, QUEUE_LISTO,
)

QUEUE_LABELS: dict[str, str] = {
    QUEUE_POR_REVISAR: "Por revisar",
    QUEUE_POR_FACTURAR: "Por facturar",
    QUEUE_POR_COBRAR: "Por cobrar",
    QUEUE_POR_ENVIAR: "Por enviar",
    QUEUE_INCIDENCIAS: "Incidencias",
    QUEUE_LISTO: "Listo",
}

# --- acciones ----------------------------------------------------------------

ACTION_LABELS: dict[str, str] = {
    "aprobar": "Aprobar",
    "emitir_factura": "Emitir factura",
    "registrar_cobro": "Registrar cobro",
    # Lote 2 C: «Crear envío» ya no es un botón; subir la etiqueta ES el envío
    # (la subida mueve el transporte a label_created). Con la etiqueta ya
    # subida, la etiqueta del paso cambia a «Marcar recogido» (ver
    # `_next_action_label`), misma acción y mismo sitio (la ficha).
    "crear_envio": "Subir etiqueta",
    "enviar_sat": "Enviar a SAT",
    "marcar_completado": "Marcar completado",
    "vincular_empresa": "Vincular empresa a FACTUSOL",
    "revisar_incidencia": "Revisar incidencia",
    "crear_albaran": "Crear albarán en FACTUSOL",
    "revalidar_vies": "Revalidar en VIES",
    "ninguna": "Sin acción pendiente",
}

# --- estados que cuentan como «ya está» --------------------------------------

_INVOICED = {
    InvoiceStatus.GENERATED.value,
    InvoiceStatus.INVOICED_BY_ERP.value,
    InvoiceStatus.ALREADY_INVOICED_EXTERNALLY.value,
    InvoiceStatus.CREDIT_NOTE.value,
}
_PAID = {
    PaymentStatus.PAID.value,
    PaymentStatus.CREDIT_APPROVED.value,
}
_SHIPPED = {
    TransportStatus.IN_TRANSIT.value,
    TransportStatus.DELIVERED.value,
    TransportStatus.ALREADY_SHIPPED_EXTERNALLY.value,
}
_PREPARED = {
    PreparationStatus.PACKED.value,
    PreparationStatus.ALREADY_COMPLETED_EXTERNALLY.value,
}


def _v(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


def is_invoiced(order: Order) -> bool:
    """Facturado de verdad: el estado lo dice, o hay nº de factura FACTUSOL."""
    return _v(order.invoice_status) in _INVOICED or bool(order.factusol_invoice_number)


def is_paid(order: Order) -> bool:
    return _v(order.payment_status) in _PAID


def is_shipped(order: Order) -> bool:
    return _v(order.transport_status) in _SHIPPED


def is_approved(order: Order) -> bool:
    """Aprobado = salió de la cola de revisión (o ya está más adelante)."""
    if order.approved_at is not None:
        return True
    return _v(order.preparation_status) != PreparationStatus.PENDING_REVIEW.value


def is_cobrada(order: Order) -> bool:
    """La factura consta COBRADA en FACTUSOL (último estado conocido)."""
    return (order.factusol_cobro_status or "") == "cobrada"


def is_web_order(order: Order) -> bool:
    return _v(order.external_source) == "woocommerce"


# --- alertas -----------------------------------------------------------------


def _alert(
    code: str, text: str, *, action: str | None = None, blocking: bool = False,
) -> dict[str, Any]:
    return {
        "code": code, "text": text, "action": action,
        "action_label": ACTION_LABELS.get(action or "", None),
        "blocking": blocking,
    }


def order_alerts(
    session: Session, order: Order, *, ctx: WorkflowContext | None = None,
) -> list[dict[str, Any]]:
    """Incidencias accionables del pedido, de la más grave a la más leve.

    `blocking=True` manda el pedido a la cola «Incidencias»; las demás solo se
    enseñan (la del IVA intracomunitario, por ejemplo, es información para no
    facturar mal, no un bloqueo).

    `ctx` evita el N+1 de la bandeja (empresas y excepciones en dos queries)."""
    alerts: list[dict[str, Any]] = []

    # El mapeo de líneas a artículo de FACTUSOL NO cuenta para el flujo: una
    # línea sin CODART se emite como texto libre (ERP-E2-fix1), así que ni es
    # incidencia, ni cola, ni acción, ni bloqueo.

    company = _company_of(session, order, ctx)
    # Empresa archivada (limpieza): no genera alertas ni incidencias («sin
    # vincular a FACTUSOL» incluido). El pedido sigue su ciclo por su estado.
    archived = bool(company is not None and getattr(company, "is_archived", False))

    # 1) Empresa sin vincular a cliente de F_CLI: sin CODCLI no hay albarán ni
    #    factura posibles para ese cliente.
    if company is not None and not archived and not (company.factusol_company_id or ""):
        alerts.append(_alert(
            "empresa_sin_vincular",
            f"«{company.name}» no está vinculada a un cliente de FACTUSOL.",
            action="vincular_empresa", blocking=True,
        ))

    # 2) Régimen de IVA del cliente: un intracomunitario / exportación factura
    #    SIN IVA. Informativo, pero es lo que evita una factura mal emitida.
    #    Fase VIES: si VIES dice que el NIF-IVA NO es válido no se puede
    #    eximir → incidencia BLOQUEANTE (se facturaría mal el IVA); si aún no
    #    está validado (pendiente / VIES caído) se avisa sin bloquear.
    regime = company_regime(company) if not archived else None
    vies = _vies_of(company) if not archived else {"vat": None, "status": None}
    if vies["vat"] and vies["status"] == "no_valido":
        alerts.append(_alert(
            "vat_no_valido_vies",
            f"El NIF-IVA {vies['vat']} NO es válido en VIES: no se puede eximir de "
            "IVA; se trata como nacional con IVA.",
            action="revalidar_vies", blocking=True,
        ))
    if regime == "intracomunitario":
        if vies["vat"] and vies["status"] == "valido":
            texto = ("Cliente intracomunitario (NIF-IVA verificado en VIES): la factura debe "
                     "salir sin IVA.")
        elif vies["vat"]:
            motivo = "VIES no respondió" if vies["status"] == "desconocido" else "sin validar"
            texto = ("Cliente intracomunitario con NIF-IVA pendiente de validar en VIES "
                     f"({motivo}): la factura saldría sin IVA — valida el NIF-IVA antes de emitir.")
        else:
            texto = "Cliente intracomunitario: la factura debe salir sin IVA."
        alerts.append(_alert(
            "cliente_intracomunitario", texto,
            action="revalidar_vies" if (vies["vat"] and vies["status"] != "valido") else None,
        ))
    elif regime == "exportacion":
        alerts.append(_alert(
            "cliente_exportacion",
            "Cliente de exportación: la factura debe salir sin IVA.",
        ))

    # 4) Cobrado en el CRM pero sin cobro registrado en FACTUSOL: descuadre
    #    contable silencioso.
    if (
        is_paid(order) and is_invoiced(order)
        and order.factusol_invoice_number and not is_cobrada(order)
    ):
        alerts.append(_alert(
            "cobro_no_registrado",
            "El pedido consta pagado pero la factura no está cobrada en FACTUSOL.",
            action="registrar_cobro",
        ))

    # 5) Excepción operativa abierta (SAT / transporte / facturación).
    blocker = _open_exception(session, order, ctx)
    if blocker:
        alerts.append(_alert(
            "excepcion_abierta", blocker, action="revisar_incidencia", blocking=True,
        ))
    return alerts


# --- Proformas (Fase 4): colas de la pantalla Proformas --------------------------
#
# Mismo criterio que la bandeja: la cola dice «lo que toca». Una proforma
# aceptada está POR CONVERTIR mientras no exista su pedido en BoHub; en
# cuanto existe pasa a «convertidas» (el equivalente a «listo»). Pendientes y
# rechazadas van por el estado `ESTPRE` de FACTUSOL.

QUOTE_QUEUE_ACEPTADAS = "aceptadas"
QUOTE_QUEUE_PENDIENTES = "pendientes"
QUOTE_QUEUE_RECHAZADAS = "rechazadas"
QUOTE_QUEUE_CONVERTIDAS = "convertidas"
QUOTE_QUEUES: tuple[str, ...] = (
    QUOTE_QUEUE_ACEPTADAS, QUOTE_QUEUE_PENDIENTES, QUOTE_QUEUE_RECHAZADAS,
    QUOTE_QUEUE_CONVERTIDAS,
)
QUOTE_QUEUE_LABELS: dict[str, str] = {
    QUOTE_QUEUE_ACEPTADAS: "Aceptadas · por convertir",
    QUOTE_QUEUE_PENDIENTES: "Pendientes de respuesta",
    QUOTE_QUEUE_RECHAZADAS: "Rechazadas",
    QUOTE_QUEUE_CONVERTIDAS: "Convertidas",
}


def quote_queue(estado: str, *, converted: bool) -> str | None:
    """Cola de una proforma por su estado FACTUSOL y si ya es pedido de BoHub.
    Un estado no reconocido (`otro`) no tiene cola (solo sale en «todas»)."""
    if converted:
        return QUOTE_QUEUE_CONVERTIDAS
    return {
        "aceptada": QUOTE_QUEUE_ACEPTADAS,
        "pendiente": QUOTE_QUEUE_PENDIENTES,
        "rechazada": QUOTE_QUEUE_RECHAZADAS,
    }.get(estado)


def company_regime(company: Any) -> str | None:
    """Régimen de IVA del cliente del pedido (Tarea C + VIES), o None sin
    empresa / sin país."""
    if company is None or not getattr(company, "country", None):
        return None
    from app.erp.language import normalize_country  # noqa: PLC0415
    from app.integrations.factusol.vat_regime import regime_for  # noqa: PLC0415
    from app.services.vies import company_vies_valid  # noqa: PLC0415

    return regime_for(
        normalize_country(company.country),
        vat=getattr(company, "vat", None), nif=getattr(company, "tax_id", None),
        vies_valid=company_vies_valid(company),
    )


def _vies_of(company: Any) -> dict[str, Any]:
    """Estado VIES aplicable al NIF-IVA actual de la empresa (o vacío)."""
    if company is None:
        return {"vat": None, "status": None}
    from app.services.vies import vies_state  # noqa: PLC0415

    state = vies_state(company)
    return {"vat": state["vat"], "status": state["status"]}


def _open_exception(
    session: Session, order: Order, ctx: WorkflowContext | None = None,
) -> str | None:
    """Texto de la excepción abierta que bloquea, o None. Mismo criterio que
    la Cola PEDIDOS (`_open_exception_blocker`)."""
    if ctx is not None:
        n = ctx.exception_counts.get(order.id, 0)
        return f"{n} excepción(es) sin resolver" if n else None
    from app.erp.api.orders import _open_exception_blocker  # noqa: PLC0415

    blocker = _open_exception_blocker(session, order)
    if not blocker:
        return None
    return str(blocker.get("detail") or blocker.get("message") or blocker.get("code"))


# --- cola + siguiente acción -------------------------------------------------


def _next_step(order: Order) -> tuple[str, str, str]:
    """`(cola, acción, explicación)` del pedido según por dónde va el ciclo.

    El orden es el del ciclo real: revisar → facturar → cobrar → enviar →
    completar. No decide nada nuevo: cada rama refleja el estado que ya
    gobierna los botones de la ficha."""
    if not is_approved(order):
        return (
            QUEUE_POR_REVISAR, "aprobar",
            "Revisa el pedido y apruébalo para que pase al taller.",
        )
    if not is_invoiced(order):
        return (
            QUEUE_POR_FACTURAR, "emitir_factura",
            "Emite la factura en FACTUSOL.",
        )
    # Cobro: SIEMPRE manual («Registrar cobro»), y solo tiene sentido cuando
    # hay una factura FACTUSOL sobre la que registrarlo. Una factura marcada
    # como emitida fuera del ERP no se puede cobrar desde aquí: se sigue.
    if not is_cobrada(order) and order.factusol_invoice_number:
        return (
            QUEUE_POR_COBRAR, "registrar_cobro",
            "El pedido consta pagado: registra el cobro en FACTUSOL."
            if is_paid(order) else
            "Registra el cobro de la factura en FACTUSOL.",
        )
    if not is_shipped(order):
        if _v(order.preparation_status) not in _PREPARED:
            return (
                QUEUE_POR_ENVIAR, "enviar_sat",
                "El taller tiene que preparar el pedido.",
            )
        # Lote 2 C: subir la etiqueta ES «Crear envío». Con ella ya subida
        # (label_created) lo que falta es que el paquete salga.
        texto = (
            "Etiqueta subida: marca el pedido como recogido cuando salga."
            if _v(order.transport_status) == TransportStatus.LABEL_CREATED.value
            else "Sube la etiqueta de envío (o marca el pedido como recogido)."
        )
        return QUEUE_POR_ENVIAR, "crear_envio", texto
    if not order.completed_at:
        return (
            QUEUE_POR_ENVIAR, "marcar_completado",
            "Todo hecho: márcalo como completado.",
        )
    return QUEUE_LISTO, "ninguna", "Nada pendiente."


def order_workflow(
    session: Session, order: Order, *, ctx: WorkflowContext | None = None,
) -> dict[str, Any]:
    """Bloque `workflow` del pedido: cola, siguiente acción y alertas.

    Es el ÚNICO sitio que decide el estado de flujo; bandeja y ficha lo
    consumen tal cual."""
    company = _company_of(session, order, ctx)
    alerts = order_alerts(session, order, ctx=ctx)
    queue, action, explain = _next_step(order)
    blocking = [a for a in alerts if a["blocking"]]
    if getattr(order, "cancelled_at", None):
        # Anulado (estado final reversible, distinto de «quitar»): nada que
        # hacer; las listas de trabajo ya no lo enseñan.
        queue, action, explain = QUEUE_LISTO, "ninguna", "Pedido anulado."
    elif order.completed_at:
        # Completado a mano: fuera de las colas de trabajo aunque quede algo
        # suelto (es el estado final que decide Bart).
        queue, action, explain = QUEUE_LISTO, "ninguna", "Pedido completado."
    elif blocking:
        # Lo que bloquea manda: la incidencia se resuelve antes que el ciclo.
        queue = QUEUE_INCIDENCIAS
        action = blocking[0]["action"] or "revisar_incidencia"
        explain = blocking[0]["text"]
    return {
        "queue": queue,
        "queue_label": QUEUE_LABELS[queue],
        "next_action": action,
        "next_action_label": _next_action_label(order, action),
        "next_action_hint": explain,
        "alerts": alerts,
        "blocked": bool(blocking),
        # Pasos del ciclo para el stepper de la ficha (7 de la maqueta).
        "steps": order_steps(order),
        "regime": company_regime(company),
        # Cliente del pedido: lo que la ficha enseña en cabecera (país +
        # régimen) y en el bloque FACTUSOL (nº de cliente o «sin vincular»).
        "company": _company_block(company),
    }


def _next_action_label(order: Order, action: str) -> str:
    """Etiqueta del siguiente paso. Lote 2 C: `crear_envio` se lee según el
    sub-estado del transporte — «Subir etiqueta» mientras no hay envío y
    «Marcar recogido» con la etiqueta ya subida. La acción (y la cola) no
    cambian: es el mismo paso visto desde donde está el pedido."""
    if (
        action == "crear_envio"
        and _v(order.transport_status) == TransportStatus.LABEL_CREATED.value
    ):
        return "Marcar recogido"
    return ACTION_LABELS.get(action, action)


def _company_block(company: Any) -> dict[str, Any] | None:
    if company is None:
        return None
    return {
        "id": company.id,
        "name": company.name,
        "country": getattr(company, "country", None),
        "factusol_id": getattr(company, "factusol_company_id", None) or None,
        "regime": company_regime(company),
        "vies": _vies_of(company),
    }


class WorkflowContext:
    """Datos precargados para calcular el `workflow` de MUCHOS pedidos sin
    N+1: las empresas y el nº de excepciones abiertas, en dos queries."""

    def __init__(
        self, companies: dict[str, Any], exception_counts: dict[str, int],
    ) -> None:
        self.companies = companies
        self.exception_counts = exception_counts

    @classmethod
    def for_orders(cls, session: Session, orders: list[Order]) -> WorkflowContext:
        from sqlalchemy import func, select  # noqa: PLC0415

        from app.erp.models import ErpException, ExceptionStatus  # noqa: PLC0415
        from app.models.crm import Company  # noqa: PLC0415

        company_ids = {o.company_id for o in orders if o.company_id}
        companies: dict[str, Any] = {}
        if company_ids:
            companies = {
                c.id: c for c in session.scalars(
                    select(Company).where(Company.id.in_(company_ids))
                )
            }
        counts: dict[str, int] = {}
        order_ids = [o.id for o in orders]
        if order_ids:
            rows = session.execute(
                select(ErpException.order_id, func.count(ErpException.id))
                .where(
                    ErpException.order_id.in_(order_ids),
                    ErpException.status.in_(
                        [ExceptionStatus.OPEN, ExceptionStatus.IN_PROGRESS]
                    ),
                ).group_by(ErpException.order_id)
            )
            counts = {oid: int(n) for oid, n in rows}
        return cls(companies, counts)


def _company_of(
    session: Session, order: Order, ctx: WorkflowContext | None,
) -> Any:
    if not order.company_id:
        return None
    if ctx is not None:
        return ctx.companies.get(order.company_id)
    from app.models.crm import Company  # noqa: PLC0415

    return session.get(Company, order.company_id)


#: Los 7 pasos del ciclo, en orden (maqueta: «línea de vida» del pedido).
STEP_KEYS: tuple[str, ...] = (
    "creado", "pagado", "aprobado", "albaran", "factura", "cobro", "enviado",
)
STEP_LABELS: dict[str, str] = {
    "creado": "Creado", "pagado": "Pagado", "aprobado": "Aprobado",
    "albaran": "Albarán", "factura": "Factura", "cobro": "Cobro",
    "enviado": "Enviado",
}


def order_steps(order: Order) -> list[dict[str, Any]]:
    """Estado de cada paso del ciclo: `done` (hecho, con su detalle), `now`
    (el primero pendiente), `pending` o `skipped`.

    El albarán es el único opcional: en un pedido WEB lo crea WooCommerce, no
    BoHub, así que si no hay número se marca `skipped` («no aplica») en vez de
    dejarlo pendiente para siempre — y no puede ser nunca el paso actual."""
    done: dict[str, tuple[bool, str]] = {
        "creado": (True, _fecha(order.placed_at or order.created_at)),
        "pagado": (is_paid(order), "" if not is_paid(order) else _importe(order)),
        "aprobado": (is_approved(order), _fecha(order.approved_at)),
        "albaran": (
            bool(order.factusol_albaran_number),
            order.factusol_albaran_number or "",
        ),
        "factura": (
            is_invoiced(order),
            str(order.factusol_invoice_number or "") if is_invoiced(order) else "",
        ),
        "cobro": (is_cobrada(order), "cobrada" if is_cobrada(order) else ""),
        "enviado": (is_shipped(order), _v(order.transport_status) if is_shipped(order) else ""),
    }
    steps: list[dict[str, Any]] = []
    now_set = False
    for key in STEP_KEYS:
        ok, detail = done[key]
        if ok:
            state = "done"
        elif key == "albaran" and is_web_order(order):
            state = "skipped"
            detail = "lo crea WooCommerce"
        elif not now_set:
            state = "now"
            now_set = True
        else:
            state = "pending"
        steps.append({
            "key": key, "label": STEP_LABELS[key], "state": state,
            "detail": detail or None,
        })
    return steps


def _fecha(value: Any) -> str:
    return value.date().isoformat() if value is not None else ""


def _importe(order: Order) -> str:
    return f"{float(order.total_amount or 0):.2f} {order.currency or 'EUR'}"


def workflows_for(
    session: Session, orders: list[Order],
) -> dict[str, dict[str, Any]]:
    """`{order_id: workflow}` de una lista, en dos queries (bandeja)."""
    ctx = WorkflowContext.for_orders(session, orders)
    return {o.id: order_workflow(session, o, ctx=ctx) for o in orders}


def queue_counts(workflows: dict[str, dict[str, Any]]) -> dict[str, int]:
    """Contadores por cola para las pastillas de la bandeja."""
    counts = dict.fromkeys(QUEUES, 0)
    for wf in workflows.values():
        counts[wf["queue"]] += 1
    return counts
