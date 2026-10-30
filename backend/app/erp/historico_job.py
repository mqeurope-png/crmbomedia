"""El histórico de ventas, sin que nadie pulse nada (worker-sync, cola
`erp:historico`).

- **El tic diario, a las 05:20 de Madrid:** recarga entero el ejercicio en
  curso (lo que se facturó ayer) y las familias. Los ejercicios cerrados no se
  recargan solos: ya no cambian, y la API solo los ve mientras estén
  reabiertos en el escritorio. El patrón de los tics del repo
  (`bank/drive_job.py`): latido con SETNX + `enqueue_in`, re-armado en
  `finally`, armado desde `app/main.py` al arrancar.
- **Desde la pantalla** (`encolar_carga`): la primera carga de los trece
  ejercicios, o relanzar uno. Una carga a la vez; los ejercicios pedidos quedan
  «cargando» hasta que el trabajo los apunta.

Solo LEE FACTUSOL. Un pase a mano: `python -m app.erp.historico_job [ejercicio…]`.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.erp.cuadre.job import proxima_ejecucion
from app.erp.models.historico import ESTADO_CARGANDO, HistoricoEjercicio

logger = logging.getLogger(__name__)

HISTORICO_QUEUE = "erp:historico"
HEARTBEAT_KEY = "erp:historico:heartbeat"
#: Una carga a la vez (la del tic o la de la pantalla).
CARGA_KEY = "erp:historico:carga"
HORA = "05:20"
#: Los trece ejercicios se leen en ~92 s; guardar ~88.000 líneas, otro tanto.
JOB_TIMEOUT_SECONDS = 1800


def _cliente_y_en_curso(session: Session):
    from app.erp.ejercicio import ejercicio_en_curso  # noqa: PLC0415
    from app.integrations.factusol.client import FactusolClient  # noqa: PLC0415

    return FactusolClient.from_settings(), ejercicio_en_curso(session)


def run(session: Session, ejercicios: list[str] | None = None) -> list[dict]:
    """Carga los ejercicios pedidos (por defecto, el en curso) y las familias."""
    from app.erp.historico import cargar  # noqa: PLC0415

    client, en_curso = _cliente_y_en_curso(session)
    return cargar(session, client, ejercicios or [en_curso], en_curso=en_curso)


def marcar_cargando(session: Session, ejercicios: list[str]) -> None:
    ahora = datetime.now(UTC).replace(tzinfo=None)
    for e in ejercicios:
        fila = session.get(HistoricoEjercicio, e)
        if fila is None:
            fila = HistoricoEjercicio(ejercicio=e, estado=ESTADO_CARGANDO)
            session.add(fila)
        fila.estado, fila.intento_at, fila.detalle = ESTADO_CARGANDO, ahora, None
    session.commit()


def encolar_carga(session: Session, ejercicios: list[str]) -> str:
    """Encola la carga de esos ejercicios. Devuelve `encolada`, `ocupada` (ya
    hay una en marcha) o `sin_cola` (Redis caído: no se ha encolado nada)."""
    try:
        from rq import Queue  # noqa: PLC0415

        from app.workers.queues import redis_connection  # noqa: PLC0415

        conn = redis_connection()
        if not conn.set(CARGA_KEY, "1", nx=True, ex=JOB_TIMEOUT_SECONDS):
            return "ocupada"
        try:
            Queue(HISTORICO_QUEUE, connection=conn,
                  default_timeout=JOB_TIMEOUT_SECONDS).enqueue(_carga_runner, ejercicios)
        except Exception:
            conn.delete(CARGA_KEY)
            raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("historico: no se pudo encolar la carga: %s", exc)
        return "sin_cola"
    marcar_cargando(session, ejercicios)
    return "encolada"


def _soltar_carga() -> None:
    try:
        from app.workers.queues import redis_connection  # noqa: PLC0415

        redis_connection().delete(CARGA_KEY)
    except Exception:  # noqa: BLE001
        pass


def _carga_runner(ejercicios: list[str]) -> None:
    """Entrada RQ de la carga pedida desde la pantalla."""
    from app.db.session import get_engine  # noqa: PLC0415

    try:
        with Session(get_engine()) as session:
            run(session, ejercicios)
    finally:
        _soltar_carga()


def schedule_daily(ahora: datetime | None = None) -> None:
    """Arma el siguiente tic a las 05:20 de Madrid. Idempotente vía SETNX."""
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
            Queue(HISTORICO_QUEUE, connection=conn,
                  default_timeout=JOB_TIMEOUT_SECONDS).enqueue_in(retraso, _daily_runner)
        except Exception as exc:  # noqa: BLE001
            logger.warning("historico arm failed: %s", exc)
            conn.delete(HEARTBEAT_KEY)
    except Exception as exc:  # noqa: BLE001
        logger.warning("historico redis unreachable: %s", exc)


def _daily_runner() -> None:
    """Entrada RQ del tic. Si hay una carga pedida en marcha, este día se la
    salta (esa ya trae lo último). El re-armado va en `finally`."""
    try:
        from app.db.session import get_engine  # noqa: PLC0415
        from app.workers.queues import redis_connection  # noqa: PLC0415

        if not redis_connection().set(CARGA_KEY, "1", nx=True, ex=JOB_TIMEOUT_SECONDS):
            return
        try:
            with Session(get_engine()) as session:
                run(session)
        finally:
            _soltar_carga()
    except Exception:  # noqa: BLE001
        logger.exception("historico daily failed")
    finally:
        schedule_daily()


def arm() -> None:
    """Llamado una vez en el arranque del API."""
    try:
        schedule_daily()
    except Exception as exc:  # noqa: BLE001
        logger.warning("historico arm failed: %s", exc)


if __name__ == "__main__":  # pase a mano
    import sys

    from app.db.session import get_engine

    with Session(get_engine()) as _s:
        for r in run(_s, sys.argv[1:] or None):
            print(r)
