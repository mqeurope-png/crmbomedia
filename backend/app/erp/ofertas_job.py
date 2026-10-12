"""Ofertas — caducidad diaria (worker-sync).

Job RQ self-rescheduling en la cola `ofertas:caducar` (a las 06:20 de Madrid),
con el mismo patrón que el catálogo de la base de conocimiento: latido con
SETNX + `enqueue_in`, re-armado en `finally`. Marca `caducada` cada oferta
enviada cuya validez ha pasado y emite `oferta.caducada` para los workflows.
NO escribe en FACTUSOL ni envía nada.

Una pasada a mano: `python -m app.erp.ofertas_job`.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.erp.cuadre.job import proxima_ejecucion
from app.workers.queues import queue_name

logger = logging.getLogger(__name__)

OFERTAS_QUEUE = queue_name("ofertas", "caducar")
HEARTBEAT_KEY = "ofertas:caducar:heartbeat"
JOB_TIMEOUT_SECONDS = 600
#: Hora de Madrid de la pasada diaria.
HORA = "06:20"


def run_daily(session: Session, *, hoy=None) -> int:
    """Caduca las enviadas vencidas. Devuelve cuántas."""
    from app.erp.ofertas import caducar_pendientes  # noqa: PLC0415

    caducadas = caducar_pendientes(session, hoy=hoy)
    if caducadas:
        logger.info("ofertas: %d caducadas: %s", len(caducadas),
                    ", ".join(o.numero for o in caducadas))
    return len(caducadas)


def schedule_daily(ahora: datetime | None = None) -> None:
    """Arma el siguiente tic. Idempotente vía SETNX."""
    ahora = ahora or datetime.now(UTC)
    retraso = proxima_ejecucion(HORA, ahora) - ahora
    try:
        from rq import Queue  # noqa: PLC0415

        from app.workers.queues import redis_connection  # noqa: PLC0415

        conn = redis_connection()
        ttl = max(int(retraso.total_seconds()) - 30, 10)
        if not conn.set(HEARTBEAT_KEY, "1", nx=True, ex=ttl):
            return
        try:
            Queue(OFERTAS_QUEUE, connection=conn,
                  default_timeout=JOB_TIMEOUT_SECONDS).enqueue_in(retraso, _daily_runner)
        except Exception as exc:  # noqa: BLE001
            logger.warning("ofertas.caducar arm failed: %s", exc)
            conn.delete(HEARTBEAT_KEY)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ofertas.caducar redis unreachable: %s", exc)


def _daily_runner() -> None:
    """Entrada RQ. El re-armado va en `finally`: un fallo no corta la cadena."""
    try:
        from app.db.session import get_engine  # noqa: PLC0415

        with Session(get_engine()) as session:
            run_daily(session)
    except Exception:  # noqa: BLE001
        logger.exception("ofertas.caducar daily failed")
    finally:
        schedule_daily()


def arm() -> None:
    """Llamado una vez en el arranque del API."""
    try:
        schedule_daily()
    except Exception as exc:  # noqa: BLE001
        logger.warning("ofertas.caducar arm failed: %s", exc)


if __name__ == "__main__":  # pasada a mano
    from app.db.session import get_engine

    with Session(get_engine()) as _s:
        print(run_daily(_s))
