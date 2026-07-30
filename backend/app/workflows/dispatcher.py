"""Dispatcher de eventos del CRM a workflows matching.

Decisión arquitectónica: los endpoints que producen eventos llaman
**explícitamente** a `dispatch_event(...)`. NO usamos SQLAlchemy
listeners — un import masivo AgileCRM no debe disparar 5000
workflows. Cada hook está en su sitio y se ve en el diff.

API:

- `dispatch_event(session, event_type, contact_id, payload)` —
  entry point. Encola la evaluación a un worker RQ (o ejecuta inline
  si Redis caído).
- `process_event_inline(session, event_type, contact_id, payload)` —
  la versión síncrona. Llama a triggers + cancellations + resume de
  event_waits.

Tipos de evento canónicos (matching `Workflow.trigger_type` + usados
por `wait_for_event`):

- `contact.created`
- `contact.updated`
- `contact.lifecycle_changed`
- `contact.unsubscribed`
- `email.crm.opened`, `email.crm.clicked`, `email.crm.replied`
- `email.brevo.opened`, `email.brevo.clicked`
- `engagement.brevo.composed`
- `task.created`, `task.completed`, `task.overdue`
- `opportunity.created`, `opportunity.stage_changed`,
  `opportunity.won`, `opportunity.lost`
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.crm import Contact
from app.models.workflows import Workflow, WorkflowStatus
from app.workflows import conditions

# PR-Fix-Engine-Trigger-Step. Import side-effect: el decorador
# `@register_step` de `app.workflows.steps` rellena el `_STEP_HANDLERS`
# del motor. La API process lo importa en `app/main.py`, pero el RQ
# worker entra por `app.workflows.dispatcher._process_event_job` y
# necesita asegurarse de que los handlers están registrados al
# resolverse este módulo. Sin esto, el primer `advance_run` que
# alcance un step type cualquiera (empezando por `trigger`) loguea
# "unknown step type" y marca el run en FAILED.
from app.workflows import steps as _wf_steps  # noqa: F401
from app.workflows.engine import (
    cancel_for_contact,
    find_matching_event_waits,
    resume_run_from_event_wait,
    start_run,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------


def dispatch_event(
    session: Session,
    event_type: str,
    contact_id: str,
    payload: dict[str, Any] | None = None,
) -> None:
    """Encola la evaluación. Fire-and-forget. Si Redis cae, procesa
    inline.

    PR-Consolidado — Fix dispatcher sync. Pasamos el callable
    `_process_event_job` directo en vez del string
    `"app.workflows.dispatcher._process_event_job"`. Con el string,
    RQ resolvía el import en el worker; si el import fallaba
    (Python path diferente entre API y worker, o un side-effect que
    se rompió silenciosamente), el job quedaba en `FailedJobRegistry`
    sin ejecutarse y NO se incrementaba `workflows.total_entered`.
    Pasar el callable hace que RQ pickle la función directamente —
    si el worker no puede deserializar es porque tiene código viejo,
    error mucho más visible.

    Loguear AMBOS extremos (encolado + fallback inline) para que el
    operador pueda diferenciar "Redis caído" vs "Redis OK pero la
    función nunca corre".
    """
    payload = payload or {}
    try:
        from rq import Queue  # noqa: PLC0415

        from app.workers.queues import (  # noqa: PLC0415
            queue_name,
            redis_connection,
        )

        queue = Queue(
            queue_name("workflows", "dispatch"),
            connection=redis_connection(),
        )
        # Callable reference + INFO log: visibles en el log del API /
        # worker que originó el dispatch.
        queue.enqueue(
            _process_event_job,
            event_type,
            contact_id,
            payload,
        )
        log.info(
            "workflows.dispatch enqueued event_type=%s contact_id=%s",
            event_type,
            contact_id,
        )
    except Exception:  # noqa: BLE001
        log.warning(
            "workflows.dispatch enqueue failed; processing inline "
            "event_type=%s contact_id=%s",
            event_type,
            contact_id,
            exc_info=True,
        )
        process_event_inline(
            session, event_type, contact_id, payload
        )


def _process_event_job(
    event_type: str, contact_id: str, payload: dict[str, Any]
) -> None:
    """RQ entry point — opens its own session."""
    from sqlalchemy.orm import Session as _Session  # noqa: PLC0415

    from app.db.session import get_engine  # noqa: PLC0415

    with _Session(get_engine()) as session:
        process_event_inline(session, event_type, contact_id, payload)
        session.commit()


# ---------------------------------------------------------------------
# Inline processing
# ---------------------------------------------------------------------


def process_event_inline(
    session: Session,
    event_type: str,
    contact_id: str,
    payload: dict[str, Any],
) -> None:
    """Realiza 3 cosas en orden:

    1. Cancela runs activos del contacto cuyo workflow declara
       `event_type` como cancelante.
    2. Resume runs en `waiting_for_event` cuyo `event_type` matchea.
    3. Inicia runs en workflows ACTIVE con `trigger_type == event_type`
       cuyas condiciones de trigger pasan.
    """
    contact = session.get(Contact, contact_id)
    if contact is None:
        log.info("workflows.dispatch contact %s missing", contact_id)
        return

    # 1. Cancellation rules.
    cancel_for_contact(
        session,
        contact_id,
        event_type=event_type,
        reason=event_type,
    )

    # 2. Resume waiting_for_event runs.
    for wait in find_matching_event_waits(
        session,
        event_type=event_type,
        contact_id=contact_id,
    ):
        try:
            condition = (
                json.loads(wait.condition_json or "{}")
                if wait.condition_json
                else {}
            )
        except (TypeError, ValueError):
            condition = {}
        ctx = conditions.EvalContext(
            session=session,
            contact=contact,
            trigger_payload=payload,
        )
        if conditions.evaluate(condition, ctx):
            resume_run_from_event_wait(session, wait.id, matched=True)

    # 3. Start new runs in workflows whose trigger matches.
    workflows = list(
        session.scalars(
            select(Workflow).where(
                Workflow.trigger_type == event_type,
                Workflow.status == WorkflowStatus.ACTIVE,
            )
        )
    )
    # PR-Hotfix-Notas-Workflows Item B. Log de diagnóstico: cuántos
    # workflows activos matchean este event_type. Útil para depurar
    # futuros "el trigger no dispara" (0 matched → nombre/estado mal).
    log.info(
        "workflows.dispatch event_type=%s contact_id=%s matched_workflows=%d",
        event_type,
        contact_id,
        len(workflows),
    )
    for workflow in workflows:
        try:
            trigger_cfg = json.loads(workflow.trigger_config_json or "{}")
        except (TypeError, ValueError):
            trigger_cfg = {}
        if not _trigger_matches(workflow, trigger_cfg, contact, payload, session):
            continue
        run = start_run(
            session,
            workflow,
            contact,
            trigger_payload={"event_type": event_type, **payload},
        )
        if run is None:
            continue
        # Inline el primer step para que las acciones inmediatas se
        # vean reflejadas sin esperar al scheduler.
        from app.workflows.engine import advance_run  # noqa: PLC0415

        advance_run(session, run.id)


def _workflow_scopes_contact(
    session: Session, workflow: Workflow, contact: Contact
) -> bool:
    """Sprint Workflows — tenancy. Un workflow PRIVADO (owner_user_id no
    NULL) solo dispara para contactos asignados a su owner (cache
    `owner_user_id` o cualquier fila de `contact_assignments`). Los
    globales (NULL) disparan para todos, como hasta ahora."""
    owner_id = getattr(workflow, "owner_user_id", None)
    if not owner_id:
        return True
    if contact.owner_user_id == owner_id:
        return True
    from app.models.crm import ContactAssignment  # noqa: PLC0415

    assigned = session.scalar(
        select(ContactAssignment.id).where(
            ContactAssignment.contact_id == contact.id,
            ContactAssignment.user_id == owner_id,
        ).limit(1)
    )
    return assigned is not None


def _trigger_matches(
    workflow: Workflow,
    trigger_cfg: dict[str, Any],
    contact: Contact,
    payload: dict[str, Any],
    session: Session,
) -> bool:
    """Aplica: (1) tenancy, (2) los filtros de config específicos del
    trigger (campaña/link/priority/... — definición canónica en
    `trigger_definitions`, la MISMA que usa el estimator), (3) el filter
    tree opcional sobre el contacto."""
    if not _workflow_scopes_contact(session, workflow, contact):
        return False

    from app.workflows import trigger_definitions  # noqa: PLC0415

    if not trigger_definitions.config_matches(
        workflow.trigger_type, trigger_cfg, payload
    ):
        return False

    # Filtro general por condición sobre el contacto.
    filter_tree = trigger_cfg.get("filter")
    if filter_tree:
        ctx = conditions.EvalContext(
            session=session,
            contact=contact,
            trigger_payload=payload,
        )
        if not conditions.evaluate(filter_tree, ctx):
            return False

    # Compat legacy: `field` a nivel raíz del config (contact.updated).
    # La versión canónica vive en trigger_definitions._match_contact_updated;
    # este guard se mantiene para configs antiguos con `field` en triggers
    # sin matcher propio.
    required_field = trigger_cfg.get("field")
    if required_field and workflow.trigger_type not in (
        "contact.updated", "contact.date_field"
    ):
        changed = payload.get("changed_fields") or []
        if payload.get("field") != required_field and required_field not in changed:
            return False

    return True


# ---------------------------------------------------------------------
# Engagement compuesto Brevo — N aperturas/clicks en ventana
# ---------------------------------------------------------------------


def evaluate_brevo_engagement(
    session: Session,
    contact_id: str,
) -> None:
    """Llamado tras cada email.brevo.opened/clicked. Para cada workflow
    con trigger `engagement.brevo.composed`, evalúa si el contacto
    cumple `{min_opens, min_clicks, window_days}` y dispara si sí."""
    contact = session.get(Contact, contact_id)
    if contact is None:
        return
    workflows = list(
        session.scalars(
            select(Workflow).where(
                Workflow.trigger_type == "engagement.brevo.composed",
                Workflow.status == WorkflowStatus.ACTIVE,
            )
        )
    )
    if not workflows:
        return

    from app.models.crm import ActivityEvent  # noqa: PLC0415

    now = datetime.now(UTC)
    for workflow in workflows:
        try:
            cfg = json.loads(workflow.trigger_config_json or "{}")
        except (TypeError, ValueError):
            cfg = {}
        window_days = int(cfg.get("window_days") or 7)
        min_opens = int(cfg.get("min_opens") or 0)
        min_clicks = int(cfg.get("min_clicks") or 0)
        cutoff = now - timedelta(days=window_days)
        opens = int(
            session.scalar(
                select(__import__("sqlalchemy").func.count(ActivityEvent.id))
                .where(
                    ActivityEvent.contact_id == contact.id,
                    ActivityEvent.event_type == "email.opened",
                    ActivityEvent.occurred_at >= cutoff,
                )
            )
            or 0
        )
        clicks = int(
            session.scalar(
                select(__import__("sqlalchemy").func.count(ActivityEvent.id))
                .where(
                    ActivityEvent.contact_id == contact.id,
                    ActivityEvent.event_type == "email.clicked",
                    ActivityEvent.occurred_at >= cutoff,
                )
            )
            or 0
        )
        if opens < min_opens or clicks < min_clicks:
            continue
        # Sprint Workflows. (a) El filter del operador ahora SÍ aplica —
        # antes este evaluador saltaba `_trigger_matches` y disparaba
        # para contactos excluidos. (b) Dedup: 1 disparo por contacto y
        # ventana — sin esto re-disparaba en cada open/click.
        payload = {"opens": opens, "clicks": clicks, "window_days": window_days}
        if not _trigger_matches(workflow, cfg, contact, payload, session):
            continue
        from app.models.workflows import WorkflowRun  # noqa: PLC0415

        already = session.scalar(
            select(WorkflowRun.id).where(
                WorkflowRun.workflow_id == workflow.id,
                WorkflowRun.contact_id == contact.id,
                WorkflowRun.started_at >= cutoff,
            ).limit(1)
        )
        if already is not None:
            continue
        run = start_run(
            session,
            workflow,
            contact,
            trigger_payload={
                "event_type": "engagement.brevo.composed",
                "opens": opens,
                "clicks": clicks,
                "window_days": window_days,
            },
        )
        if run is not None:
            from app.workflows.engine import advance_run  # noqa: PLC0415

            advance_run(session, run.id)


# ---------------------------------------------------------------------
# Trigger catalog para el frontend
# ---------------------------------------------------------------------


# Sprint Workflows. El catálogo se genera desde la definición canónica
# (trigger_definitions) — incluye `kind`, `available` y
# `unavailable_reason` para que el wizard deshabilite los triggers sin
# productor en vez de ofrecerlos como si funcionaran.
def _build_trigger_catalog() -> list[dict[str, Any]]:
    from app.workflows.trigger_definitions import catalog  # noqa: PLC0415

    return catalog()


TRIGGER_CATALOG: list[dict[str, Any]] = _build_trigger_catalog()
