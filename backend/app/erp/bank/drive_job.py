"""El extracto del banco por Drive, sin que nadie pulse nada (worker-sync,
cola `bank:drive`).

- **El tic diario, a las 05:20 de Madrid** (la hora del índice de las webs,
  fuera de FLUX 04:30 y del catálogo 04:45): para cada cuenta con hoja de
  Drive, la lee entera, importa lo que falta, recalcula las propuestas y
  escribe lo pendiente. El patrón de los tics del repo (`webs/job.py`):
  latido con SETNX + `enqueue_in`, re-armado en `finally`, armado desde
  `app/main.py` al arrancar.
- **Al confirmar una conciliación** (`encolar_escritura`): un trabajo que
  escribe en la hoja lo pendiente de esa cuenta, ~20 s después y uno por
  cuenta aunque se confirmen veinte seguidas. Si Redis no está, lo pendiente
  espera al tic diario o a «Traer de Drive».

Nada escribe en FACTUSOL. Un pase a mano: `python -m app.erp.bank.drive_job`.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.erp.cuadre.job import proxima_ejecucion
from app.erp.models import BankAccount

logger = logging.getLogger(__name__)

BANK_DRIVE_QUEUE = "bank:drive"
HEARTBEAT_KEY = "bank:drive:heartbeat"
#: Clave que evita encolar una escritura por cada confirmación.
ESCRITURA_KEY = "bank:drive:escribir:{account_id}"
#: Lo que se espera antes de escribir tras confirmar (se juntan las de un rato).
ESPERA_ESCRITURA = timedelta(seconds=20)
HORA = "05:20"
#: Bajar dos hojas de ~1 MB e importarlas cabe de sobra.
JOB_TIMEOUT_SECONDS = 600


def run_daily(session: Session) -> dict[str, str]:
    """Lee la hoja de cada cuenta que la tiene (una que falla no para a las
    demás). Devuelve, por cuenta, `ok`, el código del error u `ocupada`."""
    from app.erp.api.bank import _run_match  # noqa: PLC0415
    from app.erp.bank.drive import (  # noqa: PLC0415
        ORIGEN_PROGRAMADA,
        DriveBancoError,
        cerrojo,
        traer_de_drive,
    )
    from app.erp.bank.parsing import ParseError  # noqa: PLC0415

    out: dict[str, str] = {}
    cuentas = session.scalars(
        select(BankAccount).where(BankAccount.drive_file_id.is_not(None))
    ).all()
    for account in cuentas:
        with cerrojo(account.id) as cogido:
            if not cogido:
                out[account.id] = "ocupada"
                continue
            try:
                resumen = traer_de_drive(session, account, origen=ORIGEN_PROGRAMADA)
                if resumen.get("ok") and resumen.get("imported"):
                    _run_match(session, account.id)
                out[account.id] = "ok" if resumen.get("ok") else str(resumen.get("code"))
            except (DriveBancoError, ParseError) as exc:
                session.rollback()
                out[account.id] = getattr(exc, "code", "parse_error")
            except Exception:  # noqa: BLE001 — una cuenta no tumba el tic
                session.rollback()
                logger.exception("bank.drive: la cuenta %s falló en el tic diario", account.id)
                out[account.id] = "error"
    return out


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
            Queue(BANK_DRIVE_QUEUE, connection=conn,
                  default_timeout=JOB_TIMEOUT_SECONDS).enqueue_in(retraso, _daily_runner)
        except Exception as exc:  # noqa: BLE001
            logger.warning("bank.drive arm failed: %s", exc)
            conn.delete(HEARTBEAT_KEY)
    except Exception as exc:  # noqa: BLE001
        logger.warning("bank.drive redis unreachable: %s", exc)


def _daily_runner() -> None:
    """Entrada RQ. El re-armado va en `finally`: un fallo no corta la cadena."""
    try:
        from app.db.session import get_engine  # noqa: PLC0415

        with Session(get_engine()) as session:
            run_daily(session)
    except Exception:  # noqa: BLE001
        logger.exception("bank.drive daily failed")
    finally:
        schedule_daily()


def arm() -> None:
    """Llamado una vez en el arranque del API."""
    try:
        schedule_daily()
    except Exception as exc:  # noqa: BLE001
        logger.warning("bank.drive arm failed: %s", exc)


def encolar_escritura(account_id: str) -> bool:
    """Tras confirmar: encola la escritura en la hoja de esa cuenta, una sola
    aunque lleguen varias confirmaciones seguidas. False si no se ha podido
    (Redis caído): lo pendiente espera a la siguiente lectura."""
    try:
        from rq import Queue  # noqa: PLC0415

        from app.workers.queues import redis_connection  # noqa: PLC0415

        conn = redis_connection()
        clave = ESCRITURA_KEY.format(account_id=account_id)
        if not conn.set(clave, "1", nx=True, ex=int(ESPERA_ESCRITURA.total_seconds()) + 60):
            return True   # ya hay una en camino: se lleva también esta
        try:
            Queue(BANK_DRIVE_QUEUE, connection=conn,
                  default_timeout=JOB_TIMEOUT_SECONDS).enqueue_in(
                ESPERA_ESCRITURA, _escribir_runner, account_id,
            )
        except Exception:
            conn.delete(clave)   # que la próxima confirmación lo vuelva a intentar
            raise
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("bank.drive: no se pudo encolar la escritura de %s: %s", account_id, exc)
        return False


def _escribir_runner(account_id: str) -> None:
    """Entrada RQ de la escritura tras confirmar."""
    from app.db.session import get_engine  # noqa: PLC0415
    from app.erp.bank.drive import cerrojo, escribir_cuenta  # noqa: PLC0415

    try:
        from app.workers.queues import redis_connection  # noqa: PLC0415

        redis_connection().delete(ESCRITURA_KEY.format(account_id=account_id))
    except Exception:  # noqa: BLE001
        pass
    with Session(get_engine()) as session:
        account = session.get(BankAccount, account_id)
        if account is None:
            return
        with cerrojo(account_id) as cogido:
            if not cogido:
                # Hay otro pase de Drive en marcha y puede que ya haya pasado
                # por lo pendiente: se vuelve a intentar en un rato.
                encolar_escritura(account_id)
                return
            escribir_cuenta(session, account)


if __name__ == "__main__":  # pase a mano
    from app.db.session import get_engine

    with Session(get_engine()) as _s:
        print(run_daily(_s))
