"""Sincronización diaria del índice de la KB de FLUX (worker-sync).

Job RQ self-rescheduling en la cola `kb:flux_sync`, armado a la hora de
Configuración ERP (`conocimiento.flux_sync_hora`, 04:30 de Madrid por
defecto) con el mismo patrón que el Cuadre nocturno (latido con SETNX +
`enqueue_in`, re-armado en `finally`). Interruptor
`conocimiento.flux_sync_activa` (encendido por defecto: es solo lectura de
una API pública). Si ya hubo pasada programada hace menos de 12 h, no repite.

Un primer pase a mano tras desplegar: `python -m app.services.conocimiento.flux_job`
(o «Sincronizar ahora» en Configuración ERP → Base de conocimiento).
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.erp.cuadre.job import proxima_ejecucion
from app.models.conocimiento import SYNC_ERROR, SYNC_PROGRAMADA, FluxSincronizacion
from app.services.conocimiento.config import HORA_DEFECTO, configuracion
from app.workers.queues import queue_name

logger = logging.getLogger(__name__)

FLUX_QUEUE = queue_name("kb", "flux_sync")
HEARTBEAT_KEY = "kb:flux:heartbeat"
JOB_TIMEOUT_SECONDS = 1200
SIN_REPETIR_HORAS = 12


def _ya_corrio(session: Session, ahora: datetime) -> bool:
    desde = ahora - timedelta(hours=SIN_REPETIR_HORAS)
    return session.scalars(select(FluxSincronizacion.id).where(
        FluxSincronizacion.origen == SYNC_PROGRAMADA,
        FluxSincronizacion.estado != SYNC_ERROR,
        FluxSincronizacion.inicio >= desde,
    ).limit(1)).first() is not None


def run_daily(session: Session, *, ahora: datetime | None = None, force: bool = False) -> bool:
    """La pasada diaria si toca (interruptor encendido y sin otra pasada
    programada en las últimas 12 h, o `force`). True si ha corrido."""
    from app.services.conocimiento.zendesk import sincronizar  # noqa: PLC0415

    ahora = ahora or datetime.now(UTC)
    config = configuracion(session)
    if not force:
        if not config["flux_sync_activa"]:
            return False
        if _ya_corrio(session, ahora):
            logger.info("kb.flux: ya hubo pasada hace menos de %d h; se salta", SIN_REPETIR_HORAS)
            return False
    sincronizar(session, origen=SYNC_PROGRAMADA, ahora=ahora)
    session.commit()
    return True


def _hora_configurada() -> str:
    try:
        from app.db.session import get_engine  # noqa: PLC0415

        with Session(get_engine()) as session:
            return str(configuracion(session)["flux_sync_hora"])
    except Exception:  # noqa: BLE001
        return HORA_DEFECTO


def schedule_daily(ahora: datetime | None = None) -> None:
    """Arma el siguiente tic a la hora configurada. Idempotente vía SETNX."""
    ahora = ahora or datetime.now(UTC)
    retraso = proxima_ejecucion(_hora_configurada(), ahora) - ahora
    try:
        from rq import Queue  # noqa: PLC0415

        from app.workers.queues import redis_connection  # noqa: PLC0415

        conn = redis_connection()
        ttl = max(int(retraso.total_seconds()) - 30, 10)
        if not conn.set(HEARTBEAT_KEY, "1", nx=True, ex=ttl):
            return
        try:
            Queue(FLUX_QUEUE, connection=conn,
                  default_timeout=JOB_TIMEOUT_SECONDS).enqueue_in(retraso, _daily_runner)
        except Exception as exc:  # noqa: BLE001
            logger.warning("kb.flux arm failed: %s", exc)
            conn.delete(HEARTBEAT_KEY)
    except Exception as exc:  # noqa: BLE001
        logger.warning("kb.flux redis unreachable: %s", exc)


def _daily_runner() -> None:
    """Entrada RQ. El re-armado va en `finally`: un fallo no corta la cadena."""
    try:
        from app.db.session import get_engine  # noqa: PLC0415

        with Session(get_engine()) as session:
            run_daily(session)
    except Exception:  # noqa: BLE001
        logger.exception("kb.flux daily failed")
    finally:
        schedule_daily()


def arm() -> None:
    """Llamado una vez en el arranque del API."""
    try:
        schedule_daily()
    except Exception as exc:  # noqa: BLE001
        logger.warning("kb.flux arm failed: %s", exc)


if __name__ == "__main__":  # pasada a mano (ignora el interruptor y la hora)
    from app.db.session import get_engine

    with Session(get_engine()) as _s:
        print(run_daily(_s, force=True))
