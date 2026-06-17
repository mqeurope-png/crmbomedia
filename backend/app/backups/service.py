"""Sprint Backup. Runner del script `scripts/backup-crmbo.sh`.

Dos puntos de entrada:

- `run_backup(backup_id)`: invoca el bash script, captura la línea
  `STATS|...` del stdout y actualiza la row de `backups` con
  status + filename + filepath + size_bytes + drive_url.
- `enqueue_manual_backup(...)`: usado por el endpoint admin. Crea la
  row en estado `RUNNING` y encola un job RQ que llama a
  `run_backup(backup_id)`.

El cron usa la CLI `python -m app.backups.cli` que también pasa por
`run_backup` — así toda la lógica de persistencia vive en un sitio.
"""
from __future__ import annotations

import logging
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from rq import Queue
from sqlalchemy.orm import Session

from app.db.session import get_engine
from app.models.crm import Backup, BackupStatus, BackupTrigger
from app.workers.queues import queue_for, queue_name, redis_connection

logger = logging.getLogger(__name__)

# Path al bash script. Lo resolvemos relativo al repo en dev y a la
# instalación bajo `/opt/crmbo` en producción. Override por env var
# si Bart instala el script en otra ubicación.
BACKUP_SCRIPT_ENV = "BACKUP_SCRIPT_PATH"
DEFAULT_REPO_SCRIPT = (
    Path(__file__).resolve().parents[3] / "scripts" / "backup-crmbo.sh"
)
DEFAULT_PROD_SCRIPT = Path("/opt/crmbo/scripts/backup-crmbo.sh")


def _resolve_script_path() -> Path:
    """Resuelve la ubicación del bash. Producción primero (path
    estable), luego repo (dev), luego override por env. Documentamos
    el override en `docs/backup-setup.md`."""
    env_override = os.environ.get(BACKUP_SCRIPT_ENV)
    if env_override:
        return Path(env_override)
    if DEFAULT_PROD_SCRIPT.exists():
        return DEFAULT_PROD_SCRIPT
    return DEFAULT_REPO_SCRIPT


def _parse_stats_line(line: str) -> dict[str, str]:
    """`STATS|status=success|filename=X|filepath=Y|size_bytes=N|drive_url=Z`
    → dict. Tolerante: keys ausentes quedan fuera del dict."""
    if not line.startswith("STATS|"):
        return {}
    out: dict[str, str] = {}
    for part in line[len("STATS|") :].split("|"):
        if "=" not in part:
            continue
        k, _, v = part.partition("=")
        out[k.strip()] = v.strip()
    return out


def _extract_stats(stdout: str) -> dict[str, str]:
    """Recorre el stdout buscando la última línea que empiece por
    `STATS|`. Solo nos interesa la última (el script emite una sola,
    pero somos defensivos)."""
    last: dict[str, str] = {}
    for line in stdout.splitlines():
        parsed = _parse_stats_line(line.strip())
        if parsed:
            last = parsed
    return last


def run_backup(backup_id: str) -> dict[str, Any]:
    """Entry point invocado por el worker RQ + la CLI cron.

    Asume que la row `backup_id` ya existe en estado `RUNNING`. Si no
    existe, falla rápido (el llamador debe crearla primero) — esto
    deja al runner libre de la responsabilidad de gestionar trigger /
    user / timestamps de creación.
    """
    session = Session(get_engine())
    try:
        backup = session.get(Backup, backup_id)
        if backup is None:
            logger.error("backup_id=%s missing; nothing to do", backup_id)
            return {"status": "missing"}

        script_path = _resolve_script_path()
        if not script_path.exists():
            backup.status = BackupStatus.FAILED.value
            backup.error_summary = (
                f"Backup script no encontrado: {script_path}. "
                f"Configura {BACKUP_SCRIPT_ENV} o instala en {DEFAULT_PROD_SCRIPT}."
            )
            backup.finished_at = datetime.now(UTC)
            session.commit()
            return {"status": BackupStatus.FAILED.value, "error": backup.error_summary}

        logger.info("backup_id=%s starting via %s", backup_id, script_path)
        # Limit: el script puede tardar varios minutos (mysqldump +
        # gzip + gpg + rclone sobre red). 30 min cubre buzones grandes
        # sin colgar el worker indefinidamente. Si el timeout pega es
        # síntoma de algo roto en VPS — el error_summary lo refleja.
        try:
            result = subprocess.run(  # noqa: S603 — path constante, sin shell
                [str(script_path)],
                capture_output=True,
                text=True,
                timeout=30 * 60,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            backup.status = BackupStatus.FAILED.value
            backup.error_summary = (
                f"Backup script timeout tras {exc.timeout:.0f}s. "
                "Probable cuelgue en mysqldump o rclone."
            )
            backup.finished_at = datetime.now(UTC)
            session.commit()
            return {"status": BackupStatus.FAILED.value, "error": backup.error_summary}

        stats = _extract_stats(result.stdout)
        stderr_tail = "\n".join(result.stderr.splitlines()[-20:])

        # `result.returncode != 0` y/o `status=failed` significan fallo.
        # El script imprime `STATS|status=failed|error=...` antes de
        # exit; si murió aún antes, no hay línea STATS y caemos al
        # fallback genérico.
        if result.returncode != 0 or stats.get("status") != "success":
            backup.status = BackupStatus.FAILED.value
            error_msg = stats.get("error") or stderr_tail or (
                f"Script terminó con código {result.returncode} "
                "sin línea STATS|status=success."
            )
            backup.error_summary = error_msg[:2000]
            backup.finished_at = datetime.now(UTC)
            session.commit()
            return {"status": BackupStatus.FAILED.value, "error": error_msg}

        # Éxito. Actualiza la row con los datos del archivo generado.
        backup.filename = stats.get("filename") or backup.filename
        backup.filepath = stats.get("filepath") or backup.filepath
        try:
            backup.size_bytes = int(stats.get("size_bytes") or 0)
        except ValueError:
            backup.size_bytes = 0
        backup.drive_url = stats.get("drive_url") or None
        backup.status = BackupStatus.SUCCESS.value
        backup.finished_at = datetime.now(UTC)
        session.commit()
        logger.info(
            "backup_id=%s success file=%s size=%d",
            backup_id,
            backup.filename,
            backup.size_bytes,
        )
        return {
            "status": BackupStatus.SUCCESS.value,
            "filename": backup.filename,
            "size_bytes": backup.size_bytes,
            "drive_url": backup.drive_url,
        }
    finally:
        session.close()


def create_backup_row(
    session: Session,
    *,
    triggered_by: BackupTrigger | str,
    user_id: str | None = None,
) -> Backup:
    """Inserta la row inicial `RUNNING`. Llamada antes de encolar el
    job (manual) o antes de invocar `run_backup` directamente (CLI
    cron). `filename`/`filepath` se rellenan vacíos — el runner los
    actualiza al leer STATS."""
    trigger_value = (
        triggered_by.value if isinstance(triggered_by, BackupTrigger) else triggered_by
    )
    backup = Backup(
        id=str(uuid4()),
        filename="",
        filepath="",
        size_bytes=0,
        status=BackupStatus.RUNNING.value,
        triggered_by=trigger_value,
        started_at=datetime.now(UTC),
        created_by_user_id=user_id,
    )
    session.add(backup)
    session.flush()
    return backup


def enqueue_manual_backup(
    session: Session, *, user_id: str
) -> tuple[str, str]:
    """Crea la row + encola RQ job. Devuelve `(backup_id, rq_job_id)`."""
    backup = create_backup_row(
        session, triggered_by=BackupTrigger.MANUAL, user_id=user_id
    )
    session.commit()
    # Reutilizamos `queue_for` (Redis del worker) con un sistema
    # virtual `backups` — no toca integration_accounts.
    queue = queue_for("backups", "create")
    job = queue.enqueue(run_backup, backup.id, retry=None, job_timeout=35 * 60)
    return backup.id, job.id


# ---------------------------------------------------------------------------
# Scheduled job — Sprint Backup-Hardening
#
# El cron de Linux (`/etc/crontab`) requería instalar entries a mano y NO
# pasaba por la app: los backups generados quedaban como filas huérfanas
# en disco sin row en BD. Reemplazamos con un job RQ self-rescheduling
# (mismo patrón que `brevo.scheduler` — heartbeat con SETNX guard) para
# que los backups automáticos sí entren a la tabla y aparezcan en
# `/admin/backups`.
# ---------------------------------------------------------------------------

DEFAULT_BACKUP_INTERVAL_HOURS = 72
_SCHEDULER_LOCK_KEY = "backups:scheduler:lock"


def _interval_hours() -> int:
    """Override por env var para tests / staging. 72 h en producción."""
    raw = os.environ.get("BACKUP_INTERVAL_HOURS", "")
    try:
        value = int(raw) if raw else DEFAULT_BACKUP_INTERVAL_HOURS
    except ValueError:
        value = DEFAULT_BACKUP_INTERVAL_HOURS
    return max(1, value)


def scheduled_backup_runner() -> None:
    """Job RQ ejecutado por el scheduler cada `BACKUP_INTERVAL_HOURS`.

    Crea su propia row `triggered_by='cron'` (sin user_id), invoca
    `run_backup` síncronamente, y re-arma el siguiente tick. NO usa
    `enqueue_manual_backup` para mantener la triggered_by limpia
    (auditoría) y permitir backups automáticos aunque NO haya admin
    autenticado.
    """
    from datetime import datetime as _dt  # noqa: PLC0415

    session = Session(get_engine())
    try:
        backup = create_backup_row(
            session, triggered_by=BackupTrigger.CRON, user_id=None
        )
        session.commit()
        backup_id = backup.id
    finally:
        session.close()

    logger.info(
        "backups.scheduled run starting id=%s ts=%s",
        backup_id,
        _dt.now(UTC).isoformat(),
    )
    try:
        run_backup(backup_id)
    finally:
        # Re-arm pase lo que pase. Si la run falló, el siguiente tick
        # debe seguir intentándolo dentro de 72 h en lugar de detener
        # la cadena.
        try:
            schedule_periodic_backup()
        except Exception as exc:  # noqa: BLE001
            logger.warning("backups.scheduler re-arm failed: %s", exc)


def schedule_periodic_backup() -> None:
    """Arma el siguiente tick del scheduler. Idempotente vía SETNX:
    si dos procesos API arrancan simultáneamente, solo uno gana el
    lock y encola; el otro no-op.

    Llamado:
    - Al arrancar la API (`arm_periodic_jobs()` en `main.py`).
    - Al final de cada `scheduled_backup_runner` para re-armar.
    """
    from datetime import timedelta as _td  # noqa: PLC0415

    try:
        conn = redis_connection()
        interval = _td(hours=_interval_hours())
        # TTL más corto que el interval para que un restart que
        # perdió el SETNX re-arme dentro de un tick.
        ttl = max(60, int(interval.total_seconds()) - 30)
        if not conn.set(_SCHEDULER_LOCK_KEY, "1", nx=True, ex=ttl):
            logger.debug("backups.scheduler already armed; skipping")
            return
        try:
            queue = Queue(
                queue_name("backups", "create"), connection=conn
            )
            queue.enqueue_in(
                interval,
                scheduled_backup_runner,
                job_timeout=35 * 60,
            )
            logger.info(
                "backups.scheduler armed next_run_in=%.0fs",
                interval.total_seconds(),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("backups.scheduler enqueue failed: %s", exc)
            conn.delete(_SCHEDULER_LOCK_KEY)
    except Exception as exc:  # noqa: BLE001
        # Redis caído al arranque NO debe tirar abajo la API. El
        # siguiente reinicio re-intenta el arm.
        logger.warning("backups.scheduler redis unreachable: %s", exc)


__all__ = [
    "BACKUP_SCRIPT_ENV",
    "DEFAULT_BACKUP_INTERVAL_HOURS",
    "DEFAULT_PROD_SCRIPT",
    "DEFAULT_REPO_SCRIPT",
    "create_backup_row",
    "enqueue_manual_backup",
    "run_backup",
    "schedule_periodic_backup",
    "scheduled_backup_runner",
]
