"""El recorrido MENSUAL del índice de las webs (worker-web, cola `webs:indice`).

El patrón de los tics diarios del repo (`catalogo_job`, `flux_job`): latido
con SETNX + `enqueue_in` a la hora de Configuración ERP, re-armado en
`finally`, armado desde `app/main.py` al arrancar. Cada día, a
`conocimiento.webs_indice_hora` (05:20 de Madrid de partida: fuera de las de
FLUX, 04:30, y del catálogo, 04:45), mira si toca: solo lanza una pasada
programada si la última programada correcta o parcial es de hace
`conocimiento.webs_indice_dias` días o más (30 de partida; el tic de ese día
ya la lanza) y no hay otra en marcha.

¿Por qué un tic diario y no un `enqueue_in` de 30 días? Un trabajo programado
que se pierde (Redis vaciado, despliegue) dejaría la cadena muerta un mes, y un
cambio de hora o de días no se aplicaría hasta el siguiente; así, como mucho
se pierde un día.

Interruptor `conocimiento.webs_indice_activo` (encendido de partida: solo
lee webs públicas). Un recorrido a mano: «Reindexar ahora» en Configuración
ERP, o `python -m app.services.webs.job`.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.erp.cuadre.job import proxima_ejecucion
from app.models.fuentes_externas import PASADA_OK, PASADA_PARCIAL
from app.services.webs import pasadas

logger = logging.getLogger(__name__)

HEARTBEAT_KEY = "webs:indice:heartbeat"
#: El tic solo mira y encola: corto.
JOB_TIMEOUT_SECONDS = 300
#: Margen al contar los días: el `inicio` de la pasada cae siempre algo
#: después del tic que la lanzó (y MySQL lo redondea al segundo), así que el
#: tic de N días después se quedaba corto por nada y la pasada salía cada
#: N+1 días. Menos de un día: no adelanta ninguna.
MARGEN_DIAS = timedelta(hours=2)


def _config(session: Session) -> dict:
    return pasadas._config(session)  # noqa: SLF001 — la misma lectura tolerante


def toca(session: Session, *, ahora: datetime | None = None) -> bool:
    """¿Toca una pasada programada? Interruptor encendido, ninguna en marcha y
    la última programada correcta o parcial de hace N días o más, con el
    margen de `MARGEN_DIAS` (o ninguna)."""
    ahora = ahora or datetime.now(UTC)
    config = _config(session)
    if not config.get("webs_indice_activo", True):
        return False
    if pasadas.en_curso(session, ahora=ahora) is not None:
        return False
    dias = int(config.get("webs_indice_dias") or 30)
    ultima = pasadas.ultima(session, origen=pasadas.ORIGEN_PROGRAMADA,
                            estados=(PASADA_OK, PASADA_PARCIAL))
    if ultima is None:
        return True
    referencia = pasadas._con_zona(ultima.inicio or ultima.created_at)  # noqa: SLF001
    return referencia is None or ahora - referencia >= timedelta(days=dias) - MARGEN_DIAS


def run_daily(session: Session, *, ahora: datetime | None = None, force: bool = False) -> bool:
    """Lanza la pasada programada si toca (o con `force`). True si la ha
    encolado."""
    if not force and not toca(session, ahora=ahora):
        session.commit()
        return False
    pasadas.encolar_pasada(session, pasadas.ORIGEN_PROGRAMADA, None, ahora=ahora)
    return True


def _hora_configurada() -> str:
    from app.services.conocimiento.config import HORA_WEBS_INDICE_DEFECTO  # noqa: PLC0415

    try:
        from app.db.session import get_engine  # noqa: PLC0415

        with Session(get_engine()) as session:
            return str(_config(session).get("webs_indice_hora") or HORA_WEBS_INDICE_DEFECTO)
    except Exception:  # noqa: BLE001
        return HORA_WEBS_INDICE_DEFECTO


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
            Queue(pasadas.WEBS_QUEUE, connection=conn,
                  default_timeout=JOB_TIMEOUT_SECONDS).enqueue_in(retraso, _daily_runner)
        except Exception as exc:  # noqa: BLE001
            logger.warning("webs.indice arm failed: %s", exc)
            conn.delete(HEARTBEAT_KEY)
    except Exception as exc:  # noqa: BLE001
        logger.warning("webs.indice redis unreachable: %s", exc)


def _daily_runner() -> None:
    """Entrada RQ. El re-armado va en `finally`: un fallo no corta la cadena."""
    try:
        from app.db.session import get_engine  # noqa: PLC0415

        with Session(get_engine()) as session:
            run_daily(session)
    except Exception:  # noqa: BLE001
        logger.exception("webs.indice daily failed")
    finally:
        schedule_daily()


def arm() -> None:
    """Llamado una vez en el arranque del API."""
    try:
        schedule_daily()
    except Exception as exc:  # noqa: BLE001
        logger.warning("webs.indice arm failed: %s", exc)


if __name__ == "__main__":  # pasada a mano (ignora el interruptor y los días)
    from app.db.session import get_engine

    with Session(get_engine()) as _s:
        print(run_daily(_s, force=True))
