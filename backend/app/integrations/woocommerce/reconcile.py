"""ERP · WooCommerce — reconciliación de estados de los pedidos ya importados.

Los pedidos que cambiaron de estado en WooCommerce mientras BoHub no lo
procesaba (o antes de que BoHub guardara el estado) se quedan «colgados» como
activos. Esta reconciliación pone al día el estado de los pedidos que BoHub
tiene como activos/en curso (los del seguimiento, NUNCA el histórico del Excel)
y aplica la misma regla que el resto del sistema (`visibility_for_status`):
solo se quedan los que pasaron por caja (`processing`/`completed`), los
`refunded` se quedan MARCADOS como «Reembolsado», y lo demás —cancelado,
fallido, papelera, y los que volvieron a `pending`/`on-hold`— sale. Solo
escribe `orders.woo_status`; no borra ni toca nada más, y NO escribe en la hoja
de Drive.

EFICIENCIA (en vez de un `get_order` por pedido activo, que daba 504): se PIDE a
cada tienda la LISTA de pedidos en los estados que interesan desde la fecha del
pedido activo más antiguo, y se CRUZA con los activos de BoHub. Así son unas
pocas llamadas de listado por tienda, no decenas de consultas individuales.
Corre en segundo plano (worker-sync); esta función es el núcleo, sin Redis ni
HTTP del request.

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
from app.erp.woo_status import NOT_FOUND, REFUNDED, normalize
from app.integrations.woocommerce.client import WooError, WooHTTPClient
from app.models.integration_settings import IntegrationAccount

logger = logging.getLogger(__name__)

#: Estados de WooCommerce que se piden por listado para cruzarlos con los
#: activos de BoHub: los que SACAN el pedido del seguimiento (`cancelled`,
#: `failed`, `trash` = papelera, y `pending`/`on-hold`, que es volver a «no ha
#: pasado por caja») más `refunded`, que no lo saca pero lo marca «Reembolsado».
RECONCILE_WOO_STATUSES = (
    "cancelled", "failed", "refunded", "trash", "pending", "on-hold",
)

#: Tope de páginas por (tienda, estado). Con la fecha de corte rara vez se
#: alcanza; si se alcanza, se marca `capped` y Bart puede re-ejecutar.
DEFAULT_MAX_PAGES = 20
_PER_PAGE = 100
#: Tope de pedidos SIN estado que se consultan uno a uno por pasada: cada uno
#: es una llamada a la tienda y el job corre con un timeout fijo. Los que
#: quedan siguen a NULL (ocultos) y entran en la siguiente pasada; se marca
#: `capped` para que la pantalla diga «vuelve a ejecutar para el resto».
DEFAULT_MAX_UNKNOWN = 150


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
        if not _en_curso(o, _estado(o)):
            continue
        if visibility_for_status(o.woo_status)[0]:
            continue  # ya oculto por estado
        out.append(o)
    return out


def _unknown_status_woo_orders(
    session: Session, store_account_id: str | None,
) -> list[Order]:
    """Pedidos web SIN `woo_status` (NULL o vacío): los importados antes de que
    existiera el campo, o los que ningún listado por estado llegó a tocar. El
    listado por estado no los rellena nunca —un pedido en `processing` no
    aparece en ningún listado de los que se piden—, así que a estos se les
    pregunta UNO A UNO. Se incluyen aunque estén excluidos a mano o anulados:
    rellenar el estado no cambia nada de eso, y son pocos."""
    stmt = select(Order).where(
        Order.external_source == OrderSource.WOOCOMMERCE,
        Order.external_id.isnot(None),
        Order.store_id.isnot(None),
        (Order.woo_status.is_(None)) | (Order.woo_status == ""),
    ).order_by(Order.placed_at.desc(), Order.id)   # los recientes, primero
    return [
        o for o in session.scalars(stmt)
        if not store_account_id or _store_matches(session, o, store_account_id)
    ]


#: Código con el que WooCommerce (REST v3) dice que un id de pedido NO existe.
#: Un 404 sin este código no es «el pedido no está»: es una URL de tienda mal
#: configurada, un plugin caído o un proxy — y en ese caso NO se marca nada.
_WC_ORDER_INVALID_ID = "woocommerce_rest_shop_order_invalid_id"


def _is_order_not_found(exc: WooError) -> bool:
    """404 de WooCommerce por id inexistente, y no cualquier 404."""
    if getattr(exc, "status", None) != 404:
        return False
    body = str(getattr(exc, "body", "") or "") + str(exc)
    return _WC_ORDER_INVALID_ID in body


def _fill_unknown_statuses(
    session: Session, orders: list[Order], client_factory: Callable[..., Any],
    *, dry_run: bool, counts: dict[str, int], samples: dict[str, list[str]],
    errors: list[dict[str, str]],
) -> int:
    """Rellena `woo_status` de los pedidos sin estado consultándolos uno a uno.
    Si la tienda ya no lo tiene (404) queda `not_found`, que el seguimiento
    oculta con su motivo. Devuelve el nº de llamadas. En `dry_run` se consulta
    igual (es solo lectura) pero no se persiste."""
    calls = 0
    by_store: dict[str, list[Order]] = {}
    for o in orders:
        by_store.setdefault(o.store_id, []).append(o)
    for store_id, pending in by_store.items():
        store = session.get(IntegrationAccount, store_id)
        if store is None:
            errors.append({"store": store_id, "error": "store_not_found"})
            continue
        try:
            client = client_factory(store)
        except WooError as exc:
            errors.append({"store": store.account_id, "error": str(exc)[:200]})
            continue
        for o in pending:
            try:
                woo_id = int(str(o.external_id).strip())
            except (TypeError, ValueError):
                errors.append({"order_number": o.order_number,
                               "error": f"external_id no numérico: {o.external_id!r}"})
                continue
            calls += 1
            try:
                woo = client.get_order(woo_id)
            except WooError as exc:
                if _is_order_not_found(exc):
                    new_status = NOT_FOUND
                else:
                    errors.append({"order_number": o.order_number,
                                   "store": store.account_id,
                                   "error": str(exc)[:200]})
                    continue
            else:
                new_status = str((woo or {}).get("status") or "").strip().lower()
                if not new_status:
                    errors.append({"order_number": o.order_number,
                                   "store": store.account_id,
                                   "error": "la tienda no devuelve estado"})
                    continue
            key = "not_found" if new_status == NOT_FOUND else "filled"
            counts[key] += 1
            if len(samples[key]) < 20:
                samples[key].append(f"{o.order_number} → {new_status}")
            if not dry_run:
                o.woo_status = new_status
    return calls


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
    max_unknown: int = DEFAULT_MAX_UNKNOWN,
) -> dict[str, Any]:
    """Pone al día el estado de los pedidos activos y aplica la regla. Devuelve
    el resumen (recuentos + muestras). Con `dry_run=True` no persiste nada."""
    counts = {"cancelled": 0, "failed": 0, "trash": 0,
              "pending": 0, "on_hold": 0, "refunded": 0,
              "filled": 0, "not_found": 0}
    samples: dict[str, list[str]] = {k: [] for k in counts}
    removed = 0

    matched_ids: set[tuple[str, str]] = set()
    errors: list[dict[str, str]] = []
    capped = False

    # 1) Los SIN estado, uno a uno (el listado por estado no los ve), con tope
    #    por pasada: el resto sigue a NULL y cae en la siguiente.
    unknown = _unknown_status_woo_orders(session, store_account_id)
    if len(unknown) > max_unknown:
        capped = True
        unknown = unknown[:max_unknown]
    woo_calls = _fill_unknown_statuses(
        session, unknown, client_factory, dry_run=dry_run,
        counts=counts, samples=samples, errors=errors,
    )

    # 2) Los activos, cruzados con los listados por estado de cada tienda. Un
    #    sin estado es VISIBLE (así que también «activo»), pero ya se ha
    #    resuelto en 1): no se cuenta dos veces (en dry-run seguiría a NULL).
    unknown_ids = {o.id for o in unknown}
    active = [
        o for o in _open_woo_orders(session, store_account_id)
        if o.id not in unknown_ids
    ]
    # Agrupar por tienda e indexar por id de WooCommerce (external_id).
    by_store: dict[str, dict[str, Order]] = {}
    for o in active:
        by_store.setdefault(o.store_id, {})[str(o.external_id)] = o

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
        for st in RECONCILE_WOO_STATUSES:
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
            if normalize(o.woo_status) == normalize(new_status):
                continue    # ya estaba en ese estado: nada que poner al día
            hidden, motivo, reembolsado = visibility_for_status(new_status)
            # Un reembolso NO sale del seguimiento: se queda marcado
            # «Reembolsado». Se cuenta aparte para que la previsualización no
            # lo mezcle con lo que sí desaparece.
            key = motivo if hidden else (REFUNDED if reembolsado else None)
            if key in counts:
                counts[key] += 1
                if len(samples[key]) < 20:
                    samples[key].append(o.order_number)
                if hidden:
                    removed += 1
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
        # Volvieron a «no ha pasado por caja» (sin pagar / en espera): salen,
        # y vuelven solos si la tienda los pasa otra vez a `processing`.
        "to_unpaid": counts["pending"] + counts["on_hold"],
        # Reembolsos: NO salen del seguimiento —es el estado propio
        # «Reembolsado»—, solo se marcan como tales.
        "to_refunded": counts["refunded"],
        # Sin estado (NULL) que se han podido rellenar preguntando uno a uno,
        # y los que la tienda ya no tiene (quedan `not_found`, ocultos).
        "to_filled": counts["filled"],
        "to_not_found": counts["not_found"],
        "unknown_total": len(unknown),
        "removed_total": removed,
        "errors": errors,
        "samples": {k: v for k, v in samples.items() if v},
    }
