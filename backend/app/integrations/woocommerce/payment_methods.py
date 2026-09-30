"""ERP · WooCommerce — rellenar el MÉTODO DE PAGO de los pedidos web que no lo
tienen (`payment_method` / `payment_method_title`, #495).

Los pedidos importados antes de guardarlo se quedaron a NULL y sin él no hay
base para las reglas de contrapartida del cobro. Se rellenan:

- en cada «Puesta al día con Woo» (Seguimiento), cambien o no de estado y con
  independencia del filtro de la pantalla — el paso va el PRIMERO y se
  confirma en BD aunque el resto de la puesta al día falle;
- una vez al desplegar (job en la cola interactiva, `run_backfill_job`), con
  log de cuántos se rellenaron por tienda.

Se piden a cada tienda POR ID (`include=…`, 100 por llamada, los más recientes
primero): sin fecha de corte ni listados enteros de la tienda. Solo se rellena
lo que está VACÍO: un pedido que ya tiene método no se toca.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.erp.models import Order, OrderSource
from app.erp.woo_status import NOT_FOUND
from app.integrations.woocommerce.client import WooError, WooHTTPClient
from app.models.integration_settings import IntegrationAccount

logger = logging.getLogger(__name__)

#: Pedidos por llamada (`include`): el máximo de `per_page` de WooCommerce.
BATCH_SIZE = 100
#: Tope de pedidos por tienda y pasada (20 llamadas): los más recientes
#: primero; el resto entra en la siguiente pasada.
DEFAULT_MAX_PER_STORE = 2000
#: Marca en Ajustes ERP de que el relleno inicial (al desplegar) ya se hizo.
DONE_KEY = "payment_method_backfill"
ARM_KEY = "woo:payment_method_backfill:armed"
ARM_TTL_SECONDS = 6 * 3600
JOB_TIMEOUT_SECONDS = 900


def _empty(col: Any) -> Any:
    return col.is_(None) | (col == "")


def missing_payment_method_orders(
    session: Session, store_account_id: str | None = None,
) -> list[Order]:
    """Pedidos web sin método de pago guardado, los más recientes primero.
    Todos los pedidos web (en curso o no); fuera solo los que la tienda ya no
    tiene (`not_found`)."""
    stmt = select(Order).where(
        Order.external_source == OrderSource.WOOCOMMERCE,
        Order.external_id.isnot(None),
        Order.store_id.isnot(None),
        _empty(Order.payment_method),
        Order.woo_status.is_(None) | (Order.woo_status != NOT_FOUND),
    ).order_by(Order.placed_at.desc(), Order.id)
    orders = list(session.scalars(stmt))
    if not store_account_id:
        return orders
    accounts = {
        a.id for a in session.scalars(
            select(IntegrationAccount).where(IntegrationAccount.account_id == store_account_id)
        )
    }
    return [o for o in orders if o.store_id in accounts]


def fill_empty_payment_method(order: Order, woo: dict[str, Any]) -> bool:
    """Rellena SOLO lo vacío (gateway y título). Nunca pisa lo que hay."""
    changed = False
    method = str(woo.get("payment_method") or "").strip()[:64]
    title = str(woo.get("payment_method_title") or "").strip()[:120]
    if method and not (order.payment_method or "").strip():
        order.payment_method = method
        changed = True
    if title and not (order.payment_method_title or "").strip():
        order.payment_method_title = title
        changed = True
    return changed


def backfill_payment_methods(
    session: Session,
    *,
    dry_run: bool = False,
    store_account_id: str | None = None,
    client_factory: Callable[[IntegrationAccount], Any] = WooHTTPClient,
    max_per_store: int = DEFAULT_MAX_PER_STORE,
    errors: list[dict[str, str]] | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    """Rellena el método de pago de los pedidos web que no lo tienen,
    pidiéndolos a su tienda por id. Devuelve `{calls, filled, by_store,
    samples, pending, capped, errors}`. En `dry_run` consulta (solo lectura)
    y cuenta, sin persistir. Con `commit` confirma al terminar cada tienda."""
    errors = errors if errors is not None else []
    missing = missing_payment_method_orders(session, store_account_id)
    by_store: dict[str, list[Order]] = {}
    for o in missing:
        by_store.setdefault(o.store_id, []).append(o)
    calls = filled = 0
    filled_by_store: dict[str, int] = {}
    samples: list[str] = []
    capped = False
    for store_id, orders in by_store.items():
        store = session.get(IntegrationAccount, store_id)
        if store is None:
            continue
        key = str(store.account_id or store_id)
        filled_by_store.setdefault(key, 0)
        if len(orders) > max_per_store:
            capped = True
            orders = orders[:max_per_store]
        try:
            client = client_factory(store)
        except WooError as exc:
            errors.append({"store": key, "error": str(exc)[:200]})
            continue
        by_extid: dict[int, Order] = {}
        for o in orders:
            try:
                by_extid[int(str(o.external_id).strip())] = o
            except (TypeError, ValueError):
                continue
        ids = list(by_extid)
        for start in range(0, len(ids), BATCH_SIZE):
            chunk = ids[start:start + BATCH_SIZE]
            calls += 1
            try:
                batch = client.list_orders_by_ids(chunk) or []
            except WooError as exc:
                errors.append({"store": key, "error": str(exc)[:200]})
                break
            for wo in batch:
                try:
                    o = by_extid.get(int(wo.get("id")))
                except (TypeError, ValueError):
                    o = None
                if o is None or not str(wo.get("payment_method") or "").strip():
                    continue
                if dry_run:
                    changed = not (o.payment_method or "").strip()
                else:
                    changed = fill_empty_payment_method(o, wo)
                if not changed:
                    continue
                filled += 1
                filled_by_store[key] += 1
                if len(samples) < 20:
                    title = str(wo.get("payment_method_title") or wo.get("payment_method"))
                    samples.append(f"{o.order_number} → {title}")
        if commit and not dry_run:
            session.commit()
    pending = len(missing) - filled
    return {
        "calls": calls, "filled": filled, "by_store": filled_by_store,
        "samples": samples, "pending": max(pending, 0), "capped": capped,
        "errors": errors,
    }


def _by_store_text(by_store: dict[str, int]) -> str:
    return ", ".join(f"{k} {v}" for k, v in sorted(by_store.items())) or "ninguna tienda"


# --- relleno inicial al desplegar ---------------------------------------------------


def run_backfill_job(
    *, client_factory: Callable[[IntegrationAccount], Any] | None = None,
) -> dict[str, Any]:
    """Entrada RQ del relleno inicial: rellena TODOS los pedidos web sin método,
    deja en el log cuántos por tienda y marca en Ajustes ERP que ya se hizo
    (si no hubo errores; si los hubo, se reintenta en el siguiente arranque).
    Si ya estaba hecho, no hace nada."""
    from app.db.session import get_engine  # noqa: PLC0415
    from app.erp.models import ErpSettings  # noqa: PLC0415
    from app.erp.models.settings import ERP_SETTINGS_SINGLETON_ID  # noqa: PLC0415
    from app.integrations.factusol.service import series_config  # noqa: PLC0415

    with Session(get_engine()) as session:
        done = series_config(session).get(DONE_KEY)
        if isinstance(done, dict) and done.get("done_at"):
            return {"skipped": True, "done_at": done["done_at"]}
        result = backfill_payment_methods(
            session, dry_run=False, client_factory=client_factory or WooHTTPClient,
        )
        logger.info(
            "woo.payment_method backfill: %d pedidos rellenados (%s) · %d siguen sin "
            "método · %d llamadas%s%s",
            result["filled"], _by_store_text(result["by_store"]), result["pending"],
            result["calls"], " · con tope (resto en la próxima pasada)" if result["capped"] else "",
            f" · {len(result['errors'])} errores" if result["errors"] else "",
        )
        if not result["errors"]:
            import json  # noqa: PLC0415

            cfg = session.get(ErpSettings, ERP_SETTINGS_SINGLETON_ID)
            if cfg is None:
                cfg = ErpSettings(id=ERP_SETTINGS_SINGLETON_ID)
                session.add(cfg)
            try:
                blob = json.loads(cfg.factusol_series_json or "{}")
            except (TypeError, ValueError):
                blob = {}
            blob = blob if isinstance(blob, dict) else {}
            blob[DONE_KEY] = {
                "done_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "filled": result["filled"], "by_store": result["by_store"],
                "pending": result["pending"],
            }
            cfg.factusol_series_json = json.dumps(blob, ensure_ascii=False)
            session.commit()
        return result


def arm() -> None:
    """Al arrancar el API: encola UNA vez el relleno inicial en la cola
    interactiva (la atiende `worker-factusol`). SETNX en Redis para que varios
    procesos del API no lo encolen a la vez; el job comprueba la marca de
    «hecho» en Ajustes ERP y no repite."""
    try:
        from rq import Queue  # noqa: PLC0415

        from app.integrations.woocommerce.jobs import ERP_INTERACTIVE_QUEUE  # noqa: PLC0415
        from app.workers.queues import redis_connection  # noqa: PLC0415

        conn = redis_connection()
        if not conn.set(ARM_KEY, "1", nx=True, ex=ARM_TTL_SECONDS):
            return
        Queue(ERP_INTERACTIVE_QUEUE, connection=conn).enqueue(
            run_backfill_job, job_timeout=JOB_TIMEOUT_SECONDS, result_ttl=24 * 3600,
        )
    except Exception as exc:  # noqa: BLE001 — sin Redis: la puesta al día lo hará
        logger.warning("woo.payment_method backfill arm failed: %s", exc)


__all__ = [
    "BATCH_SIZE",
    "arm",
    "backfill_payment_methods",
    "fill_empty_payment_method",
    "missing_payment_method_orders",
    "run_backfill_job",
]
