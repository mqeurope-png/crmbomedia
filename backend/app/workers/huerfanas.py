"""Ejecuciones de sincronización «en curso» que ya no lo están.

Un `sync_logs` se crea en `pending` al encolar el job y pasa a `running`
cuando el worker lo coge; el propio job lo cierra (success / failed…). Si el
worker muere a medias (contenedor recreado en un despliegue, caída), la fila
se queda en `pending`/`running` PARA SIEMPRE. Incidencia del 05/10/2026: el
dedup de `agilecrm:periodic_read` saltaba cualquier cuenta con una fila así,
y 8 de 9 cuentas llevaban 18 días sin sincronizar por filas de agosto.

Dos defensas, aquí:

- **Caducidad del «en curso»** (`inflight_vigente_desde`): una ejecución en
  `pending`/`running` que empezó (o se encoló) hace más de
  `SYNC_INFLIGHT_MAX_MINUTES` (60 por defecto) no cuenta como en curso para
  los dedup: el siguiente tick encola otra.
- **Cierre de huérfanas** (`cerrar_huerfanas`): marca `failed`, con un
  mensaje claro, las ejecuciones cuyo job de RQ ya no está vivo: el job no
  existe, terminó sin cerrar la fila, o está «started» en un worker que ya no
  late. Se mira RQ, no se supone: un job encolado o en marcha en un worker
  vivo (otro worker que escucha la misma cola) NO se toca. Corre al arrancar
  cada worker (`app.workers.worker.BoHubWorker`) sobre las colas que escucha.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.crm import SyncLog, SyncStatus

logger = logging.getLogger(__name__)

#: Estados de un `sync_logs` «sin terminar».
INFLIGHT_STATUSES: tuple[str, ...] = (SyncStatus.PENDING.value, SyncStatus.RUNNING.value)
#: Minutos tras los que una ejecución sin terminar deja de contar como «en
#: curso» para los dedup (env `SYNC_INFLIGHT_MAX_MINUTES`).
DEFAULT_INFLIGHT_MAX_MINUTES = 60
#: Un worker que no ha latido en este tiempo se da por muerto. Un worker con
#: un job en marcha late cada ~30 s (`job_monitoring_interval` de RQ).
LATIDO_MAX = timedelta(minutes=3)
#: Una fila sin job_id (el encolado aún no lo ha apuntado) se respeta un rato.
SIN_JOB_GRACIA = timedelta(minutes=5)

_ESTADOS_VIVOS = {"queued", "scheduled", "deferred"}


def inflight_max_minutes() -> int:
    raw = os.environ.get("SYNC_INFLIGHT_MAX_MINUTES")
    try:
        value = int(raw) if raw else DEFAULT_INFLIGHT_MAX_MINUTES
    except ValueError:
        value = DEFAULT_INFLIGHT_MAX_MINUTES
    return value if value > 0 else DEFAULT_INFLIGHT_MAX_MINUTES


def inflight_vigente_desde(ahora: datetime | None = None) -> datetime:
    """Una ejecución sin terminar cuenta como «en curso» solo si empezó (o se
    encoló, si aún no empezó) después de esto."""
    return (ahora or datetime.now(UTC)) - timedelta(minutes=inflight_max_minutes())


def inicio_sql() -> Any:
    """Expresión SQL del inicio de una ejecución: `started_at` o, si aún no
    empezó, `created_at`."""
    return func.coalesce(SyncLog.started_at, SyncLog.created_at)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def ejecucion_viva(sync_log: SyncLog, conn: Any, ahora: datetime) -> bool:
    """¿Sigue viva en RQ la ejecución de este `sync_log`?"""
    from rq.exceptions import NoSuchJobError  # noqa: PLC0415
    from rq.job import Job  # noqa: PLC0415

    if not sync_log.job_id:
        creada = _aware(sync_log.created_at)
        return creada is not None and ahora - creada < SIN_JOB_GRACIA
    try:
        job = Job.fetch(sync_log.job_id, connection=conn)
    except NoSuchJobError:
        return False
    status = job.get_status(refresh=False)
    estado = str(getattr(status, "value", status))
    if estado in _ESTADOS_VIVOS:
        return True
    if estado != "started":
        return False                          # terminó (o falló) sin cerrar la fila
    nombre = getattr(job, "worker_name", None)
    if not nombre:
        return True                           # no se puede saber: se respeta
    return _worker_vivo(nombre, conn, ahora)


def _worker_vivo(nombre: str, conn: Any, ahora: datetime) -> bool:
    from rq.worker import Worker  # noqa: PLC0415

    try:
        worker = Worker.find_by_key(Worker.redis_worker_namespace_prefix + nombre,
                                    connection=conn)
    except Exception:  # noqa: BLE001
        return True                           # duda: se respeta
    if worker is None:
        return False
    latido = _aware(getattr(worker, "last_heartbeat", None))
    return latido is None or ahora - latido <= LATIDO_MAX


def cola_de(sync_log: SyncLog) -> str:
    from app.workers.queues import queue_name  # noqa: PLC0415

    system = str(getattr(sync_log.system, "value", sync_log.system))
    return queue_name(system, sync_log.operation or "")


def cerrar_huerfanas(
    session: Session,
    *,
    conn: Any,
    colas: set[str] | None = None,
    motivo: str,
    ahora: datetime | None = None,
) -> int:
    """Marca `failed` las ejecuciones sin terminar cuyo job ya no está vivo.

    Solo las encoladas con `enqueue_sync_job` (con `job_id`): son las que
    tienen cola conocida y job de RQ que mirar. `colas` limita a las de esas
    colas (las que escucha el worker que arranca). Devuelve cuántas cerró."""
    ahora = ahora or datetime.now(UTC)
    filas = list(session.scalars(
        select(SyncLog).where(
            SyncLog.status.in_(INFLIGHT_STATUSES), SyncLog.job_id.is_not(None),
        )
    ))
    cerradas = 0
    for sync_log in filas:
        if colas is not None and cola_de(sync_log) not in colas:
            continue
        try:
            if ejecucion_viva(sync_log, conn, ahora):
                continue
        except Exception:  # noqa: BLE001 — Redis caído: no se decide nada
            logger.warning("huerfanas: no se pudo consultar RQ; no se cierra nada",
                           exc_info=True)
            return cerradas
        anterior = sync_log.status
        sync_log.status = SyncStatus.FAILED.value
        sync_log.finished_at = ahora
        sync_log.error_summary = (
            f"Ejecución interrumpida: se quedó en «{anterior}» y su job ya no existe en "
            f"el worker ({motivo}). Probable worker recreado o caído a mitad del job; "
            "la siguiente sincronización programada la repite."
        )
        _al_cerrar(session, sync_log)
        cerradas += 1
    if cerradas:
        session.commit()
        logger.warning("huerfanas: %d ejecución(es) sin terminar marcadas como fallidas (%s)",
                       cerradas, motivo)
    return cerradas


def _al_cerrar(session: Session, sync_log: SyncLog) -> None:
    """Lo que hay que poner al día al cerrar una huérfana: el estado RUNNING
    de un target de Brevo (si no, «Ejecutar ahora» se queda bloqueado)."""
    if cola_de(sync_log) != "brevo:push_target":
        return
    try:
        payload = json.loads(sync_log.metadata_json or "{}")
    except (TypeError, ValueError):
        return
    target_id = payload.get("target_id") if isinstance(payload, dict) else None
    if not target_id:
        return
    from app.models.brevo import BrevoSyncTarget, TargetRunStatus  # noqa: PLC0415

    target = session.get(BrevoSyncTarget, str(target_id))
    if target is not None and target.last_run_status == TargetRunStatus.RUNNING:
        target.last_run_status = TargetRunStatus.ERROR
        target.last_run_at = sync_log.finished_at
        target.last_run_stats_json = json.dumps(
            {"fatal": "Ejecución interrumpida (worker recreado o caído)."},
        )
