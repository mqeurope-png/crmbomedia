"""Seguimiento (app) — reconcile AUTOMÁTICO del espejo BoHub ↔ hoja (Fase 2).

Cada `seguimiento_reconcile_interval_minutes` (10 por defecto, mínimo 5) un job
RQ self-rescheduling en la cola `seguimiento:reconcile` —la escucha
`worker-sync` (`--with-scheduler`)— corre la MISMA pasada que el botón
«Actualizar hoja de Drive» (`push_managed_tabs`): la hoja se sincroniza sola en
los dos sentidos.

- Detrás de un INTERRUPTOR en Configuración ERP (`seguimiento_reconcile_enabled`),
  APAGADO por defecto: primero se revisa una pasada manual y luego se enciende.
  Con el interruptor apagado el latido sigue armado (no hace nada), así que
  encenderlo surte efecto en el siguiente tic, sin reiniciar nada.
- Un solo reconcile a la vez (cerrojo en Redis): el bucle y el botón nunca se
  pisan. Sin Redis, sin cerrojo (como antes).
- Mismo patrón que `services.vies_sweep` (heartbeat con SETNX + `enqueue_in`);
  el re-armado va en `finally` para que un fallo no corte la cadena.
- Confirma en BD SOLO si la hoja se escribió bien; si falla, se deshace y la
  siguiente pasada lo repite.

Una pasada a mano: `python -m app.erp.seguimiento_sync_job`.
"""
from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from app.workers.queues import queue_name

logger = logging.getLogger(__name__)

RECONCILE_QUEUE = queue_name("seguimiento", "reconcile")
HEARTBEAT_KEY = "seguimiento:reconcile:heartbeat"
RUNNING_KEY = "seguimiento:reconcile:running"
JOB_TIMEOUT_SECONDS = 600
RUNNING_TTL_SECONDS = 900
DEFAULT_INTERVAL_MINUTES = 10
MIN_INTERVAL_MINUTES = 5


def reconcile_config(session: Session) -> tuple[bool, int]:
    """(¿encendido?, intervalo en minutos) de Configuración ERP."""
    from app.integrations.factusol.service import series_config  # noqa: PLC0415

    cfg = series_config(session)
    enabled = bool(cfg.get("seguimiento_reconcile_enabled", False))
    try:
        minutes = int(cfg.get("seguimiento_reconcile_interval_minutes") or DEFAULT_INTERVAL_MINUTES)
    except (TypeError, ValueError):
        minutes = DEFAULT_INTERVAL_MINUTES
    return enabled, max(minutes, MIN_INTERVAL_MINUTES)


@contextmanager
def reconcile_lock() -> Iterator[bool]:
    """Cerrojo «un reconcile a la vez» (botón y bucle). Cede `True` si se ha
    cogido (o si no hay Redis: sin cerrojo, como antes) y `False` si ya hay otro
    en curso. Se libera al salir; caduca solo si el proceso muere."""
    try:
        from app.workers.queues import redis_connection  # noqa: PLC0415

        conn = redis_connection()
        token = str(uuid4())
        cogido = bool(conn.set(RUNNING_KEY, token, nx=True, ex=RUNNING_TTL_SECONDS))
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
            actual = conn.get(RUNNING_KEY)
            if actual is not None and actual.decode() == token:
                conn.delete(RUNNING_KEY)
        except Exception:  # noqa: BLE001
            logger.warning("seguimiento.reconcile: no se pudo soltar el cerrojo", exc_info=True)


def run_reconcile(session: Session, *, force: bool = False) -> dict[str, Any] | None:
    """Una pasada del reconcile, si toca: interruptor encendido (o `force`),
    Drive configurado y modo «pestaña gestionada». Devuelve el resumen, o None
    si no ha corrido. Confirma en BD solo si la hoja se escribió bien."""
    from app.erp.api.seguimiento import (  # noqa: PLC0415
        drive_completados_rows,
        drive_live_rows,
    )
    from app.erp.drive_managed import push_managed_tabs  # noqa: PLC0415
    from app.erp.drive_sheets import (  # noqa: PLC0415
        DriveConfigError,
        DriveSyncError,
        GoogleSheetsClient,
        drive_config,
    )
    from app.erp.models import ErpSettings  # noqa: PLC0415
    from app.erp.models.settings import ERP_SETTINGS_SINGLETON_ID  # noqa: PLC0415
    from app.integrations.factusol.service import series_config  # noqa: PLC0415

    enabled, _ = reconcile_config(session)
    if not (enabled or force):
        return None
    if series_config(session).get("drive_legacy_insert"):
        logger.info("seguimiento.reconcile: modo antiguo (drive_legacy_insert): no corre")
        return None
    try:
        conf = drive_config(session.get(ErpSettings, ERP_SETTINGS_SINGLETON_ID))
    except DriveConfigError as exc:
        logger.warning("seguimiento.reconcile: Drive mal configurado: %s", exc)
        return None
    if conf is None:
        return None
    info, spreadsheet_id = conf
    with reconcile_lock() as cogido:
        if not cogido:
            logger.info("seguimiento.reconcile: ya hay una sincronización en curso; se salta")
            return None
        try:
            resumen = push_managed_tabs(
                session, GoogleSheetsClient(info, spreadsheet_id), drive_live_rows(session),
                completados=drive_completados_rows(session),
            )
        except DriveSyncError as exc:
            session.rollback()
            logger.warning("seguimiento.reconcile: la hoja no se pudo escribir: %s", exc)
            return None
        session.commit()
    esp = resumen.get("espejo") or {}
    logger.info(
        "seguimiento.reconcile: %d filas de BoHub, %d a mano · %d ediciones leídas, "
        "%d manuales nuevas, %d inválidas, %d borradas, %d restauradas%s",
        resumen.get("rows", 0), resumen.get("manuales", 0), esp.get("ediciones_leidas", 0),
        esp.get("manuales_nuevas", 0), esp.get("manuales_invalidas", 0),
        esp.get("borradas", 0), esp.get("restauradas", 0),
        " · BORRADO MASIVO restaurado" if esp.get("borrado_masivo") else "",
    )
    return resumen


# --- armado (job RQ self-rescheduling) -------------------------------------------


def _interval() -> timedelta:
    try:
        from app.db.session import get_engine  # noqa: PLC0415

        with Session(get_engine()) as session:
            _, minutes = reconcile_config(session)
    except Exception:  # noqa: BLE001
        minutes = DEFAULT_INTERVAL_MINUTES
    return timedelta(minutes=max(minutes, MIN_INTERVAL_MINUTES))


def schedule_reconcile() -> None:
    """Arma el siguiente tic `interval` más tarde. Idempotente vía SETNX (api y
    worker pueden llamarlo a la vez)."""
    interval = _interval()
    try:
        from rq import Queue  # noqa: PLC0415

        from app.workers.queues import redis_connection  # noqa: PLC0415

        conn = redis_connection()
        ttl = max(int(interval.total_seconds()) - 30, 10)
        if not conn.set(HEARTBEAT_KEY, "1", nx=True, ex=ttl):
            return
        try:
            Queue(RECONCILE_QUEUE, connection=conn,
                  default_timeout=JOB_TIMEOUT_SECONDS).enqueue_in(interval, _reconcile_runner)
        except Exception as exc:  # noqa: BLE001
            logger.warning("seguimiento.reconcile arm failed: %s", exc)
            conn.delete(HEARTBEAT_KEY)
    except Exception as exc:  # noqa: BLE001
        logger.warning("seguimiento.reconcile redis unreachable: %s", exc)


def _reconcile_runner() -> None:
    """Entrada RQ. El re-armado va en `finally`: un fallo no corta la cadena."""
    try:
        from app.db.session import get_engine  # noqa: PLC0415

        with Session(get_engine()) as session:
            run_reconcile(session)
    except Exception:  # noqa: BLE001
        logger.exception("seguimiento.reconcile failed")
    finally:
        schedule_reconcile()


def arm() -> None:
    """Llamado una vez en el arranque del API."""
    try:
        schedule_reconcile()
    except Exception as exc:  # noqa: BLE001
        logger.warning("seguimiento.reconcile arm failed: %s", exc)


if __name__ == "__main__":  # pasada a mano (ignora el interruptor)
    from app.db.session import get_engine

    with Session(get_engine()) as _s:
        print(run_reconcile(_s, force=True))
