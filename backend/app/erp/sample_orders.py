"""Pedidos de MUESTRA / envíos no facturables.

Un pedido de muestra sirve para mandar una muestra a un cliente o prospecto, o
un envío fuera de facturación (una pieza olvidada, un repuesto de cortesía,
material de prueba). Su único objetivo es **prepararlo y enviarlo** desde el
taller.

Qué lo distingue de un pedido corriente:

- **No es facturable.** No lleva empresa vinculada a FACTUSOL, ni serie, ni
  NIF, y NO escribe nada en FACTUSOL: sin cliente F_CLI, sin albarán, sin
  factura y sin cobro. Esos pasos salen «No aplica» y sus acciones se rechazan.
- **Numeración propia** `MUESTRA-000001`, para que se distinga de un vistazo de
  los manuales (`MANUAL-…`) y de los web.
- **Entra directo a la Cola SAT** al crearse: no hay puerta de facturación que
  esperar.
- **Destinatario libre**: basta un nombre y una dirección; vincular una
  empresa/contacto del CRM es opcional.

El tipo vive en `Order.order_kind` (NULL = pedido normal facturable), ortogonal
a `external_source`, que sigue diciendo el ORIGEN (web/manual/FACTUSOL).
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.erp.models.orders import Order

#: Valor de `Order.order_kind` para una muestra / envío no facturable.
ORDER_KIND_SAMPLE = "sample"

#: Prefijo + ancho del secuencial de las muestras (mismo formato que
#: `MANUAL-000001`, para que las dos series se lean igual).
SAMPLE_ORDER_PREFIX = "MUESTRA-"
SAMPLE_ORDER_PAD = 6

#: Motivo que se guarda en `packing_json` (por qué se manda la muestra).
SAMPLE_REASON_KEY = "sample_reason"

#: Código de error cuando se intenta una acción fiscal sobre una muestra.
NOT_BILLABLE_CODE = "order_not_billable"
NOT_BILLABLE_DETAIL = (
    "Este pedido es una muestra / envío no facturable: no lleva albarán, "
    "factura ni cobro. Si hay que facturarlo, créalo como pedido normal."
)


def is_sample_order(order: Any) -> bool:
    """¿Es una muestra / envío no facturable?"""
    return str(getattr(order, "order_kind", None) or "") == ORDER_KIND_SAMPLE


def is_billable(order: Any) -> bool:
    """¿Este pedido pasa por facturación? Hoy solo las muestras quedan fuera."""
    return not is_sample_order(order)


def next_sample_number(session: Session) -> str:
    """Siguiente `MUESTRA-000001` libre: max(secuencial) + 1 sobre los que ya
    siguen el patrón. El choque real lo corta el 409 del endpoint (misma
    estrategia que la numeración manual)."""
    rows = session.scalars(
        select(Order.order_number).where(
            Order.order_number.like(f"{SAMPLE_ORDER_PREFIX}%")
        )
    )
    top = 0
    for number in rows:
        suffix = (number or "")[len(SAMPLE_ORDER_PREFIX):]
        if suffix.isdigit():
            top = max(top, int(suffix))
    return f"{SAMPLE_ORDER_PREFIX}{top + 1:0{SAMPLE_ORDER_PAD}d}"


def not_billable_error() -> Any:
    """409 uniforme para las acciones fiscales sobre una muestra."""
    from fastapi import HTTPException, status  # noqa: PLC0415

    return HTTPException(status.HTTP_409_CONFLICT, {
        "code": NOT_BILLABLE_CODE, "detail": NOT_BILLABLE_DETAIL,
    })


def reject_if_sample(order: Any) -> None:
    """Corta con 409 si el pedido es una muestra. Se usa en los endpoints de
    albarán / factura / cobro, que no aplican a un envío no facturable."""
    if is_sample_order(order):
        raise not_billable_error()
