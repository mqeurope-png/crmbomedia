"""ERP · WooCommerce — borrado DEFINITIVO de los pedidos web que nunca entraron
en el flujo (los carritos que se colaron con la regla antigua del sync).

Desde el arreglo integral el sync solo crea pedidos en `processing` /
`completed` / `refunded`. Los `pending`, `on-hold`, `failed`, `draft` y
`checkout-draft` que ya estaban en BoHub no son pedidos: son carritos sin
pagar que ensucian las listas. Este módulo los BORRA de verdad —no es la
exclusión reversible de `cleanup.py`—, con sus filas hijas (líneas, historial,
excepciones, envíos, fila de Drive) para no dejar huérfanos.

Red de seguridad, a rajatabla: NUNCA se borra un pedido con cualquier huella
fiscal o de trabajo real — factura, albarán (FACTUSOL o paquete), cobro, nº de
serie, tracking, WhiteRIP, excepción/tarea SAT, ya trabajado en preparación,
pagado. Esos se listan aparte con su motivo y se quedan. Tampoco se toca un
`refunded` / `completed` / `processing` / `cancelled`, ni un pedido sin estado,
ni un manual, ni una muestra.

`dry_run=True` (por defecto) solo lista y cuenta. Idempotente: tras `--apply`,
una segunda pasada da 0.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.erp.downstream import downstream_reasons, hard_reasons
from app.erp.models import Order, OrderSource
from app.erp.sample_orders import is_sample_order
from app.erp.woo_status import normalize
from app.integrations.woocommerce.cleanup import _client_name
from app.models.integration_settings import IntegrationAccount

logger = logging.getLogger(__name__)

__all__ = ["PURGE_STATUSES", "delete_orders_cascade", "purge_unprocessed_web_orders"]

#: Estados (normalizados) que se borran: carritos que nunca pasaron por caja.
#: `cancelled` NO está: un cancelado en la tienda es un pedido anulado con su
#: historia, no basura.
PURGE_STATUSES: frozenset[str] = frozenset({
    "pending", "on_hold", "failed", "draft", "checkout_draft",
})

#: Tamaño de lote para los `IN (...)` del borrado.
_CHUNK = 500


def _val(v: Any) -> str:
    return str(getattr(v, "value", v) or "")


def fiscal_footprint(session: Session, order: Order) -> list[str]:
    """Todo lo que impide borrar el pedido (vacío = se puede borrar). Reúne
    los hechos «aguas abajo» de `downstream.py` (factura, cobro, albarán o
    etiqueta, excepción/tarea SAT, en preparación; NO el informativo «escrito
    en Drive») con los datos propios del pedido que también son huella: albarán
    y cobro de FACTUSOL, nº de serie, tracking y WhiteRIP."""
    motivos = hard_reasons(downstream_reasons(session, order))
    if order.factusol_albaran_number:
        motivos.append("albarán FACTUSOL")
    if order.factusol_cobro_status:
        motivos.append("cobro FACTUSOL")
    if (order.serial_number or "").strip():
        motivos.append("nº de serie")
    if (order.tracking_number or "").strip():
        motivos.append("tracking")
    if (order.whiterip_license or "").strip():
        motivos.append("WhiteRIP")
    return list(dict.fromkeys(motivos))


def _row(session: Session, order: Order, motivos: list[str]) -> dict[str, Any]:
    return {
        "order_id": order.id,
        "order_number": order.order_number,
        "cliente": _client_name(session, order),
        "woo_status": order.woo_status,
        "importe": float(order.total_amount or 0),
        "motivos": motivos,
    }


def _chunks(values: list[Any]) -> list[list[Any]]:
    return [values[i:i + _CHUNK] for i in range(0, len(values), _CHUNK)]


def delete_orders_cascade(session: Session, order_ids: list[str]) -> dict[str, int]:
    """Borra los pedidos y TODO lo que cuelga de ellos, recorriendo el grafo de
    claves foráneas del modelo (hijos, y los hijos de los hijos) de abajo
    arriba. Así no se depende del `ON DELETE CASCADE` de la base ni se deja un
    huérfano si mañana aparece una tabla nueva que apunte a `orders`. No hace
    commit. Devuelve filas borradas por tabla."""
    from app.db.base import Base  # noqa: PLC0415

    metadata = Base.metadata
    counts: dict[str, int] = {}

    def _purge_children(table: Any, ids: list[Any], path: tuple[str, ...]) -> None:
        if not ids:
            return
        for child in metadata.tables.values():
            if child.name in path:
                continue                       # sin ciclos
            for fk in child.foreign_keys:
                if fk.column.table is not table:
                    continue
                pk = list(child.primary_key.columns)
                for lote in _chunks(ids):
                    if len(pk) == 1:
                        child_ids = [
                            r[0] for r in session.execute(
                                select(pk[0]).where(fk.parent.in_(lote)),
                            )
                        ]
                        _purge_children(child, child_ids, (*path, child.name))
                    res = session.execute(delete(child).where(fk.parent.in_(lote)))
                    counts[child.name] = counts.get(child.name, 0) + int(res.rowcount or 0)

    orders_table = metadata.tables["orders"]
    _purge_children(orders_table, list(order_ids), ("orders",))
    for lote in _chunks(list(order_ids)):
        res = session.execute(delete(orders_table).where(orders_table.c.id.in_(lote)))
        counts["orders"] = counts.get("orders", 0) + int(res.rowcount or 0)
    return counts


def purge_unprocessed_web_orders(
    session: Session, *, dry_run: bool = True, store_account_id: str | None = None,
) -> dict[str, Any]:
    """Localiza (y con `dry_run=False` BORRA) los pedidos web que nunca
    entraron en el flujo y no tienen huella fiscal ni de trabajo."""
    stmt = select(Order).where(Order.external_source == OrderSource.WOOCOMMERCE)
    if store_account_id:
        store = session.scalar(select(IntegrationAccount).where(
            IntegrationAccount.account_id == store_account_id,
        ))
        if store is None:
            return {"ok": False, "error": f"tienda {store_account_id!r} no encontrada"}
        stmt = stmt.where(Order.store_id == store.id)

    candidates: list[dict[str, Any]] = []
    protected: list[dict[str, Any]] = []
    por_estado: dict[str, int] = {}
    protegidos_por_motivo: dict[str, int] = {}
    for o in session.scalars(stmt.order_by(Order.placed_at, Order.id)):
        st = normalize(o.woo_status)
        if st not in PURGE_STATUSES or is_sample_order(o):
            continue
        motivos = fiscal_footprint(session, o)
        if motivos:
            protected.append(_row(session, o, motivos))
            for m in motivos:
                clave = m.split(" (")[0]
                protegidos_por_motivo[clave] = protegidos_por_motivo.get(clave, 0) + 1
            continue
        candidates.append(_row(session, o, []))
        por_estado[st] = por_estado.get(st, 0) + 1

    deleted: dict[str, int] = {}
    if not dry_run and candidates:
        ids = [c["order_id"] for c in candidates]
        deleted = delete_orders_cascade(session, ids)
        _audit(session, candidates, deleted)
        session.commit()

    logger.info(
        "woo purge%s: %s candidatos (%s), %s protegidos%s",
        " (preview)" if dry_run else "", len(candidates), por_estado, len(protected),
        f", borrados {deleted.get('orders', 0)}" if not dry_run else "",
    )
    return {
        "ok": True,
        "preview": dry_run,
        "candidates": candidates,
        "por_estado": por_estado,
        "total_importe": round(sum(c["importe"] for c in candidates), 2),
        "protected": protected,
        "protegidos_por_motivo": protegidos_por_motivo,
        "deleted": deleted.get("orders", 0),
        "deleted_rows": deleted,
    }


def _audit(session: Session, candidates: list[dict[str, Any]], deleted: dict[str, int]) -> None:
    """Deja constancia de qué se borró (los pedidos ya no existen para
    contarlo): un evento con los números y el recuento por tabla."""
    from app.core.audit import record_event  # noqa: PLC0415

    record_event(
        session, action="erp.web_orders_purged", target_type="woocommerce",
        target_id="purge", actor=None,
        message=f"Borrados {deleted.get('orders', 0)} pedidos web que nunca entraron en el flujo",
        metadata={
            "order_numbers": [c["order_number"] for c in candidates],
            "deleted_rows": deleted,
        },
    )
