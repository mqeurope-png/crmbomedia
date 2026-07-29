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

    # 2.5 Sprint Workflows E4 (parcial): runs atascados en CANCELLING -
    # cancel_run los marcaba y nada volvia a tocarlos, reteniendo el
    # dedup key para siempre.
    cancelling = list(
        session.scalars(
            select(WorkflowRun)
            .where(WorkflowRun.state == WorkflowRunState.CANCELLING)
            .limit(limit)
        )
    )
    for run in cancelling:
        try:
            advance_run(session, run.id)  # el guard CANCELLING finaliza
        except Exception:  # noqa: BLE001
            log.exception("workflows.scheduler cancelling failed run=%s", run.id)

    # 3. Workflows con trigger `cron.recurring` cuyo proximo tick vencio.
    try:
        cron_started = _evaluate_cron_workflows(session, now)
    except Exception:  # noqa: BLE001 - E8: no matar la cadena del tick
        log.exception("workflows.scheduler cron phase failed")
        session.rollback()
        cron_started = 0

    # 4. Sprint Workflows - trigger contact.matches_conditions:
    # transicion no-cumple -> cumple via diff de membresia.
    try:
        matches_started = _evaluate_matches_conditions(session, now)
    except Exception:  # noqa: BLE001
        log.exception("workflows.scheduler matches_conditions phase failed")
        session.rollback()
        matches_started = 0

    session.commit()

    return {
        "runs_resumed": resumed,
        "event_waits_timed_out": timed,
        "cron_started": cron_started,
        "matches_started": matches_started,
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
        # Sprint Workflows. Dedup de slot: el tick corre cada 30s y la
        # ventana `minute < 1` dura 60s -> sin esto cada slot disparaba
        # DOS veces. SETNX por (workflow, slot); Redis caido -> se
        # permite (best-effort, igual que antes).
        if not _claim_cron_slot(workflow.id, preset, now):
            continue
        from app.workflows.conditions import EvalContext, evaluate  # noqa: PLC0415

        # Iteracion COMPLETA por chunks (antes `.limit(200)` sin orden
        # truncaba el universo: contactos 201+ jamas entraban). Cap
        # explicito por workflow/tick con log - nada de silencios.
        per_tick_cap = 1000
        last_id = ""
        while started < per_tick_cap:
            chunk = list(
                session.scalars(
                    select(Contact)
                    .where(Contact.is_active.is_(True), Contact.id > last_id)
                    .order_by(Contact.id)
                    .limit(200)
                )
            )
            if not chunk:
                break
            for contact in chunk:
                last_id = contact.id
                ctx = EvalContext(
                    session=session,
                    contact=contact,
                    trigger_payload={
                        "event_type": "cron.recurring", "preset": preset,
                    },
                )
                if cfg.get("filter") and not evaluate(cfg.get("filter"), ctx):
                    continue
                run = start_run(
                    session,
                    workflow,
                    contact,
                    trigger_payload={
                        "event_type": "cron.recurring", "preset": preset,
                    },
                )
                if run:
                    advance_run(session, run.id)
                    started += 1
                if started >= per_tick_cap:
                    log.warning(
                        "workflows.cron cap %d alcanzado workflow=%s - "
                        "resto pospuesto", per_tick_cap, workflow.id,
                    )
                    break
    return started


_CRON_SLOT_FMT = {
    "hourly": "%Y%m%d%H",
    "daily": "%Y%m%d",
    "weekly_monday": "%G-W%V",
    "weekly_friday": "%G-W%V",
    "monthly_first_day": "%Y%m",
}


def _claim_cron_slot(workflow_id: str, preset: str, now: datetime) -> bool:
    try:
        from app.workers.queues import redis_connection  # noqa: PLC0415

        slot = now.strftime(_CRON_SLOT_FMT.get(preset, "%Y%m%d%H%M"))
        key = f"workflows:cron:{workflow_id}:{preset}:{slot}"
        return bool(
            redis_connection().set(key, "1", nx=True, ex=8 * 24 * 3600)
        )
    except Exception:  # noqa: BLE001 - Redis caido: comportamiento previo
        return True


def _evaluate_matches_conditions(session: Session, now: datetime) -> int:
    """Sprint Workflows - trigger custom `contact.matches_conditions`.

    Backbone de correccion por poll (30s): compila el filter del trigger
    a SQL (motor de segmentos), diffea contra la tabla de membresia y
    dispara SOLO en la transicion no-cumple -> cumple. La activacion del
    workflow siembra la baseline (ver /activate), asi que los contactos
    que ya cumplian al activar NO disparan. Re-entrada: si
    `allow_reentry`, la fila se borra al salir -> volver a cumplir
    re-dispara; si no, la fila persiste -> 1 disparo por contacto para
    siempre. Patron clonado de BrevoTargetMembership
    (sync_targets.py:104-204)."""
    from app.models.workflows import (  # noqa: PLC0415
        WorkflowTriggerMembership,
    )
    from app.services.segments.engine import (  # noqa: PLC0415
        SegmentRuleError,
        build_filter,
    )
    from app.workflows.dispatcher import (  # noqa: PLC0415
        _workflow_scopes_contact,
    )

    started = 0
    workflows = list(
        session.scalars(
            select(Workflow).where(
                Workflow.trigger_type == "contact.matches_conditions",
                Workflow.status == WorkflowStatus.ACTIVE,
            )
        )
    )
    for workflow in workflows:
        try:
            cfg = json.loads(workflow.trigger_config_json or "{}")
        except (TypeError, ValueError):
            cfg = {}
        tree = cfg.get("filter") or {}
        try:
            clause = build_filter(tree)
        except SegmentRuleError:
            log.warning(
                "workflows.matches_conditions filtro invalido workflow=%s",
                workflow.id,
            )
            continue
        current_ids = set(
            session.scalars(
                select(Contact.id).where(Contact.is_active.is_(True), clause)
            )
        )
        rows = {
            m.contact_id: m
            for m in session.scalars(
                select(WorkflowTriggerMembership).where(
                    WorkflowTriggerMembership.workflow_id == workflow.id
                )
            )
        }
        entered = current_ids - rows.keys()
        departed = set(rows) - current_ids
        for contact_id in sorted(entered):
            contact = session.get(Contact, contact_id)
            if contact is None:
                continue
            session.add(
                WorkflowTriggerMembership(
                    workflow_id=workflow.id,
                    contact_id=contact_id,
                    first_matched_at=now,
                    last_seen_at=now,
                )
            )
            if not _workflow_scopes_contact(session, workflow, contact):
                continue
            run = start_run(
                session,
                workflow,
                contact,
                trigger_payload={
                    "event_type": "contact.matches_conditions",
                },
            )
            if run:
                advance_run(session, run.id)
                started += 1
        for contact_id in departed:
            if workflow.allow_reentry:
                session.delete(rows[contact_id])
            else:
                rows[contact_id].last_seen_at = now
        for contact_id in current_ids & rows.keys():
            rows[contact_id].last_seen_at = now
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
    """RQ entry point. Sprint Workflows E8: el re-arm vive en un
    `finally` - antes una excepcion en el tick mataba la cadena
    self-rescheduling hasta el siguiente reinicio del API."""
    try:
        from app.db.session import get_engine  # noqa: PLC0415

        with Session(get_engine()) as session:
            run_tick(session)
    except Exception:  # noqa: BLE001
        log.exception("workflows.scheduler tick failed")
    finally:
        schedule_tick()


def arm() -> None:
    """Llamado una vez en API startup. Idempotente vía SETNX."""
    try:
        schedule_tick()
    except Exception as exc:  # noqa: BLE001
        log.warning("workflows.scheduler arm failed: %s", exc)
