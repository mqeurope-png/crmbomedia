"""Sincronización diaria del catálogo (worker-sync): el compositor y las
tiendas, una tras otra; y detrás, con su propio interruptor
(`conocimiento.medios_sync_activa`), las imágenes de las webs y la tabla de
vídeos de los correos compuestos. Cada pasada va por su cuenta: una que
falla no corta las demás.

Job RQ self-rescheduling en la cola `kb:catalogo_sync`, armado a la hora de
Configuración ERP (`conocimiento.catalogo_sync_hora`, 04:45 de Madrid por
defecto) con el mismo patrón que el índice de FLUX (latido con SETNX +
`enqueue_in`, re-armado en `finally`). Interruptor
`conocimiento.catalogo_sync_activa` (encendido por defecto: solo lectura). Si
ya hubo pasada programada hace menos de 12 h, no repite (el catálogo y los
medios, cada uno con las suyas).

Un primer pase a mano tras desplegar: `python -m app.services.conocimiento.catalogo_job`
(o «Sincronizar ahora» en Configuración ERP → Base de conocimiento → Catálogo).
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.erp.cuadre.job import proxima_ejecucion
from app.models.conocimiento import (
    FUENTES,
    FUENTES_MEDIOS,
    SYNC_ERROR,
    SYNC_PROGRAMADA,
    CatalogoSincronizacion,
)
from app.services.conocimiento.config import HORA_CATALOGO_DEFECTO, configuracion
from app.workers.queues import queue_name

logger = logging.getLogger(__name__)

CATALOGO_QUEUE = queue_name("kb", "catalogo_sync")
HEARTBEAT_KEY = "kb:catalogo:heartbeat"
JOB_TIMEOUT_SECONDS = 1800
SIN_REPETIR_HORAS = 12


def _ya_corrio(session: Session, ahora: datetime, fuentes: tuple[str, ...] = FUENTES) -> bool:
    desde = ahora - timedelta(hours=SIN_REPETIR_HORAS)
    # Solo las pasadas de esas fuentes: las del catálogo y las de imágenes y
    # vídeos van al mismo histórico y cada grupo cuenta las suyas.
    return session.scalars(select(CatalogoSincronizacion.id).where(
        CatalogoSincronizacion.fuente.in_(fuentes),
        CatalogoSincronizacion.origen == SYNC_PROGRAMADA,
        CatalogoSincronizacion.estado != SYNC_ERROR,
        CatalogoSincronizacion.inicio >= desde,
    ).limit(1)).first() is not None


def run_daily(session: Session, *, ahora: datetime | None = None, force: bool = False) -> bool:
    """Las pasadas que tocan: las del catálogo (compositor y tiendas) y,
    detrás, las de imágenes y vídeos; cada grupo si su interruptor está
    encendido y no hubo pasada programada suya en las últimas 12 h (o con
    `force`). Una pasada que falla no corta las siguientes. True si ha corrido
    alguna."""
    from app.services.conocimiento import (  # noqa: PLC0415
        catalogo_compositor,
        catalogo_tiendas,
        imagenes,
        videos,
    )

    ahora = ahora or datetime.now(UTC)
    config = configuracion(session)

    def _toca(interruptor: str, fuentes: tuple[str, ...], nombre: str) -> bool:
        if force:
            return True
        if not config.get(interruptor, True):
            return False
        if _ya_corrio(session, ahora, fuentes):
            logger.info("kb.%s: ya hubo pasada hace menos de %d h; se salta", nombre,
                        SIN_REPETIR_HORAS)
            return False
        return True

    pasadas = []
    if _toca("catalogo_sync_activa", FUENTES, "catalogo"):
        pasadas += [catalogo_compositor.sincronizar, catalogo_tiendas.sincronizar]
    # Detrás del catálogo: las imágenes de un producto de la tienda se
    # descartan con los productos que acaba de leer.
    if _toca("medios_sync_activa", FUENTES_MEDIOS, "medios"):
        pasadas += [imagenes.sincronizar, videos.sincronizar]
    for sincronizar in pasadas:
        try:
            sincronizar(session, origen=SYNC_PROGRAMADA, ahora=ahora)
            session.commit()
        except Exception:  # noqa: BLE001 — una pasada que falla no corta las demás
            session.rollback()
            logger.exception("kb.catalogo: %s falló", sincronizar.__module__)
    return bool(pasadas)


def _hora_configurada() -> str:
    try:
        from app.db.session import get_engine  # noqa: PLC0415

        with Session(get_engine()) as session:
            return str(configuracion(session)["catalogo_sync_hora"])
    except Exception:  # noqa: BLE001
        return HORA_CATALOGO_DEFECTO


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
            Queue(CATALOGO_QUEUE, connection=conn,
                  default_timeout=JOB_TIMEOUT_SECONDS).enqueue_in(retraso, _daily_runner)
        except Exception as exc:  # noqa: BLE001
            logger.warning("kb.catalogo arm failed: %s", exc)
            conn.delete(HEARTBEAT_KEY)
    except Exception as exc:  # noqa: BLE001
        logger.warning("kb.catalogo redis unreachable: %s", exc)


def _daily_runner() -> None:
    """Entrada RQ. El re-armado va en `finally`: un fallo no corta la cadena."""
    try:
        from app.db.session import get_engine  # noqa: PLC0415

        with Session(get_engine()) as session:
            run_daily(session)
    except Exception:  # noqa: BLE001
        logger.exception("kb.catalogo daily failed")
    finally:
        schedule_daily()


def arm() -> None:
    """Llamado una vez en el arranque del API."""
    try:
        schedule_daily()
    except Exception as exc:  # noqa: BLE001
        logger.warning("kb.catalogo arm failed: %s", exc)


if __name__ == "__main__":  # pasada a mano (ignora el interruptor y la hora)
    from app.db.session import get_engine

    with Session(get_engine()) as _s:
        print(run_daily(_s, force=True))
