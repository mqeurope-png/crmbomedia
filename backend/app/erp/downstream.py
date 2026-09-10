"""ERP — qué tiene un pedido «aguas abajo» (factura, cobro, albarán, SAT, Drive…).

Sirve de AVISO (nunca de bloqueo) al «Quitar del seguimiento» a mano y de red
de seguridad en la limpieza por lotes de pedidos Woo (#387). Bart manda: se
enseña lo que hay y se deja continuar.

Sobre «escrito en Drive» (`DRIVE_REASON`) — cómo se calcula y por qué falla:

- Significa que existe una fila en `erp_drive_sync_rows` con `synced_at`.
- Esa fila la crea la sincronización con la hoja en DOS casos: (1) BoHub
  insertó el pedido en la hoja; (2) la sincronización decidió que el pedido
  YA ESTABA en la hoja y lo marcó sin escribir nada (para no duplicarlo).
- El caso (2) se decide emparejando por NÚMERO DESNUDO — `FLUXLA-5781` →
  `5781`, se quita el prefijo de tienda (`extract_order_number`) — y basta UN
  dato secundario que coincida: factura, cliente (comparación tolerante,
  `clients_match`) o fecha de entrada (mismo día).
- Falsos positivos: si dos tiendas comparten número (BOPRIN-5781 y
  FLUXLA-5781) y la fila de la hoja es de la OTRA, la misma fecha o un
  cliente parecido casan la fila equivocada y marcan «escrito» un pedido que
  no está. Y la marca nunca se borra, aunque la fila desaparezca de la hoja.

Por eso «escrito en Drive» es INFORMATIVO (`hard_reasons` lo excluye): ni
protege en la limpieza ni bloquea nada; el control manual lo sustituye.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.erp.models import (
    ErpDriveSyncRow,
    ErpException,
    InvoiceStatus,
    Order,
    PaymentStatus,
    PreparationStatus,
    ShipmentFile,
    ShipmentPackage,
)

#: Motivo informativo (no es protección): ver el docstring del módulo.
DRIVE_REASON = "escrito en Drive"

_INVOICED = {
    InvoiceStatus.GENERATED.value, InvoiceStatus.INVOICED_BY_ERP.value,
    InvoiceStatus.ALREADY_INVOICED_EXTERNALLY.value, InvoiceStatus.CREDIT_NOTE.value,
}
_PAID = {PaymentStatus.PAID.value, PaymentStatus.PARTIAL_PAID.value}
_WORKED = {
    PreparationStatus.IN_QUEUE.value, PreparationStatus.PREPARING.value,
    PreparationStatus.PACKED.value, PreparationStatus.BLOCKED.value,
}


def _val(v: Any) -> str:
    return str(getattr(v, "value", v) or "")


def downstream_reasons(session: Session, order: Order) -> list[str]:
    """Todo lo que el pedido tiene aguas abajo (vacío = limpio). Incluye el
    informativo «escrito en Drive»; usa `hard_reasons` para quedarte solo con
    lo que de verdad existe (factura, cobro, albarán, SAT, preparación)."""
    reasons: list[str] = []
    if order.factusol_invoice_number or _val(order.invoice_status) in _INVOICED:
        reasons.append("facturado")
    if _val(order.payment_status) in _PAID:
        reasons.append("cobrado/pagado")
    if session.scalar(select(exists().where(ShipmentPackage.order_id == order.id))):
        reasons.append("albarán/envío")
    if session.scalar(select(exists().where(ShipmentFile.order_id == order.id))):
        reasons.append("albarán/etiqueta guardada")
    if session.scalar(select(exists().where(ErpException.order_id == order.id))):
        reasons.append("excepción/tarea SAT")
    if session.scalar(select(exists().where(
        ErpDriveSyncRow.order_id == order.id, ErpDriveSyncRow.synced_at.isnot(None),
    ))):
        reasons.append(DRIVE_REASON)
    if _val(order.preparation_status) in _WORKED:
        reasons.append(f"en preparación ({_val(order.preparation_status)})")
    return reasons


def hard_reasons(reasons: list[str]) -> list[str]:
    """Los motivos que sí son hechos (sin el informativo «escrito en Drive»)."""
    return [r for r in reasons if r != DRIVE_REASON]
