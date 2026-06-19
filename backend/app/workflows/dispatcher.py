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
    inline."""
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
        queue.enqueue(
            "app.workflows.dispatcher._process_event_job",
            event_type,
            contact_id,
            payload,
        )
    except Exception:  # noqa: BLE001
        log.warning(
            "workflows.dispatch enqueue failed; processing inline",
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


def _trigger_matches(
    workflow: Workflow,
    trigger_cfg: dict[str, Any],
    contact: Contact,
    payload: dict[str, Any],
    session: Session,
) -> bool:
    """Aplica el filter del trigger (condición opcional sobre el
    contacto + criterio específico del trigger en payload)."""
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

    # Triggers específicos pueden definir reglas extra. Ej:
    #   trigger_type=contact.updated + trigger_cfg={"field": "lead_score"}
    #   significa "solo dispara si el campo modificado es lead_score".
    required_field = trigger_cfg.get("field")
    if required_field:
        payload_field = payload.get("field")
        if payload_field != required_field:
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


TRIGGER_CATALOG: list[dict[str, Any]] = [
    {"type": "contact.created", "label": "Contacto creado"},
    {"type": "contact.updated", "label": "Contacto actualizado"},
    {
        "type": "contact.lifecycle_changed",
        "label": "Contacto cambia de estado del ciclo",
    },
    {"type": "contact.unsubscribed", "label": "Contacto se da de baja"},
    {"type": "email.crm.opened", "label": "Email del CRM abierto"},
    {"type": "email.crm.clicked", "label": "Link de email CRM cliqueado"},
    {"type": "email.crm.replied", "label": "Email del CRM respondido"},
    {"type": "email.brevo.opened", "label": "Email campaña Brevo abierto"},
    {"type": "email.brevo.clicked", "label": "Link campaña Brevo cliqueado"},
    {
        "type": "engagement.brevo.composed",
        "label": "Engagement Brevo compuesto (N aperturas + N clicks en X días)",
    },
    {"type": "task.created", "label": "Tarea creada"},
    {"type": "task.completed", "label": "Tarea completada"},
    {"type": "task.overdue", "label": "Tarea vencida"},
    {"type": "opportunity.created", "label": "Oportunidad creada"},
    {"type": "opportunity.stage_changed", "label": "Oportunidad cambia de stage"},
    {"type": "opportunity.won", "label": "Oportunidad ganada"},
    {"type": "opportunity.lost", "label": "Oportunidad perdida"},
    {"type": "contact.date_field", "label": "Fecha del contacto (cumpleaños, aniversario...)"},
    # PR-Fixes-Pase-2 Bug D: el dropdown frontend leía este label
    # literalmente. "Recurrente (preset)" no es jerga que un comercial
    # entienda; "Horario fijo" sí.
    {"type": "cron.recurring", "label": "Horario fijo"},
]
