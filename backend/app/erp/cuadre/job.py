"""Cuadre — job nocturno en worker-sync y «Comprobar ahora».

- Job RQ self-rescheduling en la cola `cuadre:run` (la escucha `worker-sync`,
  `--with-scheduler`): se arma para la hora de Configuración ERP (03:00 de
  Madrid por defecto) y corre TODAS las comprobaciones activas (BoHub y
  FACTUSOL). Mismo patrón que `seguimiento_sync_job` (heartbeat con SETNX +
  `enqueue_in`; el re-armado va en `finally`).
- Detrás de un INTERRUPTOR (`cuadre.nocturno_activo`), APAGADO por defecto:
  primero se pulsa «Comprobar ahora» y se revisa el primer lote. Apagado, el
  job sigue armado y no hace nada.
- Si se cambia la hora, el tic ya armado corre a la hora vieja y se re-arma
  para la nueva; si ya hubo pasada nocturna hace menos de 12 h, no repite.
- «Comprobar ahora»: las de BoHub corren al momento (en la petición); las de
  FACTUSOL se ENCOLAN en la misma cola (nunca en la petición web: la API de
  DELSOL no se satura) y la pantalla enseña «comprobando…».
- Una pasada por fuente a la vez (cerrojo en Redis). Sin Redis, sin cerrojo.

Una pasada a mano: `python -m app.erp.cuadre.job`.
"""
from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from app.erp.cuadre.config import HORA_DEFECTO, cuadre_config
from app.erp.cuadre.registry import FUENTE_FACTUSOL, FUENTE_MYSQL, FUENTES
from app.workers.queues import queue_name

logger = logging.getLogger(__name__)

CUADRE_QUEUE = queue_name("cuadre", "run")
HEARTBEAT_KEY = "cuadre:nightly:heartbeat"
LOCK_KEY = "cuadre:lock:{fuente}"
JOB_TIMEOUT_SECONDS = 1800
LOCK_TTL_SECONDS = 2400
#: Si ya hubo pasada nocturna hace menos de esto, el tic no repite (al cambiar
#: la hora, el tic ya armado corre a la hora vieja y re-arma para la nueva).
SIN_REPETIR_HORAS = 12
ZONA = "Europe/Madrid"


# --- hora local (Madrid) -----------------------------------------------------------


def _ultimo_domingo(anio: int, mes: int) -> datetime:
    d = datetime(anio, mes + 1, 1, tzinfo=UTC) - timedelta(days=1) if mes < 12 \
        else datetime(anio, 12, 31, tzinfo=UTC)
    return d - timedelta(days=(d.weekday() + 1) % 7)


def _offset_madrid(momento_utc: datetime) -> timedelta:
    """Desfase de Madrid (CET/CEST, regla UE) por si la imagen no trae tzdata."""
    anio = momento_utc.year
    inicio = _ultimo_domingo(anio, 3).replace(hour=1)
    fin = _ultimo_domingo(anio, 10).replace(hour=1)
    return timedelta(hours=2) if inicio <= momento_utc < fin else timedelta(hours=1)


def a_local(momento_utc: datetime) -> datetime:
    """UTC → hora de Madrid (naive)."""
    try:
        from zoneinfo import ZoneInfo  # noqa: PLC0415

        return momento_utc.astimezone(ZoneInfo(ZONA)).replace(tzinfo=None)
    except Exception:  # noqa: BLE001 — sin tzdata: regla UE a mano
        return (momento_utc + _offset_madrid(momento_utc)).replace(tzinfo=None)


def a_utc(local: datetime) -> datetime:
    """Hora de Madrid (naive) → UTC."""
    try:
        from zoneinfo import ZoneInfo  # noqa: PLC0415

        return local.replace(tzinfo=ZoneInfo(ZONA)).astimezone(UTC)
    except Exception:  # noqa: BLE001
        aprox = local.replace(tzinfo=UTC) - timedelta(hours=1)
        return local.replace(tzinfo=UTC) - _offset_madrid(aprox)


def _hm(hora: str) -> tuple[int, int]:
    try:
        h, m = hora.split(":")
        return int(h), int(m)
    except (ValueError, AttributeError):
        h, m = HORA_DEFECTO.split(":")
        return int(h), int(m)


def proxima_ejecucion(hora: str, ahora: datetime) -> datetime:
    """Siguiente HH:MM de Madrid (en UTC) estrictamente después de `ahora` + 1
    min (un tic que llega un pelo antes no se vuelve a programar para hoy)."""
    h, m = _hm(hora)
    local = a_local(ahora + timedelta(minutes=1))
    objetivo = local.replace(hour=h, minute=m, second=0, microsecond=0)
    if objetivo <= local:
        objetivo += timedelta(days=1)
    return a_utc(objetivo)


# --- cerrojo --------------------------------------------------------------------------


@contextmanager
def cerrojo(fuente: str) -> Iterator[bool]:
    """Una pasada por fuente a la vez (botón y job). Cede `True` si se ha cogido
    (o si no hay Redis) y `False` si ya hay otra en curso."""
    clave = LOCK_KEY.format(fuente=fuente)
    try:
        from app.workers.queues import redis_connection  # noqa: PLC0415

        conn = redis_connection()
        token = str(uuid4())
        cogido = bool(conn.set(clave, token, nx=True, ex=LOCK_TTL_SECONDS))
    except Exception:  # noqa: BLE001 — Redis caído: se sigue sin cerrojo
        yield True
        return
    if not cogido:
        yield False
        return
    try:
        yield True
    finally:
        try:
            actual = conn.get(clave)
            if actual is not None and actual.decode() == token:
                conn.delete(clave)
        except Exception:  # noqa: BLE001
            logger.warning("cuadre: no se pudo soltar el cerrojo %s", clave, exc_info=True)


# --- pasadas ---------------------------------------------------------------------------


class PasadaEnCurso(RuntimeError):
    """Ya hay una pasada de esa fuente corriendo."""


def correr(
    session: Session, *, fuente: str, origen: str, lanzado_por: str | None = None,
    run_id: str | None = None, client: Any = None, ejercicio: str | None = None,
) -> Any:
    """Una pasada de una fuente bajo su cerrojo. Devuelve la `CuadreRun`."""
    from app.erp.cuadre.engine import ejecutar  # noqa: PLC0415
    from app.erp.models import CuadreRun  # noqa: PLC0415

    with cerrojo(fuente) as cogido:
        if not cogido:
            raise PasadaEnCurso(fuente)
        run = session.get(CuadreRun, run_id) if run_id else None
        return ejecutar(
            session, fuente=fuente, origen=origen, lanzado_por=lanzado_por, run=run,
            client=client, ejercicio=ejercicio,
        )


def comprobar_ahora(session: Session, *, user_id: str | None) -> dict[str, Any]:
    """«Comprobar ahora»: BoHub al momento; FACTUSOL a la cola de worker-sync."""
    from app.erp.cuadre.engine import (  # noqa: PLC0415
        comprobaciones_activas,
        marcar_error,
        nueva_pasada,
        pasadas_en_curso,
    )

    out: dict[str, Any] = {"mysql": None, "factusol": None}
    try:
        run = correr(session, fuente=FUENTE_MYSQL, origen="manual", lanzado_por=user_id)
        out["mysql"] = {"id": run.id, "estado": run.estado}
    except PasadaEnCurso:
        out["mysql"] = {"estado": "en_curso"}

    if not comprobaciones_activas(cuadre_config(session), FUENTE_FACTUSOL):
        return out
    pendiente = next(
        (r for r in pasadas_en_curso(session) if r.fuente == FUENTE_FACTUSOL), None,
    )
    if pendiente is not None:                 # ya hay una en cola / corriendo
        out["factusol"] = {"id": pendiente.id, "estado": pendiente.estado}
        return out
    run = nueva_pasada(session, fuente=FUENTE_FACTUSOL, origen="manual", lanzado_por=user_id)
    session.commit()
    try:
        _encolar(_run_factusol, run.id)
        out["factusol"] = {"id": run.id, "estado": run.estado}
    except Exception as exc:  # noqa: BLE001
        logger.warning("cuadre: no se pudo encolar la pasada de FACTUSOL: %s", exc)
        marcar_error(session, run.id, "No se pudo encolar la comprobación de FACTUSOL "
                                      "(¿Redis / worker-sync parados?).")
        out["factusol"] = {"id": run.id, "estado": "error"}
    return out


def _encolar(func: Any, *args: Any) -> None:
    from rq import Queue  # noqa: PLC0415

    from app.workers.queues import redis_connection  # noqa: PLC0415

    Queue(CUADRE_QUEUE, connection=redis_connection(),
          default_timeout=JOB_TIMEOUT_SECONDS).enqueue(func, *args)


def _run_factusol(run_id: str) -> None:
    """Entrada RQ de la pasada de FACTUSOL de «Comprobar ahora»."""
    from app.db.session import get_engine  # noqa: PLC0415
    from app.erp.cuadre.engine import marcar_error  # noqa: PLC0415

    with Session(get_engine()) as session:
        try:
            correr(session, fuente=FUENTE_FACTUSOL, origen="manual", run_id=run_id)
        except PasadaEnCurso:
            marcar_error(session, run_id, "Ya había una comprobación de FACTUSOL en curso.")
        except Exception:  # noqa: BLE001
            logger.exception("cuadre: la pasada de FACTUSOL ha fallado")
            session.rollback()
            marcar_error(session, run_id, "La comprobación de FACTUSOL ha fallado (ver log).")


def _ya_corrio(session: Session, ahora: datetime) -> bool:
    from sqlalchemy import select  # noqa: PLC0415

    from app.erp.models import CuadreRun  # noqa: PLC0415

    desde = ahora - timedelta(hours=SIN_REPETIR_HORAS)
    return session.scalars(select(CuadreRun.id).where(
        CuadreRun.origen == "nocturno", CuadreRun.started_at >= desde,
    ).limit(1)).first() is not None


def run_nightly(session: Session, *, ahora: datetime | None = None, force: bool = False) -> bool:
    """Pasada nocturna: todas las comprobaciones activas, si toca (interruptor
    encendido y sin otra pasada nocturna en las últimas 12 h, o `force`).
    True si ha corrido."""
    ahora = ahora or datetime.now(UTC)
    config = cuadre_config(session)
    if not force:
        if not config["nocturno_activo"]:
            return False
        if _ya_corrio(session, ahora):
            logger.info("cuadre: ya hubo pasada nocturna hace menos de %d h; se salta",
                        SIN_REPETIR_HORAS)
            return False
    for fuente in FUENTES:
        try:
            correr(session, fuente=fuente, origen="nocturno")
        except PasadaEnCurso:
            logger.info("cuadre: ya hay una pasada de %s en curso; se salta", fuente)
        except Exception:  # noqa: BLE001
            session.rollback()
            logger.exception("cuadre: la pasada nocturna de %s ha fallado", fuente)
    return True


# --- armado (job RQ self-rescheduling) ----------------------------------------------------


def _hora_configurada() -> str:
    try:
        from app.db.session import get_engine  # noqa: PLC0415

        with Session(get_engine()) as session:
            return str(cuadre_config(session)["hora"])
    except Exception:  # noqa: BLE001
        return HORA_DEFECTO


def schedule_nightly(ahora: datetime | None = None) -> None:
    """Arma el siguiente tic a la hora configurada. Idempotente vía SETNX (api y
    worker pueden llamarlo a la vez)."""
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
            Queue(CUADRE_QUEUE, connection=conn,
                  default_timeout=JOB_TIMEOUT_SECONDS).enqueue_in(retraso, _nightly_runner)
        except Exception as exc:  # noqa: BLE001
            logger.warning("cuadre.nightly arm failed: %s", exc)
            conn.delete(HEARTBEAT_KEY)
    except Exception as exc:  # noqa: BLE001
        logger.warning("cuadre.nightly redis unreachable: %s", exc)


def _nightly_runner() -> None:
    """Entrada RQ. El re-armado va en `finally`: un fallo no corta la cadena."""
    try:
        from app.db.session import get_engine  # noqa: PLC0415

        with Session(get_engine()) as session:
            run_nightly(session)
    except Exception:  # noqa: BLE001
        logger.exception("cuadre.nightly failed")
    finally:
        schedule_nightly()


def arm() -> None:
    """Llamado una vez en el arranque del API."""
    try:
        schedule_nightly()
    except Exception as exc:  # noqa: BLE001
        logger.warning("cuadre.nightly arm failed: %s", exc)


if __name__ == "__main__":  # pasada a mano (ignora el interruptor y la hora)
    from app.db.session import get_engine

    with Session(get_engine()) as _s:
        print(run_nightly(_s, force=True))
