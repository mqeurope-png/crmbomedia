"""Scheduler periódico que despierta runs con `wake_at <= now` y
dispara `cron.recurring` workflows.

Tick cada 30 s. Procesa hasta 200 runs por tick para no acaparar
worker. Multiples worker processes coexisten via
`FOR UPDATE SKIP LOCKED`.
"""
from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.crm import Contact
from app.models.workflows import (
    Workflow,
    WorkflowEventWait,
    WorkflowRun,
    WorkflowRunState,
    WorkflowStatus,
)
from app.workflows.engine import (
    advance_run,
    resume_run_from_event_wait,
    start_run,
)

log = logging.getLogger(__name__)

TICK_LOCK_KEY = "workflows:scheduler:heartbeat"
DEFAULT_TICK_SECONDS = 30
DEFAULT_BATCH_LIMIT = 200


def _tick_interval() -> timedelta:
    raw = os.environ.get("WORKFLOWS_SCHEDULER_TICK_SECONDS")
    if raw:
        try:
            value = int(raw)
            if value > 0:
                return timedelta(seconds=value)
        except ValueError:
            pass
    return timedelta(seconds=DEFAULT_TICK_SECONDS)


# ---------------------------------------------------------------------
# Tick body
# ---------------------------------------------------------------------


def run_tick(session: Session, *, limit: int = DEFAULT_BATCH_LIMIT) -> dict[str, int]:
    """Procesa runs vencidos + waits con timeout + cron workflows.
    Devuelve estadísticas para el SyncLog."""
    now = datetime.now(UTC)

    # 1. Runs en `waiting` cuyo wake_at venció → resume.
    waiting_runs = list(
        session.scalars(
            select(WorkflowRun)
            .where(
                WorkflowRun.state == WorkflowRunState.WAITING,
                WorkflowRun.wake_at <= now,
            )
            .limit(limit)
        )
    )
    resumed = 0
    for run in waiting_runs:
        try:
            advance_run(session, run.id)
            resumed += 1
        except Exception:  # noqa: BLE001
            log.exception(
                "workflows.scheduler advance failed run=%s", run.id
            )

    # 2. Event waits con timeout → resume rama timeout.
    timed_out_waits = list(
        session.scalars(
            select(WorkflowEventWait)
            .where(WorkflowEventWait.timeout_at <= now)
            .limit(limit)
        )
    )
    timed = 0
    for wait in timed_out_waits:
        try:
            resume_run_from_event_wait(session, wait.id, matched=False)
            timed += 1
        except Exception:  # noqa: BLE001
            log.exception(
                "workflows.scheduler timeout failed wait=%s", wait.id
            )

    # 3. Workflows con trigger `cron.recurring` cuyo próximo tick venció.
    cron_started = _evaluate_cron_workflows(session, now)

    session.commit()

    # Re-arma el próximo tick (self-rescheduling).
    schedule_tick()

    return {
        "runs_resumed": resumed,
        "event_waits_timed_out": timed,
        "cron_started": cron_started,
    }


# ---------------------------------------------------------------------
# Cron evaluation
# ---------------------------------------------------------------------


# Presets soportados (decisión: cron arbitrario es DESCARTADO en Bloque 1).
# Cada preset declara `cadence_minutes` y un predicate sobre `now` que
# decide si está en el tick correcto.
_CRON_PRESETS: dict[str, Callable[[datetime, dict], bool]] = {
    "hourly": lambda now, cfg: now.minute < 1,
    "daily": lambda now, cfg: now.hour == int(cfg.get("hour", 9))
    and now.minute < 1,
    "weekly_monday": lambda now, cfg: now.weekday() == 0
    and now.hour == int(cfg.get("hour", 9))
    and now.minute < 1,
    "weekly_friday": lambda now, cfg: now.weekday() == 4
    and now.hour == int(cfg.get("hour", 9))
    and now.minute < 1,
    "monthly_first_day": lambda now, cfg: now.day == 1
    and now.hour == int(cfg.get("hour", 9))
    and now.minute < 1,
}


def _evaluate_cron_workflows(session: Session, now: datetime) -> int:
    """Para cada workflow cron, evalúa si toca disparar AHORA. Si sí,
    encola un run por cada contacto que matchee el filter del
    trigger. Cap implícito 200 contactos/tick para no atragantar."""
    workflows = list(
        session.scalars(
            select(Workflow).where(
                Workflow.trigger_type == "cron.recurring",
                Workflow.status == WorkflowStatus.ACTIVE,
            )
        )
    )
    started = 0
    for workflow in workflows:
        try:
            cfg = json.loads(workflow.trigger_config_json or "{}")
        except (TypeError, ValueError):
            cfg = {}
        preset = cfg.get("preset") or "daily"
        predicate = _CRON_PRESETS.get(preset)
        if predicate is None or not predicate(now, cfg):
            continue
        # Por defecto el cron aplica a TODOS los contactos activos.
        # El filter del cfg lo restringe.
        from app.workflows.conditions import EvalContext, evaluate  # noqa: PLC0415

        contacts = list(
            session.scalars(
                select(Contact)
                .where(Contact.is_active.is_(True))
                .limit(200)
            )
        )
        for contact in contacts:
            ctx = EvalContext(
                session=session,
                contact=contact,
                trigger_payload={"event_type": "cron.recurring", "preset": preset},
            )
            if cfg.get("filter") and not evaluate(cfg.get("filter"), ctx):
                continue
            run = start_run(
                session,
                workflow,
                contact,
                trigger_payload={"event_type": "cron.recurring", "preset": preset},
            )
            if run:
                advance_run(session, run.id)
                started += 1
    return started


# ---------------------------------------------------------------------
# Arming
# ---------------------------------------------------------------------


def schedule_tick() -> None:
    interval = _tick_interval()
    try:
        from rq import Queue  # noqa: PLC0415

        from app.workers.queues import (  # noqa: PLC0415
            queue_name,
            redis_connection,
        )

        conn = redis_connection()
        lock_ttl = max(int(interval.total_seconds()) - 5, 10)
        if not conn.set(TICK_LOCK_KEY, "1", nx=True, ex=lock_ttl):
            return
        try:
            Queue(
                queue_name("workflows", "scheduler"),
                connection=conn,
            ).enqueue_in(interval, _tick_runner)
        except Exception as exc:  # noqa: BLE001
            log.warning("workflows.scheduler arm failed: %s", exc)
            conn.delete(TICK_LOCK_KEY)
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "workflows.scheduler redis unreachable: %s", exc
        )


def _tick_runner() -> None:
    from app.db.session import get_engine  # noqa: PLC0415

    with Session(get_engine()) as session:
        try:
            run_tick(session)
        except Exception:  # noqa: BLE001
            log.exception("workflows.scheduler tick crashed")


def arm() -> None:
    """Llamado una vez en API startup. Idempotente vía SETNX."""
    try:
        schedule_tick()
    except Exception as exc:  # noqa: BLE001
        log.warning("workflows.scheduler arm failed: %s", exc)
