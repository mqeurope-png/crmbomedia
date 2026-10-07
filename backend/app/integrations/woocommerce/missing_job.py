"""WooCommerce — repaso periódico de pedidos PAGADOS que no están en BoHub.

Red por si la tienda no dispara el webhook al pasar un pedido a pagado (caso
99976 de boprint, 07/10/2026). Cada `woo_missing_check_interval_minutes` (60
por defecto, mínimo 15) pide a cada tienda, en UNA consulta, los pedidos
pagados modificados desde su última pasada (`missing.desde_para_repaso`) y crea
en BoHub los que falten por el camino del webhook
(`missing.import_missing_paid_orders`). Idempotente.

- Job RQ self-rescheduling en `woocommerce:backfill` (la escucha
  `worker-web`, `--with-scheduler`): mismo patrón que `genei.tracking_job`
  (latido con SETNX + `enqueue_in`, re-armado en `finally`). Al ir en el mismo
  worker que los webhooks, nunca importa a la vez que uno de ellos.
- Interruptor `woo_missing_check_enabled` en Configuración ERP (encendido por
  defecto: hace lo mismo que haría el webhook). Apagado, el tic sigue armado y
  no hace nada.
- Si ya hubo un repaso hace menos de medio intervalo, no repite (dos cadenas
  de tics tras un reinicio no duplican el trabajo).

Una pasada a mano: `python -m app.integrations.woocommerce.missing_job`.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integrations.woocommerce.jobs import WOO_QUEUE_BACKFILL
from app.integrations.woocommerce.missing import (
    DEFAULT_INTERVAL_MINUTES,
    OPERATION,
    import_missing_paid_orders,
    missing_config,
)

logger = logging.getLogger(__name__)

MISSING_QUEUE = WOO_QUEUE_BACKFILL
HEARTBEAT_KEY = "woocommerce:missing:heartbeat"
JOB_TIMEOUT_SECONDS = 900
TRIGGER = "cron"


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _ultimo_repaso(session: Session) -> datetime | None:
    from app.models.crm import ExternalSystem, SyncLog  # noqa: PLC0415

    ultimo = session.scalars(select(SyncLog.started_at).where(
        SyncLog.system == ExternalSystem.WOOCOMMERCE,
        SyncLog.operation == OPERATION,
        SyncLog.triggered_by == TRIGGER,
    ).order_by(SyncLog.started_at.desc()).limit(1)).first()
    return _aware(ultimo) if ultimo else None


def run_periodic_check(
    session: Session,
    *,
    client_factory: Callable[[Any], Any] | None = None,
    importer: Callable[..., dict] | None = None,
    now: datetime | None = None,
    force: bool = False,
) -> dict[str, Any] | None:
    """Un repaso. Devuelve el resumen, o None si no toca (interruptor apagado,
    o ya hubo un repaso hace menos de medio intervalo)."""
    now = _aware(now or datetime.now(UTC))
    cfg = missing_config(session)
    if not force:
        if not cfg["enabled"]:
            return None
        ultimo = _ultimo_repaso(session)
        if ultimo is not None and now - ultimo < timedelta(minutes=cfg["interval_minutes"] / 2):
            logger.info("woo.missing: ya hubo un repaso a las %s; se salta", ultimo.isoformat())
            return None
    kwargs: dict[str, Any] = {}
    if client_factory is not None:
        kwargs["client_factory"] = client_factory
    if importer is not None:
        kwargs["importer"] = importer
    resumen = import_missing_paid_orders(
        session, dry_run=False, days=cfg["days"], incremental=True, trigger=TRIGGER,
        now=now, **kwargs,
    )
    logger.info(
        "woo.missing: repaso — %d pagados que faltaban, %d importados%s%s",
        resumen["faltan"], resumen["importados"],
        f", {len(resumen['errores'])} errores" if resumen["errores"] else "",
        " (con tope: el resto en el siguiente)" if resumen["con_tope"] else "",
    )
    return resumen


# --- armado (job RQ self-rescheduling) ----------------------------------------------


def interval() -> timedelta:
    """Cada cuánto corre el repaso (Configuración ERP)."""
    minutes = DEFAULT_INTERVAL_MINUTES
    try:
        from app.db.session import get_engine  # noqa: PLC0415

        with Session(get_engine()) as session:
            minutes = missing_config(session)["interval_minutes"]
    except Exception:  # noqa: BLE001
        minutes = DEFAULT_INTERVAL_MINUTES
    return timedelta(minutes=minutes)


def schedule_check() -> None:
    """Arma el siguiente tic. Idempotente vía SETNX (api y worker a la vez)."""
    every = interval()
    try:
        from rq import Queue  # noqa: PLC0415

        from app.workers.queues import redis_connection  # noqa: PLC0415

        conn = redis_connection()
        ttl = max(int(every.total_seconds()) - 30, 10)
        if not conn.set(HEARTBEAT_KEY, "1", nx=True, ex=ttl):
            return
        try:
            Queue(MISSING_QUEUE, connection=conn,
                  default_timeout=JOB_TIMEOUT_SECONDS).enqueue_in(every, _check_runner)
        except Exception as exc:  # noqa: BLE001
            logger.warning("woo.missing arm failed: %s", exc)
            conn.delete(HEARTBEAT_KEY)
    except Exception as exc:  # noqa: BLE001
        logger.warning("woo.missing redis unreachable: %s", exc)


def _check_runner() -> None:
    """Entrada RQ. El re-armado va en `finally`: un fallo no corta la cadena."""
    try:
        from app.db.session import get_engine  # noqa: PLC0415

        with Session(get_engine()) as session:
            run_periodic_check(session)
    except Exception:  # noqa: BLE001
        logger.exception("woo.missing failed")
    finally:
        schedule_check()


def arm() -> None:
    """Llamado una vez en el arranque del API."""
    try:
        schedule_check()
    except Exception as exc:  # noqa: BLE001
        logger.warning("woo.missing arm failed: %s", exc)


if __name__ == "__main__":  # pragma: no cover
    from app.db.session import get_engine

    with Session(get_engine()) as _s:
        print(run_periodic_check(_s, force=True))
