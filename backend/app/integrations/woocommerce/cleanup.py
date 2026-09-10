"""ERP · WooCommerce — limpieza de los pedidos importados con la regla antigua.

Hasta la regla «solo se crea en `processing`», BoHub creaba el pedido en
CUALQUIER estado de WooCommerce (`pending`, `on-hold`…). Esto localiza los ya
importados cuyo `woo_status` NO es `processing` ni `completed` y los saca del
seguimiento con la exclusión reversible de F6-fix7 (`seguimiento_excluded_*`,
misma que el botón «Excluir»; se deshace con «Reincluir»). NO borra nada.

Red de seguridad — NUNCA se toca un pedido con algo aguas abajo, aunque su
estado sea raro: facturado, cobrado/pagado, con albarán/envío o etiquetas, con
excepción/tarea SAT, ya escrito en la hoja de Drive, o ya trabajado en
preparación. Esos se listan aparte con su motivo.

Los pedidos SIN `woo_status` (importados antes de #376) no se conocen: no son
candidatos; se cuentan aparte para reconciliarlos primero.

`dry_run=True` (por defecto) solo lista y cuenta.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.erp.models import (
    ErpDriveSyncRow,
    ErpException,
    InvoiceStatus,
    Order,
    OrderSource,
    PaymentStatus,
    PreparationStatus,
    ShipmentFile,
    ShipmentPackage,
)
from app.models.crm import Company, Contact
from app.models.integration_settings import IntegrationAccount

logger = logging.getLogger(__name__)

#: Estados que NO se limpian: el flujo normal (processing → completed).
KEEP_STATUSES: frozenset[str] = frozenset({"processing", "completed"})

#: Motivo que queda en `seguimiento_excluded_reason` (para poder localizarlos y
#: reincluirlos en bloque si hiciera falta).
EXCLUSION_REASON_PREFIX = "limpieza woo no-processing"

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
    """Por qué NO se puede tocar este pedido (vacío = limpio aguas abajo)."""
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
    if session.scalar(select(exists().where(ErpDriveSyncRow.order_id == order.id))):
        reasons.append("escrito en Drive")
    if _val(order.preparation_status) in _WORKED:
        reasons.append(f"en preparación ({_val(order.preparation_status)})")
    return reasons


def _client_name(session: Session, order: Order) -> str:
    company = session.get(Company, order.company_id) if order.company_id else None
    if company is not None and company.name:
        return company.name
    contact = session.get(Contact, order.contact_id) if order.contact_id else None
    if contact is not None:
        return " ".join(p for p in (contact.first_name, contact.last_name) if p) or (
            contact.email or ""
        )
    return ""


def _row(session: Session, order: Order, reasons: list[str]) -> dict[str, Any]:
    return {
        "order_id": order.id,
        "order_number": order.order_number,
        "cliente": _client_name(session, order),
        "woo_status": order.woo_status,
        "importe": float(order.total_amount or 0),
        "preparation_status": _val(order.preparation_status),
        "motivos": reasons,
    }


def cleanup_non_processing_orders(
    session: Session, *, dry_run: bool = True, store_account_id: str | None = None,
    actor_user_id: str | None = None,
) -> dict[str, Any]:
    """Localiza (y con `dry_run=False` EXCLUYE del seguimiento) los pedidos Woo
    importados que no están en `processing`/`completed` y no tienen nada aguas
    abajo. Idempotente: los ya excluidos no se cuentan ni se vuelven a tocar."""
    stmt = select(Order).where(
        Order.external_source == OrderSource.WOOCOMMERCE,
        Order.seguimiento_excluded_at.is_(None),
    )
    if store_account_id:
        store = session.scalar(select(IntegrationAccount).where(
            IntegrationAccount.account_id == store_account_id,
        ))
        if store is None:
            return {"ok": False, "error": f"tienda {store_account_id!r} no encontrada"}
        stmt = stmt.where(Order.store_id == store.id)

    candidates: list[dict[str, Any]] = []
    protected: list[dict[str, Any]] = []
    sin_estado = 0
    por_estado: dict[str, int] = {}
    protegidos_por_motivo: dict[str, int] = {}

    for o in session.scalars(stmt):
        st = (o.woo_status or "").strip().lower()
        if not st:
            sin_estado += 1
            continue
        if st in KEEP_STATUSES:
            continue
        reasons = downstream_reasons(session, o)
        row = _row(session, o, reasons)
        if reasons:
            protected.append(row)
            for r in reasons:
                key = r.split(" (")[0]
                protegidos_por_motivo[key] = protegidos_por_motivo.get(key, 0) + 1
            continue
        candidates.append(row)
        por_estado[st] = por_estado.get(st, 0) + 1

    excluded = 0
    if not dry_run and candidates:
        now = datetime.now(UTC).replace(tzinfo=None)
        ids = [c["order_id"] for c in candidates]
        for o in session.scalars(select(Order).where(Order.id.in_(ids))):
            if o.seguimiento_excluded_at is not None:
                continue  # idempotente (carrera con otra limpieza)
            o.seguimiento_excluded_at = now
            o.seguimiento_excluded_by_user_id = actor_user_id
            o.seguimiento_excluded_reason = (
                f"{EXCLUSION_REASON_PREFIX}: Woo «{o.woo_status}», importado con "
                "la regla antigua"
            )
            excluded += 1
        session.commit()

    logger.info(
        "woo cleanup%s: %s candidatos (%s), %s protegidos, %s sin estado%s",
        " (preview)" if dry_run else "", len(candidates), por_estado,
        len(protected), sin_estado,
        f", {excluded} excluidos" if not dry_run else "",
    )
    candidates.sort(key=lambda r: (r["woo_status"] or "", r["order_number"]))
    protected.sort(key=lambda r: (r["woo_status"] or "", r["order_number"]))
    return {
        "ok": True,
        "preview": dry_run,
        "candidates": candidates,
        "por_estado": dict(sorted(por_estado.items())),
        "protected": protected,
        "protegidos_por_motivo": dict(sorted(protegidos_por_motivo.items())),
        "sin_estado": sin_estado,
        "excluded": excluded,
    }
