"""El histórico de ventas, sin que nadie pulse nada (worker-sync, cola
`erp:historico`).

- **El tic diario, a las 05:20 de Madrid:** recarga entero el ejercicio en
  curso (lo que se facturó ayer) y las familias; en enero, también el recién
  cerrado (lo que se apunta en él después del 31). Los demás cerrados no se
  recargan solos: ya no cambian, y la API solo los ve mientras estén
  reabiertos en el escritorio. Si a esa hora hay una carga pedida en marcha,
  el tic se reintenta cada 10 minutos. El patrón de los tics del repo
  (`bank/drive_job.py`): latido con SETNX + `enqueue_in`, re-armado en
  `finally`, armado desde `app/main.py` al arrancar.
- **Desde la pantalla** (`encolar_carga`): la primera carga de los trece
  ejercicios, o relanzar uno. Una carga a la vez; los ejercicios pedidos quedan
  «cargando» hasta que el trabajo los apunta.

Solo LEE FACTUSOL. Un pase a mano: `python -m app.erp.historico_job [ejercicio…]`.
"""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.erp.cuadre.job import proxima_ejecucion
from app.erp.ejercicio import PRIMER_EJERCICIO
from app.erp.models.historico import ESTADO_CARGANDO, ESTADO_ERROR, HistoricoEjercicio

logger = logging.getLogger(__name__)

HISTORICO_QUEUE = "erp:historico"
HEARTBEAT_KEY = "erp:historico:heartbeat"
#: Una carga a la vez (la del tic o la de la pantalla).
CARGA_KEY = "erp:historico:carga"
HORA = "05:20"
#: Los trece ejercicios se leen en ~92 s; guardar ~88.000 líneas, otro tanto.
JOB_TIMEOUT_SECONDS = 1800
MADRID = ZoneInfo("Europe/Madrid")


#: Si a la hora del tic hay una carga pedida en marcha, se reintenta así.
REINTENTO_TIC = timedelta(minutes=10)
REINTENTOS_TIC = 12
#: Hasta esta fecha del año se recarga también el ejercicio recién cerrado
#: (lo que se apunta en él después del 31 de diciembre).
FIN_CIERRE = (1, 31)


def _cliente_y_en_curso(session: Session):
    from app.erp.ejercicio import ejercicio_en_curso  # noqa: PLC0415
    from app.integrations.factusol.client import FactusolClient  # noqa: PLC0415

    return FactusolClient.from_settings(), ejercicio_en_curso(session)


def ejercicios_del_tic(en_curso: str, hoy: date) -> list[str]:
    """El en curso; en enero, también el anterior."""
    out = [en_curso]
    if (hoy.month, hoy.day) <= FIN_CIERRE and int(en_curso) - 1 >= PRIMER_EJERCICIO:
        out.append(str(int(en_curso) - 1))
    return out


def run(session: Session, ejercicios: list[str] | None = None) -> list[dict]:
    """Carga los ejercicios pedidos (por defecto, los del tic) y las familias."""
    from app.erp.historico import cargar  # noqa: PLC0415

    client, en_curso = _cliente_y_en_curso(session)
    pedidos = ejercicios or ejercicios_del_tic(en_curso, datetime.now(MADRID).date())
    return cargar(session, client, pedidos, en_curso=en_curso)


def marcar_cargando(session: Session, ejercicios: list[str]) -> None:
    ahora = datetime.now(UTC).replace(tzinfo=None)
    for e in ejercicios:
        fila = session.get(HistoricoEjercicio, e)
        if fila is None:
            fila = HistoricoEjercicio(ejercicio=e, estado=ESTADO_CARGANDO)
            session.add(fila)
        fila.estado, fila.intento_at, fila.detalle = ESTADO_CARGANDO, ahora, None
    session.commit()


def _marcar_fallo(session: Session, ejercicios: list[str], detalle: str) -> None:
    """Los que sigan «cargando» pasan a error (no se borra nada volcado)."""
    session.rollback()
    for e in ejercicios:
        fila = session.get(HistoricoEjercicio, e)
        if fila is not None and fila.estado == ESTADO_CARGANDO:
            fila.estado, fila.detalle = ESTADO_ERROR, detalle
    session.commit()


def _redis():
    from app.workers.queues import redis_connection  # noqa: PLC0415

    return redis_connection()


def _coger_carga(conn) -> str | None:
    """El cerrojo de «una carga a la vez»: devuelve su ficha, o None si está cogido."""
    ficha = uuid.uuid4().hex
    return ficha if conn.set(CARGA_KEY, ficha, nx=True, ex=JOB_TIMEOUT_SECONDS) else None


#: Borra el cerrojo solo si sigue siendo el nuestro (una carga que se pasó
#: del plazo no suelta el de otra).
_SOLTAR_SI_ES_MIO = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) end "
    "return 0"
)


def _soltar_carga(ficha: str) -> None:
    try:
        _redis().eval(_SOLTAR_SI_ES_MIO, 1, CARGA_KEY, ficha)
    except Exception:  # noqa: BLE001
        pass


def encolar_carga(session: Session, ejercicios: list[str]) -> str:
    """Encola la carga de esos ejercicios. Devuelve `encolada`, `ocupada` (ya
    hay una en marcha) o `sin_cola` (Redis caído: no se ha encolado nada)."""
    try:
        from rq import Queue  # noqa: PLC0415

        conn = _redis()
        ficha = _coger_carga(conn)
        if ficha is None:
            return "ocupada"
    except Exception as exc:  # noqa: BLE001
        logger.warning("historico: no se pudo encolar la carga (%s)", type(exc).__name__)
        return "sin_cola"
    # «Cargando» ANTES de encolar: si el trabajo arranca enseguida, lo que
    # apunte él no lo pisa la petición.
    marcar_cargando(session, ejercicios)
    try:
        Queue(HISTORICO_QUEUE, connection=conn,
              default_timeout=JOB_TIMEOUT_SECONDS).enqueue(_carga_runner, ejercicios, ficha)
    except Exception as exc:  # noqa: BLE001
        logger.warning("historico: no se pudo encolar la carga (%s)", type(exc).__name__)
        _soltar_carga(ficha)
        _marcar_fallo(session, ejercicios, "No se ha podido encolar la carga: vuelve a lanzarla.")
        return "sin_cola"
    return "encolada"


def _cargar_con_cerrojo(ejercicios: list[str] | None, ficha: str) -> None:
    """Carga y suelta el cerrojo. Si falla, se apunta solo el tipo de error
    (el mensaje podría llevar datos de clientes)."""
    from app.db.session import get_engine  # noqa: PLC0415

    with Session(get_engine()) as session:
        try:
            run(session, ejercicios)
        except Exception as exc:  # noqa: BLE001
            logger.error("historico: la carga falló (%s)", type(exc).__name__)
            if ejercicios:
                _marcar_fallo(session, ejercicios,
                              f"La carga falló ({type(exc).__name__}): vuelve a lanzarla.")
        finally:
            _soltar_carga(ficha)


def _carga_runner(ejercicios: list[str], ficha: str) -> None:
    """Entrada RQ de la carga pedida desde la pantalla."""
    _cargar_con_cerrojo(ejercicios, ficha)


def _tic(intento: int = 0) -> None:
    """El tic: si hay una carga pedida en marcha, se reintenta en un rato
    (puede ser de un año cerrado y no traer lo de hoy)."""
    ficha = _coger_carga(_redis())
    if ficha is None:
        if intento < REINTENTOS_TIC:
            from rq import Queue  # noqa: PLC0415

            Queue(HISTORICO_QUEUE, connection=_redis(),
                  default_timeout=JOB_TIMEOUT_SECONDS).enqueue_in(
                REINTENTO_TIC, _tic_reintento, intento + 1)
        else:
            logger.warning("historico: el tic de hoy no ha podido correr (carga en marcha)")
        return
    _cargar_con_cerrojo(None, ficha)


def _tic_reintento(intento: int) -> None:
    try:
        _tic(intento)
    except Exception as exc:  # noqa: BLE001
        logger.error("historico: reintento del tic fallido (%s)", type(exc).__name__)


def schedule_daily(ahora: datetime | None = None) -> None:
    """Arma el siguiente tic a las 05:20 de Madrid. Idempotente vía SETNX."""
    ahora = ahora or datetime.now(UTC)
    retraso = proxima_ejecucion(HORA, ahora) - ahora
    try:
        from rq import Queue  # noqa: PLC0415

        conn = _redis()
        ttl = max(int(retraso.total_seconds()) - 30, 10)
        if not conn.set(HEARTBEAT_KEY, "1", nx=True, ex=ttl):
            return
        try:
            Queue(HISTORICO_QUEUE, connection=conn,
                  default_timeout=JOB_TIMEOUT_SECONDS).enqueue_in(retraso, _daily_runner)
        except Exception as exc:  # noqa: BLE001
            logger.warning("historico arm failed: %s", type(exc).__name__)
            conn.delete(HEARTBEAT_KEY)
    except Exception as exc:  # noqa: BLE001
        logger.warning("historico redis unreachable: %s", type(exc).__name__)


def _daily_runner() -> None:
    """Entrada RQ del tic diario. El re-armado va en `finally`."""
    try:
        _tic()
    except Exception as exc:  # noqa: BLE001
        logger.error("historico daily failed (%s)", type(exc).__name__)
    finally:
        schedule_daily()


def arm() -> None:
    """Llamado una vez en el arranque del API."""
    try:
        schedule_daily()
    except Exception as exc:  # noqa: BLE001
        logger.warning("historico arm failed: %s", type(exc).__name__)


if __name__ == "__main__":  # pase a mano
    import sys

    from app.db.session import get_engine

    with Session(get_engine()) as _s:
        for r in run(_s, sys.argv[1:] or None):
            print(r)
