"""Cuándo se recalcula el resumen ERP por empresa (1/11/2026).

- **Cada noche**, después del tic del histórico (05:20 de Madrid): los meses
  avanzan solos (antigüedad, meses sin comprar, dormidos).
- **Tras cada carga del histórico**, también la pedida desde la pantalla.
- **Poco después de un cambio de Bart** que lo afecta (marcas de máquina,
  familias, gamas, equivalencias, alias, bajas, series, umbrales): se programa
  a `MARGEN` del primer cambio y los que llegan mientras tanto van en el mismo
  pase; los que llegan con el pase ya en marcha programan otro.
- **Con el botón «Recalcular ahora»** de la pantalla de contactos.

Un pase a la vez (cerrojo en Redis). Va por la cola del histórico
(worker-sync). Si Redis no está, no se programa nada y queda en el registro;
la pantalla sigue enseñando la hora del último recálculo.
"""
from __future__ import annotations

import logging
from datetime import timedelta

logger = logging.getLogger(__name__)

PROGRAMADO_KEY = "crm:resumen-erp:programado"
CORRIENDO_KEY = "crm:resumen-erp:corriendo"
#: Margen tras un cambio: los cambios seguidos se juntan en un pase.
MARGEN = timedelta(seconds=45)
JOB_TIMEOUT_SECONDS = 900


def _redis():
    """Con tiempos de espera cortos: si Redis no responde, se dice
    `sin_cola` en un par de segundos en vez de colgar a quien llama."""
    import os  # noqa: PLC0415

    from redis import Redis  # noqa: PLC0415

    return Redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
                          socket_connect_timeout=2, socket_timeout=5)


def _cola():
    from rq import Queue  # noqa: PLC0415

    from app.erp.historico_job import HISTORICO_QUEUE  # noqa: PLC0415

    return Queue(HISTORICO_QUEUE, connection=_redis(), default_timeout=JOB_TIMEOUT_SECONDS)


def encolar(motivo: str = "cambio", *, ya: bool = False) -> str:
    """Programa un recálculo. Devuelve `programado`, `ya_programado` o
    `sin_cola`. Con `ya`, sin margen (el botón)."""
    conn, puesto = None, False
    try:
        conn = _redis()
        if not conn.set(PROGRAMADO_KEY, motivo, nx=True,
                        ex=int(MARGEN.total_seconds()) + 120):
            return "ya_programado"
        puesto = True
        if ya:
            _cola().enqueue(_runner)
        else:
            _cola().enqueue_in(MARGEN, _runner)
        return "programado"
    except Exception as exc:  # noqa: BLE001
        logger.warning("resumen ERP: no se pudo programar el recálculo (%s)",
                       type(exc).__name__)
        if conn is not None and puesto:
            # Se apuntó pero no se encoló: se borra el apunte para que el
            # próximo cambio pueda programarlo (si Redis sigue sin responder,
            # el apunte caduca solo).
            try:
                conn.delete(PROGRAMADO_KEY)
            except Exception:  # noqa: BLE001
                pass
        return "sin_cola"


def _runner() -> None:
    """Entrada RQ: un pase, con cerrojo. Si hay otro en marcha, se reprograma."""
    from sqlalchemy.orm import Session  # noqa: PLC0415

    from app.db.session import get_engine  # noqa: PLC0415
    from app.services.empresa_erp import recalcular  # noqa: PLC0415

    conn = _redis()
    conn.delete(PROGRAMADO_KEY)       # los cambios a partir de aquí programan otro
    if not conn.set(CORRIENDO_KEY, "1", nx=True, ex=JOB_TIMEOUT_SECONDS):
        encolar("en espera")
        return
    try:
        with Session(get_engine()) as session:
            recalcular(session)
    except Exception as exc:  # noqa: BLE001
        # Ya está apuntado en crm_resumen_estado (la pantalla lo enseña).
        logger.error("resumen ERP: recálculo fallido (%s)", type(exc).__name__)
    finally:
        conn.delete(CORRIENDO_KEY)


def tras_cambio() -> None:
    """Para colgar de las rutas que cambian lo que lee el resumen (como tarea
    de fondo, después de responder): programa un recálculo sin que un fallo
    de la cola afecte a la respuesta."""
    try:
        encolar("cambio")
    except Exception:  # noqa: BLE001
        pass
