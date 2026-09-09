"""ERP · WooCommerce — reconciliación de estados de los pedidos ya importados.

Los pedidos que cambiaron de estado en WooCommerce mientras BoHub no lo
procesaba (o antes de que BoHub guardara el estado) se quedan «colgados» como
activos. Esta reconciliación re-consulta en la tienda el estado ACTUAL de los
pedidos que BoHub tiene como activos/en curso (los del seguimiento, NUNCA el
histórico del Excel) y aplica la misma regla que el resto del sistema
(`visibility_for_status`): cancelado/fallido/reembolso-no-cumplido → fuera;
reembolso-cumplido → marcado. Solo escribe `orders.woo_status`; no borra ni
toca nada más, y NO escribe en la hoja de Drive.

`dry_run=True` (por defecto) NO persiste: devuelve el recuento de lo que
CAMBIARÍA (la magnitud del problema), para que Bart lo vea antes de aplicar.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.erp.models import Order, OrderSource
from app.erp.seguimiento import _en_curso, _estado, visibility_for_status
from app.integrations.woocommerce.client import WooError, WooHTTPClient
from app.models.integration_settings import IntegrationAccount

logger = logging.getLogger(__name__)

#: Tope de seguridad: la reconciliación hace una llamada a la tienda por pedido
#: abierto. Con más pedidos abiertos que esto, se avisa y se procesa hasta el
#: tope (Bart puede re-ejecutar).
DEFAULT_SCAN_LIMIT = 1500


def _open_woo_orders(session: Session, store_account_id: str | None) -> list[Order]:
    """Pedidos Woo que HOY se ven en el seguimiento (activos): en curso, no
    excluidos a mano y no ya ocultos por estado. Nunca el histórico (que no
    vive en BoHub). Se excluyen los ya marcados como cancelado/etc.: no hay
    que volver a consultarlos."""
    stmt = select(Order).where(
        Order.external_source == OrderSource.WOOCOMMERCE,
        Order.external_id.isnot(None),
        Order.store_id.isnot(None),
        Order.externally_processed_at.is_(None),
        Order.seguimiento_excluded_at.is_(None),
    )
    out: list[Order] = []
    for o in session.scalars(stmt):
        if store_account_id and (o.store_id or "") != store_account_id \
                and not _store_matches(session, o, store_account_id):
            continue
        est = _estado(o)
        if not _en_curso(o, est):
            continue
        if visibility_for_status(o, est, o.woo_status)[0]:
            continue  # ya oculto por estado: no hace falta re-consultarlo
        out.append(o)
    return out


def _store_matches(session: Session, order: Order, account_id: str) -> bool:
    acc = session.get(IntegrationAccount, order.store_id) if order.store_id else None
    return acc is not None and acc.account_id == account_id


def reconcile_open_order_statuses(
    session: Session,
    *,
    dry_run: bool = True,
    store_account_id: str | None = None,
    client_factory: Callable[[IntegrationAccount], Any] = WooHTTPClient,
    limit: int = DEFAULT_SCAN_LIMIT,
) -> dict[str, Any]:
    """Re-consulta el estado actual en la tienda de los pedidos abiertos y
    aplica la regla. Devuelve el resumen (recuentos + muestras). Con
    `dry_run=True` no persiste nada."""
    candidates = _open_woo_orders(session, store_account_id)
    capped = len(candidates) > limit
    candidates = candidates[:limit]

    # Un cliente por tienda (los secretos se descifran una vez por tienda).
    clients: dict[str, Any] = {}
    stores: dict[str, IntegrationAccount] = {}

    def _client(store_id: str) -> Any | None:
        if store_id in clients:
            return clients[store_id]
        acc = stores.get(store_id) or session.get(IntegrationAccount, store_id)
        stores[store_id] = acc
        try:
            clients[store_id] = client_factory(acc) if acc is not None else None
        except WooError as exc:
            logger.warning("reconcile: no se pudo crear cliente de %s: %s", store_id, exc)
            clients[store_id] = None
        return clients[store_id]

    counts = {"cancelled": 0, "failed": 0, "trash": 0,
              "refunded_sin_cumplir": 0, "refunded": 0}
    samples: dict[str, list[str]] = {k: [] for k in counts}
    removed = 0
    kept_marked = 0
    unchanged = 0
    errors: list[dict[str, str]] = []

    for o in candidates:
        client = _client(o.store_id or "")
        if client is None:
            errors.append({"order_number": o.order_number, "error": "store_client_unavailable"})
            continue
        try:
            data = client.get_order(int(o.external_id))
            new_status = str((data or {}).get("status") or "").strip().lower() or None
        except (WooError, ValueError, TypeError) as exc:
            status = getattr(exc, "status", None)
            if isinstance(exc, WooError) and status is not None and 400 <= status < 500:
                new_status = "trash"   # ya no existe en la tienda → papelera
            else:
                errors.append({"order_number": o.order_number, "error": str(exc)[:200]})
                continue

        est = _estado(o)
        hidden, motivo, reembolsado = visibility_for_status(o, est, new_status)
        changed_value = new_status != o.woo_status
        if hidden and motivo in counts:
            counts[motivo] += 1
            if len(samples[motivo]) < 20:
                samples[motivo].append(o.order_number)
            removed += 1
            if not dry_run:
                o.woo_status = new_status
        elif reembolsado:
            counts["refunded"] += 1
            if len(samples["refunded"]) < 20:
                samples["refunded"].append(o.order_number)
            kept_marked += 1
            if not dry_run:
                o.woo_status = new_status
        else:
            unchanged += 1
            if not dry_run and changed_value:
                o.woo_status = new_status  # p.ej. processing → completed

    if not dry_run:
        session.commit()

    return {
        "ok": True,
        "preview": dry_run,
        "scanned": len(candidates),
        "capped": capped,
        "limit": limit,
        # Sacados del seguimiento por estado.
        "to_cancel": counts["cancelled"],
        "to_fail": counts["failed"],
        "to_trash": counts["trash"],
        "to_refund_out": counts["refunded_sin_cumplir"],
        "removed_total": removed,
        # Reembolsos ya cumplidos: se quedan, marcados.
        "to_refund_kept": kept_marked,
        "unchanged": unchanged,
        "errors": errors,
        "samples": {k: v for k, v in samples.items() if v},
    }
