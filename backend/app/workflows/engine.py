"""State machine que avanza un `WorkflowRun` paso a paso.

API pública:

- `start_run(session, workflow, contact, trigger_payload)` — crea el
  run, encola el primer step. Devuelve el run o None si el reentry
  guard bloqueó la entrada.
- `advance_run(session, run_id)` — empuja un run un paso adelante.
  Llamada por el worker RQ y por el scheduler. Idempotente: si el run
  está en estado terminal o cancelado, no hace nada.
- `cancel_run(session, run_id, reason)` — marca el run como
  `cancelling`; el próximo step boundary lo cierra.

Cada step type tiene su handler en `app.workflows.steps`. El handler
devuelve un `StepResult` con el siguiente step y opcionalmente un
`wake_at` (para waits) o un evento esperado (para waits-on-event).
"""
from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.crm import Contact
from app.models.workflows import (
    Workflow,
    WorkflowEdge,
    WorkflowEventWait,
    WorkflowExitKind,
    WorkflowRun,
    WorkflowRunHistory,
    WorkflowRunState,
    WorkflowStatus,
    WorkflowStep,
)

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------
# Step result contract
# ---------------------------------------------------------------------


@dataclass
class StepResult:
    """Lo que un handler devuelve al engine.

    - `next_step_id` — `None` significa "terminar el run" (con
      `exit_kind` decidido por el step exit).
    - `branch_label` — para nodos con múltiples salidas
      (condition: "true"/"false", switch: "case_X").
    - `wake_at` — si está seteado, el run pasa a `waiting`.
    - `wait_for_event` — si está seteado, el run pasa a
      `waiting_for_event`.
    - `status` — `"ok"` / `"skipped"` / `"deferred"` para el historial.
    - `result` — payload JSON para el historial (subject del email
      enviado, tag añadido, etc.).
    - `exit_kind` — solo en exit steps.
    """

    next_step_id: str | None = None
    branch_label: str | None = None
    wake_at: datetime | None = None
    wait_for_event: dict[str, Any] | None = None
    status: str = "ok"
    result: dict[str, Any] | None = None
    error: str | None = None
    exit_kind: WorkflowExitKind | None = None


# ---------------------------------------------------------------------
# Step registry — handlers se registran via decorator @register_step.
# ---------------------------------------------------------------------


_STEP_HANDLERS: dict[
    str,
    callable[[Session, WorkflowRun, WorkflowStep, Contact], StepResult],
] = {}


def register_step(step_type: str):
    """Decorador para registrar handlers de step type."""

    def decorator(fn):
        _STEP_HANDLERS[step_type] = fn
        return fn

    return decorator


def get_step_handler(step_type: str):
    return _STEP_HANDLERS.get(step_type)


def registered_step_types() -> list[str]:
    return sorted(_STEP_HANDLERS.keys())


# ---------------------------------------------------------------------
# Helpers de grafo
# ---------------------------------------------------------------------


def _entry_step(session: Session, workflow: Workflow) -> WorkflowStep | None:
    return session.scalar(
        select(WorkflowStep).where(
            WorkflowStep.workflow_id == workflow.id,
            WorkflowStep.is_entry.is_(True),
        )
    )


def next_step_for_edge(
    session: Session,
    *,
    from_step_id: str,
    branch_label: str,
) -> str | None:
    """Sigue la arista correspondiente al branch_label. Si no encuentra
    arista específica, prueba `default`. Si tampoco, devuelve None
    (terminación natural)."""
    edge = session.scalar(
        select(WorkflowEdge).where(
            WorkflowEdge.from_step_id == from_step_id,
            WorkflowEdge.branch_label == branch_label,
        )
    )
    if edge is not None:
        return edge.to_step_id
    if branch_label != "default":
        edge = session.scalar(
            select(WorkflowEdge).where(
                WorkflowEdge.from_step_id == from_step_id,
                WorkflowEdge.branch_label == "default",
            )
        )
        if edge is not None:
            return edge.to_step_id
    return None


# ---------------------------------------------------------------------
# Reentry guard
# ---------------------------------------------------------------------


def _build_dedup_key(
    workflow: Workflow, contact_id: str, run_id: str
) -> str:
    """Cuando reentry NO está permitido, usamos `workflow:contact`
    así dos eventos concurrentes del mismo contacto se bloquean
    mutuamente. Cuando SÍ está permitido, incluimos el run_id para
    que cada entrada tenga su slot único."""
    if workflow.allow_reentry:
        return f"{workflow.id}:{contact_id}:{run_id}"
    return f"{workflow.id}:{contact_id}"


def _archived_dedup_key(run_id: str) -> str:
    return f"archived:{run_id}"


# ---------------------------------------------------------------------
# Start
# ---------------------------------------------------------------------


def start_run(
    session: Session,
    workflow: Workflow,
    contact: Contact,
    *,
    trigger_payload: dict[str, Any] | None = None,
    skip_dedup: bool = False,
) -> WorkflowRun | None:
    """Crea un run y lo deja listo para que el executor lo avance.
    Devuelve None si el workflow no tiene entry step o si el dedup
    guard bloqueó la entrada.

    `skip_dedup=True` solo cuando un admin lo añade manualmente desde
    la ficha del contacto — la decisión expresa que la entrada forzada
    salta el cap de reentry."""
    if workflow.status != WorkflowStatus.ACTIVE:
        log.info(
            "workflows.engine ignoring start: workflow %s status=%s",
            workflow.id,
            workflow.status,
        )
        return None

    entry = _entry_step(session, workflow)
    if entry is None:
        log.warning(
            "workflows.engine no entry step for workflow %s", workflow.id
        )
        return None

    from uuid import uuid4  # noqa: PLC0415

    run_id = str(uuid4())
    dedup_key = (
        f"{workflow.id}:{contact.id}:{run_id}"
        if skip_dedup
        else _build_dedup_key(workflow, contact.id, run_id)
    )

    now = datetime.now(UTC)
    run = WorkflowRun(
        id=run_id,
        workflow_id=workflow.id,
        contact_id=contact.id,
        current_step_id=entry.id,
        state=WorkflowRunState.RUNNING,
        active_dedup_key=dedup_key,
        trigger_payload_json=json.dumps(trigger_payload or {}, default=str),
        started_at=now,
        wake_at=now,
    )
    session.add(run)
    # Capturamos ids antes del flush porque tras una rollback (camino
    # del dedup-block) acceder a `workflow.id` reabriría la conexión
    # con el objeto expirado y volvería a fallar.
    workflow_id_str = workflow.id
    contact_id_str = contact.id
    try:
        session.flush()
    except Exception as exc:  # noqa: BLE001 - unique constraint
        log.info(
            "workflows.engine dedup blocked workflow=%s contact=%s: %s",
            workflow_id_str,
            contact_id_str,
            exc,
        )
        session.rollback()
        return None

    workflow.total_entered = (workflow.total_entered or 0) + 1
    session.flush()
    return run


# ---------------------------------------------------------------------
# Advance
# ---------------------------------------------------------------------


def advance_run(session: Session, run_id: str, *, max_steps: int = 30) -> None:
    """Avanza el run tantos steps consecutivos como pueda (hasta wait /
    exit / cap). El cap previene loops accidentales en condiciones."""
    steps_executed = 0
    while steps_executed < max_steps:
        run = session.get(WorkflowRun, run_id)
        if run is None:
            log.warning("workflows.engine advance: run %s gone", run_id)
            return
        if run.state in (
            WorkflowRunState.COMPLETED,
            WorkflowRunState.CANCELLED,
            WorkflowRunState.FAILED,
        ):
            return
        if run.state == WorkflowRunState.CANCELLING:
            _finalize(session, run, WorkflowRunState.CANCELLED, exit_kind=None)
            return
        if run.state == WorkflowRunState.WAITING_FOR_EVENT:
            return
        if (
            run.state == WorkflowRunState.WAITING
            and run.wake_at
            and run.wake_at > datetime.now(UTC)
        ):
            return

        if run.current_step_id is None:
            _finalize(
                session, run, WorkflowRunState.COMPLETED, exit_kind=WorkflowExitKind.NATURAL
            )
            return

        step = session.get(WorkflowStep, run.current_step_id)
        if step is None:
            log.warning(
                "workflows.engine advance: step %s gone",
                run.current_step_id,
            )
            _finalize(
                session, run, WorkflowRunState.FAILED, error="step_missing"
            )
            return

        contact = session.get(Contact, run.contact_id)
        if contact is None:
            _finalize(
                session,
                run,
                WorkflowRunState.FAILED,
                error="contact_missing",
            )
            return

        # PR-Fix-Engine-Trigger-Step. El step `trigger` es el nodo raíz
        # del grafo: representa el EVENTO que dispara el workflow, NO
        # una acción ejecutable. Lo tratamos como anchor — avanzamos
        # directo al sucesor por su única salida `default` sin invocar
        # ningún handler. Esto es robusto incluso si el registro del
        # handler `trigger` fallase (caso histórico: el worker RQ no
        # importaba `app.workflows.steps` y `_STEP_HANDLERS` quedaba
        # vacío, así que el primer `advance_run` con el trigger marcaba
        # FAILED con "unknown step type: trigger").
        if step.type == "trigger":
            next_id = next_step_for_edge(
                session, from_step_id=step.id, branch_label="default"
            )
            _record_history(
                session, run, step, status="ok",
                result={"anchor": True},
            )
            if next_id is None:
                # Workflow con solo el trigger sin sucesor — completado
                # inmediatamente. El validador del activate ya lo
                # rechaza, pero por defensividad.
                log.info(
                    "workflows.engine workflow_empty run=%s", run.id
                )
                _finalize(
                    session, run, WorkflowRunState.COMPLETED,
                    exit_kind=WorkflowExitKind.NATURAL,
                    error="workflow_empty",
                )
                return
            run.current_step_id = next_id
            session.flush()
            steps_executed += 1
            continue

        # Email cap defer: el handler send_email puede devolver
        # `status="deferred"` con `wake_at = mañana 00:01`. Lo
        # respetamos sin avanzar.
        handler = get_step_handler(step.type)
        if handler is None:
            log.warning(
                "workflows.engine unknown step type: %s", step.type
            )
            _record_history(
                session,
                run,
                step,
                status="failed",
                error=f"unknown step type: {step.type}",
            )
            _finalize(
                session,
                run,
                WorkflowRunState.FAILED,
                error=f"unknown_step_type:{step.type}",
            )
            return

        try:
            result = handler(session, run, step, contact)
        except Exception as exc:  # noqa: BLE001
            log.exception(
                "workflows.engine handler crashed step=%s type=%s",
                step.id,
                step.type,
            )
            _record_history(
                session,
                run,
                step,
                status="failed",
                error=str(exc)[:500],
            )
            _finalize(
                session,
                run,
                WorkflowRunState.FAILED,
                error=str(exc)[:500],
            )
            return

        _record_history(
            session,
            run,
            step,
            status=result.status,
            result=result.result,
            error=result.error,
        )

        # Deferred = no avanzamos. El siguiente despertador del
        # scheduler reintentará el mismo step.
        if result.status == "deferred":
            run.wake_at = result.wake_at or datetime.now(UTC)
            run.state = WorkflowRunState.WAITING
            session.flush()
            return

        # Exit explícito.
        if result.exit_kind is not None:
            _finalize(
                session,
                run,
                WorkflowRunState.COMPLETED,
                exit_kind=result.exit_kind,
            )
            return

        # Wait-time.
        if result.wake_at is not None:
            next_id = next_step_for_edge(
                session,
                from_step_id=step.id,
                branch_label=result.branch_label or "default",
            )
            run.current_step_id = next_id
            run.wake_at = result.wake_at
            run.state = WorkflowRunState.WAITING
            session.flush()
            return

        # Wait-for-event.
        if result.wait_for_event is not None:
            next_id = next_step_for_edge(
                session,
                from_step_id=step.id,
                branch_label="matched",
            )
            timeout_at = result.wait_for_event.get("timeout_at")
            wait_row = WorkflowEventWait(
                run_id=run.id,
                workflow_id=run.workflow_id,
                contact_id=run.contact_id,
                step_id=step.id,
                event_type=result.wait_for_event["event_type"],
                condition_json=json.dumps(
                    result.wait_for_event.get("condition") or {},
                    default=str,
                ),
                timeout_at=timeout_at or datetime.now(UTC),
            )
            session.add(wait_row)
            run.state = WorkflowRunState.WAITING_FOR_EVENT
            run.current_step_id = next_id or step.id
            # El scheduler también recoge timeouts via wake_at.
            run.wake_at = timeout_at
            session.flush()
            return

        # Avance normal: el handler ya dejó el resultado, ahora
        # buscamos el siguiente step.
        next_id = next_step_for_edge(
            session,
            from_step_id=step.id,
            branch_label=result.branch_label or "default",
        )
        run.current_step_id = next_id
        if next_id is None:
            _finalize(
                session,
                run,
                WorkflowRunState.COMPLETED,
                exit_kind=WorkflowExitKind.NATURAL,
            )
            return
        steps_executed += 1
        session.flush()


# ---------------------------------------------------------------------
# Cancel / finalize
# ---------------------------------------------------------------------


def cancel_run(
    session: Session,
    run_id: str,
    *,
    reason: str | None = None,
) -> None:
    """Marca el run para cancelación. El próximo step boundary lo
    transiciona limpio."""
    run = session.get(WorkflowRun, run_id)
    if run is None:
        return
    if run.state in (
        WorkflowRunState.COMPLETED,
        WorkflowRunState.CANCELLED,
        WorkflowRunState.FAILED,
    ):
        return
    if run.state == WorkflowRunState.RUNNING:
        # Si está en RUNNING significa que está siendo ejecutado AHORA
        # mismo. Lo marcamos cancelling para que el próximo step
        # boundary lo limpie.
        run.state = WorkflowRunState.CANCELLING
        session.flush()
        return
    # Si está esperando, podemos cerrarlo directo — no hay step en flight.
    _finalize(session, run, WorkflowRunState.CANCELLED, error=reason)


def cancel_for_contact(
    session: Session,
    contact_id: str,
    *,
    event_type: str,
    reason: str | None = None,
) -> int:
    """Cancela todos los runs activos del contacto cuyo workflow
    declara `event_type` como cancelante. Llamado por el dispatcher
    cuando llega ese evento."""
    runs = list(
        session.scalars(
            select(WorkflowRun).where(
                WorkflowRun.contact_id == contact_id,
                WorkflowRun.state.in_(
                    [
                        WorkflowRunState.RUNNING,
                        WorkflowRunState.WAITING,
                        WorkflowRunState.WAITING_FOR_EVENT,
                    ]
                ),
            )
        )
    )
    cancelled = 0
    for run in runs:
        wf = session.get(Workflow, run.workflow_id)
        if wf is None:
            continue
        try:
            cancellation_events = set(
                json.loads(wf.cancellation_events_json or "[]")
            )
        except (TypeError, ValueError):
            cancellation_events = set()
        if event_type not in cancellation_events:
            continue
        cancel_run(session, run.id, reason=reason or event_type)
        cancelled += 1
    return cancelled


def _finalize(
    session: Session,
    run: WorkflowRun,
    state: WorkflowRunState,
    *,
    exit_kind: WorkflowExitKind | None = None,
    error: str | None = None,
) -> None:
    run.state = state
    run.exit_kind = exit_kind
    run.completed_at = datetime.now(UTC)
    run.wake_at = None
    run.active_dedup_key = _archived_dedup_key(run.id)
    if error:
        run.error_summary = error[:1000]
    workflow = session.get(Workflow, run.workflow_id)
    if workflow is not None:
        if state == WorkflowRunState.COMPLETED:
            workflow.total_completed = (workflow.total_completed or 0) + 1
            if exit_kind == WorkflowExitKind.WON:
                workflow.total_won = (workflow.total_won or 0) + 1
        elif state == WorkflowRunState.CANCELLED:
            workflow.total_cancelled = (workflow.total_cancelled or 0) + 1
        elif state == WorkflowRunState.FAILED:
            workflow.total_failed = (workflow.total_failed or 0) + 1
    # También limpia el event_wait pendiente si lo había.
    session.execute(
        WorkflowEventWait.__table__.delete().where(
            WorkflowEventWait.run_id == run.id
        )
    )
    session.flush()


# ---------------------------------------------------------------------
# History
# ---------------------------------------------------------------------


def _record_history(
    session: Session,
    run: WorkflowRun,
    step: WorkflowStep,
    *,
    status: str,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    row = WorkflowRunHistory(
        run_id=run.id,
        workflow_id=run.workflow_id,
        contact_id=run.contact_id,
        step_id=step.id,
        step_type=step.type,
        status=status,
        result_json=json.dumps(result, default=str) if result else None,
        error_summary=(error or None) and error[:500],
        executed_at=datetime.now(UTC),
    )
    session.add(row)
    session.flush()


# ---------------------------------------------------------------------
# Resume helper (llamado por el dispatcher cuando un evento desencadena
# un wait-for-event matching).
# ---------------------------------------------------------------------


def resume_run_from_event_wait(
    session: Session, wait_id: str, *, matched: bool = True
) -> None:
    """Borra el event_wait y avanza el run. Si `matched=False` se asume
    que es por timeout y se sigue la rama de timeout."""
    wait = session.get(WorkflowEventWait, wait_id)
    if wait is None:
        return
    run = session.get(WorkflowRun, wait.run_id)
    if run is None:
        session.delete(wait)
        return
    if run.state != WorkflowRunState.WAITING_FOR_EVENT:
        session.delete(wait)
        return
    branch = "matched" if matched else "timeout"
    next_id = next_step_for_edge(
        session,
        from_step_id=wait.step_id,
        branch_label=branch,
    )
    run.current_step_id = next_id
    run.state = WorkflowRunState.RUNNING
    run.wake_at = datetime.now(UTC)
    session.delete(wait)
    session.flush()
    advance_run(session, run.id)


def find_matching_event_waits(
    session: Session,
    *,
    event_type: str,
    contact_id: str,
) -> Iterable[WorkflowEventWait]:
    """Devuelve los `WorkflowEventWait` de este contacto que esperan
    `event_type`. El dispatcher los aplica uno a uno (con resume)."""
    now = datetime.now(UTC)
    return list(
        session.scalars(
            select(WorkflowEventWait).where(
                WorkflowEventWait.event_type == event_type,
                WorkflowEventWait.contact_id == contact_id,
                WorkflowEventWait.timeout_at > now,
            )
        )
    )
