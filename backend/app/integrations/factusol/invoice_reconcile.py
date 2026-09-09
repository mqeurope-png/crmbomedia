"""ERP · vincular a los pedidos las facturas creadas A MANO en FACTUSOL.

Contexto: cuando una factura se emite por BoHub (`emit-factusol-invoice`), el
pedido queda con `factusol_invoice_number` + `invoice_status` y el seguimiento
lo muestra «facturado». Pero una factura creada a mano directamente en FACTUSOL
NO llega a BoHub: la única vinculación existente es la consulta EN VIVO por
pedido (`get_and_link_factusol_status`, solo cuando alguien abre el pedido y con
`factusol_live` activo). No hay ninguna sincronización periódica: la cola
`factusol:sync_invoices` del worker NO tiene productor — es vestigial. Por eso
esas facturas manuales se quedan como «pendiente» y no se pueden enviar.

Esta reconciliación recorre los pedidos que BoHub tiene como NO facturados,
busca su factura en FACTUSOL por la REFERENCIA COMÚN del pedido (REFFAC =
`PREFIJO-NNNNNN`, el mismo `_compose_ref` que usa el enlace en vivo) y, si hay
UNA sola, la enlaza (escribe SOLO en BoHub, vía `_auto_link_factura`; NUNCA en
FACTUSOL). Si un pedido tiene MÁS de una factura con esa referencia, es un
conflicto: NO se enlaza y se reporta para que Bart decida.

Eficiencia: carga F_FAC UNA vez (gotcha nº1: `filtro='1=1'`) y cruza en memoria;
no hace una consulta por pedido. `dry_run=True` (por defecto) no escribe.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.erp.models import InvoiceStatus, Order
from app.integrations.factusol.documents import visible_number
from app.integrations.factusol.service import (
    _auto_link_factura,
    _compose_ref,
    _status_value,
    _store_ref_prefix,
    coerce_serie,
)

logger = logging.getLogger(__name__)

#: Estados que ya cuentan como «facturado» en el seguimiento (no son candidatos).
_INVOICED = {
    InvoiceStatus.GENERATED.value,
    InvoiceStatus.INVOICED_BY_ERP.value,
    InvoiceStatus.ALREADY_INVOICED_EXTERNALLY.value,
}


def _is_invoiced(order: Order) -> bool:
    return bool(order.factusol_invoice_number) or _status_value(order.invoice_status) in _INVOICED


def _fac_summary(row: dict[str, Any]) -> dict[str, Any]:
    serie = coerce_serie(row.get("TIPFAC"))
    codfac = row.get("CODFAC")
    return {
        "codfac": str(codfac) if codfac is not None else None,
        "serie": serie,
        "numero": visible_number(serie, codfac),
        "cliente_codigo": str(row.get("CODCLI")) if row.get("CODCLI") is not None else None,
        "total": row.get("TOTFAC"),
        "fecha": str(row.get("FECFAC")) if row.get("FECFAC") is not None else None,
    }


def reconcile_factusol_invoices(
    session: Session, client: Any, ejercicio: str, *, dry_run: bool = True,
) -> dict[str, Any]:
    """Enlaza las facturas de FACTUSOL a los pedidos no facturados de BoHub por
    REFFAC. Devuelve el resumen (a enlazar + conflictos + recuentos). Con
    `dry_run=True` no persiste."""
    # 1. Índice REFFAC → [facturas]. Una sola llamada a FACTUSOL.
    fac_rows = client.load_table("F_FAC", filtro="1=1", ejercicio=ejercicio)
    by_ref: dict[str, list[dict[str, Any]]] = {}
    for r in fac_rows:
        ref = str(r.get("REFFAC") or "").strip().upper()
        if ref:
            by_ref.setdefault(ref, []).append(r)

    # 2. Candidatos: pedidos NO facturados con número de pedido.
    candidates = session.scalars(
        select(Order).where(
            Order.factusol_invoice_number.is_(None),
            Order.order_number.isnot(None),
        )
    )

    to_link: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    scanned = 0
    no_match = 0

    for o in candidates:
        if _is_invoiced(o):
            continue  # ya facturado por otro estado; no se toca
        scanned += 1
        ref = _compose_ref(o.order_number, _store_ref_prefix(session, o))
        matches = by_ref.get(ref.upper(), [])
        if not matches:
            no_match += 1
            continue
        if len(matches) > 1:
            # Facturado DOS+ veces con la misma referencia: NO elegir; reportar.
            conflicts.append({
                "order_number": o.order_number,
                "ref": ref,
                "facturas": [_fac_summary(m) for m in matches],
            })
            continue
        info = _fac_summary(matches[0])
        to_link.append({
            "order_id": o.id,
            "order_number": o.order_number,
            "ref": ref,
            **info,
        })
        if not dry_run and info["codfac"]:
            _auto_link_factura(session, o, info["codfac"], ejercicio, ref=ref, actor=None)

    if not dry_run:
        session.commit()

    logger.info(
        "reconcile facturas%s: %s a enlazar, %s conflictos, %s sin factura "
        "(escaneados %s)",
        " (preview)" if dry_run else "", len(to_link), len(conflicts),
        no_match, scanned,
    )
    return {
        "ok": True,
        "preview": dry_run,
        "scanned": scanned,
        "linked": len(to_link),
        "to_link": to_link,
        "conflicts": conflicts,
        "no_match": no_match,
    }
