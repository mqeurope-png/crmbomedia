"""ERP · WooCommerce — reconciliación de estados de los pedidos ya importados.

Los pedidos que cambiaron de estado en WooCommerce mientras BoHub no lo
procesaba (o antes de que BoHub guardara el estado) se quedan «colgados» como
activos. Esta reconciliación pone al día el estado de los pedidos que BoHub
tiene como activos/en curso (los del seguimiento, NUNCA el histórico del Excel)
y aplica la misma regla que el resto del sistema (`visibility_for_status`):
cancelado/fallido/trash → fuera; reembolso-no-cumplido → fuera; reembolso
cumplido → marcado. Solo escribe `orders.woo_status`; no borra ni toca nada
más, y NO escribe en la hoja de Drive.

EFICIENCIA (en vez de un `get_order` por pedido activo, que daba 504): se PIDE a
cada tienda la LISTA de pedidos en los estados que ocultan (`cancelled`,
`refunded`, `failed`, `trash`) desde la fecha del pedido activo más antiguo, y
se CRUZA con los activos de BoHub. Así son unas pocas llamadas de listado por
tienda, no decenas de consultas individuales. Corre en segundo plano
(worker-sync); esta función es el núcleo, sin Redis ni HTTP del request.

`dry_run=True` (por defecto) NO persiste: devuelve el recuento de lo que
CAMBIARÍA (la magnitud del problema), para que Bart lo vea antes de aplicar.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.erp.models import Order, OrderSource
from app.erp.seguimiento import _en_curso, _estado, visibility_for_status
from app.integrations.woocommerce.client import WooError, WooHTTPClient
from app.models.integration_settings import IntegrationAccount

logger = logging.getLogger(__name__)

#: Estados de WooCommerce que OCULTAN el pedido (se piden por listado). `trash`
#: cubre los pedidos enviados a la papelera en la tienda.
HIDDEN_WOO_STATUSES = ("cancelled", "failed", "refunded", "trash")

#: Tope de páginas por (tienda, estado). Con la fecha de corte rara vez se
#: alcanza; si se alcanza, se marca `capped` y Bart puede re-ejecutar.
DEFAULT_MAX_PAGES = 20
_PER_PAGE = 100


def _open_woo_orders(session: Session, store_account_id: str | None) -> list[Order]:
    """Pedidos Woo que HOY se ven en el seguimiento (activos): en curso, no
    excluidos a mano y no ya ocultos por estado. Nunca el histórico (que no
    vive en BoHub). Los ya marcados como cancelado/etc. se excluyen: no hay que
    volver a mirarlos."""
    stmt = select(Order).where(
        Order.external_source == OrderSource.WOOCOMMERCE,
        Order.external_id.isnot(None),
        Order.store_id.isnot(None),
        Order.externally_processed_at.is_(None),
        Order.seguimiento_excluded_at.is_(None),
    )
    out: list[Order] = []
    for o in session.scalars(stmt):
        if store_account_id and not _store_matches(session, o, store_account_id):
            continue
        est = _estado(o)
        if not _en_curso(o, est):
            continue
        if visibility_for_status(o, est, o.woo_status)[0]:
            continue  # ya oculto por estado
        out.append(o)
    return out


def _store_matches(session: Session, order: Order, account_id: str) -> bool:
    acc = session.get(IntegrationAccount, order.store_id) if order.store_id else None
    return acc is not None and acc.account_id == account_id


def _cutoff_iso(orders: list[Order]) -> str | None:
    """Fecha (YYYY-MM-DD) del pedido activo MÁS ANTIGUO: acota el listado de
    cancelados/reembolsados a la ventana de los pedidos que nos importan (un
    pedido activo se creó después de esta fecha, así que su posible cancelación
    entra en el listado). None si no hay fechas."""
    dates = [o.placed_at for o in orders if o.placed_at is not None]
    if not dates:
        return None
    earliest = min(dates)
    if earliest.tzinfo is None:
        earliest = earliest.replace(tzinfo=UTC)
    return earliest.date().isoformat()


def reconcile_open_order_statuses(
    session: Session,
    *,
    dry_run: bool = True,
    store_account_id: str | None = None,
    client_factory: Callable[[IntegrationAccount], Any] = WooHTTPClient,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> dict[str, Any]:
    """Pone al día el estado de los pedidos activos y aplica la regla. Devuelve
    el resumen (recuentos + muestras). Con `dry_run=True` no persiste nada."""
    active = _open_woo_orders(session, store_account_id)
    # Agrupar por tienda e indexar por id de WooCommerce (external_id).
    by_store: dict[str, dict[str, Order]] = {}
    for o in active:
        by_store.setdefault(o.store_id, {})[str(o.external_id)] = o

    counts = {"cancelled": 0, "failed": 0, "trash": 0,
              "refunded_sin_cumplir": 0, "refunded": 0}
    samples: dict[str, list[str]] = {k: [] for k in counts}
    removed = 0
    kept_marked = 0
    matched_ids: set[tuple[str, str]] = set()
    errors: list[dict[str, str]] = []
    capped = False
    woo_calls = 0

    for store_id, orders_by_extid in by_store.items():
        store = session.get(IntegrationAccount, store_id)
        if store is None:
            errors.append({"store": store_id, "error": "store_not_found"})
            continue
        try:
            client = client_factory(store)
        except WooError as exc:
            errors.append({"store": store.account_id, "error": str(exc)[:200]})
            continue

        cutoff = _cutoff_iso(list(orders_by_extid.values()))
        # id de WooCommerce → estado actual, SOLO de los que están en un estado
        # que oculta Y además son pedidos activos de BoHub (la intersección).
        fetched: dict[str, str] = {}
        for st in HIDDEN_WOO_STATUSES:
            try:
                for page in range(1, max_pages + 1):
                    woo_calls += 1
                    batch = client.list_orders(
                        status=st, since=cutoff, per_page=_PER_PAGE, page=page,
                    )
                    if not batch:
                        break
                    for wo in batch:
                        wid = str(wo.get("id") or "")
                        if wid in orders_by_extid:
                            fetched[wid] = str(wo.get("status") or st).strip().lower()
                    if len(batch) >= _PER_PAGE and page == max_pages:
                        capped = True
            except WooError as exc:
                errors.append({"store": store.account_id, "status": st,
                               "error": str(exc)[:200]})
                continue

        for extid, new_status in fetched.items():
            o = orders_by_extid[extid]
            est = _estado(o)
            hidden, motivo, reembolsado = visibility_for_status(o, est, new_status)
            if hidden and motivo in counts:
                counts[motivo] += 1
                if len(samples[motivo]) < 20:
                    samples[motivo].append(o.order_number)
                removed += 1
                matched_ids.add((store_id, extid))
                if not dry_run:
                    o.woo_status = new_status
            elif reembolsado:
                counts["refunded"] += 1
                if len(samples["refunded"]) < 20:
                    samples["refunded"].append(o.order_number)
                kept_marked += 1
                matched_ids.add((store_id, extid))
                if not dry_run:
                    o.woo_status = new_status

    if not dry_run:
        session.commit()

    scanned = len(active)
    return {
        "ok": True,
        "preview": dry_run,
        "scanned": scanned,
        # Activos que NO han cambiado de estado (no aparecen en ningún listado).
        "unchanged": scanned - len(matched_ids),
        "capped": capped,
        "limit": max_pages,
        # nº de llamadas a WooCommerce (listados) — para confirmar que bajó.
        "woo_calls": woo_calls,
        # Sacados del seguimiento por estado.
        "to_cancel": counts["cancelled"],
        "to_fail": counts["failed"],
        "to_trash": counts["trash"],
        "to_refund_out": counts["refunded_sin_cumplir"],
        "removed_total": removed,
        # Reembolsos ya cumplidos: se quedan, marcados.
        "to_refund_kept": kept_marked,
        "errors": errors,
        "samples": {k: v for k, v in samples.items() if v},
    }
