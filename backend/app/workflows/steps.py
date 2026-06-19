"""Handlers para cada `step.type`. Registrados vía
`@register_step("type")` en `app.workflows.engine`.

Cada handler:
  - Lee `step.config_json` (dict ya cargado por el caller).
  - Ejecuta el efecto secundario (envío email, mutate contact, etc.).
  - Devuelve un `StepResult` con `next_step_id` implícito (el engine
    sigue la arista) o `wake_at` para waits.

Lo que NO hace un handler:
  - Decidir el siguiente step de la cadena (lo hace el engine
    siguiendo `WorkflowEdge.branch_label`).
  - Commitear (el engine es el dueño de la transacción).
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from app.models.crm import (
    ContactPipelineStage,
    EmailDirection,
    EmailMessage,
    Task,
    TaskPriority,
    TaskStatus,
    User,
)
from app.models.workflows import (
    WorkflowExitKind,
    WorkflowRun,
    WorkflowStep,
)
from app.workflows import conditions, variables
from app.workflows.engine import StepResult, register_step

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------


def _config(step: WorkflowStep) -> dict[str, Any]:
    try:
        return json.loads(step.config_json or "{}")
    except (TypeError, ValueError):
        return {}


def _trigger_payload(run: WorkflowRun) -> dict[str, Any]:
    try:
        return json.loads(run.trigger_payload_json or "{}")
    except (TypeError, ValueError):
        return {}


# ---------------------------------------------------------------------
# Trigger (entry node — no effect)
# ---------------------------------------------------------------------


@register_step("trigger")
def _step_trigger(session, run, step, contact) -> StepResult:
    """Nodo raíz. No tiene efecto — solo redirige al siguiente."""
    _ = session, run, step, contact
    return StepResult(status="ok", result={"trigger_passed": True})


# ---------------------------------------------------------------------
# Waits
# ---------------------------------------------------------------------


@register_step("wait_time")
def _step_wait_time(session, run, step, contact) -> StepResult:
    """Espera una duración fija. Config:
      {"duration_minutes": 4320}   # 3 días
    """
    _ = session, contact
    cfg = _config(step)
    minutes = int(cfg.get("duration_minutes") or 0)
    wake_at = datetime.now(UTC) + timedelta(minutes=max(minutes, 1))
    return StepResult(
        wake_at=wake_at,
        result={"sleep_until": wake_at.isoformat()},
    )


@register_step("wait_until")
def _step_wait_until(session, run, step, contact) -> StepResult:
    """Espera hasta una fecha relativa a un campo del contacto. Config:
      {"field": "contact.date_birthday", "hour_local": 9, "offset_days": 0}

    Acepta también una fecha absoluta:
      {"absolute_at": "2026-12-25T09:00:00+00:00"}
    """
    _ = session
    cfg = _config(step)
    if cfg.get("absolute_at"):
        try:
            target = datetime.fromisoformat(cfg["absolute_at"])
            if target.tzinfo is None:
                target = target.replace(tzinfo=UTC)
            return StepResult(
                wake_at=target, result={"sleep_until": target.isoformat()}
            )
        except ValueError:
            log.warning("workflows wait_until invalid absolute_at")
            return StepResult(status="failed", error="invalid_absolute_at")

    # Field-based. Hoy solo `contact.created_at` y campos genéricos.
    field = cfg.get("field") or "contact.created_at"
    offset_days = int(cfg.get("offset_days") or 0)
    hour_local = int(cfg.get("hour_local") or 9)
    raw = None
    if field == "contact.created_at":
        raw = contact.created_at
    if raw is None:
        return StepResult(
            status="failed", error=f"unsupported_field:{field}"
        )
    target = raw + timedelta(days=offset_days)
    target = target.replace(
        hour=hour_local, minute=0, second=0, microsecond=0
    )
    if target.tzinfo is None:
        target = target.replace(tzinfo=UTC)
    return StepResult(
        wake_at=target, result={"sleep_until": target.isoformat()}
    )


@register_step("wait_for_event")
def _step_wait_for_event(session, run, step, contact) -> StepResult:
    """Espera a que llegue un evento concreto (o caduque).
    Config:
      {"event_type": "email.crm.opened",
       "timeout_minutes": 10080,   # 7 días
       "condition": {...}}
    """
    _ = session, contact
    cfg = _config(step)
    event_type = cfg.get("event_type")
    if not event_type:
        return StepResult(
            status="failed", error="wait_for_event_no_event_type"
        )
    timeout_minutes = int(cfg.get("timeout_minutes") or 10080)
    timeout_at = datetime.now(UTC) + timedelta(
        minutes=max(timeout_minutes, 1)
    )
    return StepResult(
        wait_for_event={
            "event_type": event_type,
            "condition": cfg.get("condition") or {},
            "timeout_at": timeout_at,
        },
        result={
            "waiting_for": event_type,
            "timeout_at": timeout_at.isoformat(),
        },
    )


# ---------------------------------------------------------------------
# Conditions
# ---------------------------------------------------------------------


@register_step("condition")
def _step_condition(session, run, step, contact) -> StepResult:
    """if/else. Config:
      {"condition": <árbol JSON>}
    Rama tomada: "true" o "false".
    """
    cfg = _config(step)
    ctx = conditions.EvalContext(
        session=session,
        contact=contact,
        trigger_payload=_trigger_payload(run),
    )
    matched = conditions.evaluate(cfg.get("condition"), ctx)
    return StepResult(
        branch_label="true" if matched else "false",
        result={"matched": matched},
    )


@register_step("switch")
def _step_switch(session, run, step, contact) -> StepResult:
    """N ramas sobre el mismo campo. Config:
      {"field": "contact.lifecycle_status",
       "cases": ["new", "qualified", "customer"]}
    Rama tomada: "case_0", "case_1", ..., "default".
    """
    cfg = _config(step)
    field = cfg.get("field")
    cases = cfg.get("cases") or []
    if not field:
        return StepResult(
            branch_label="default", result={"reason": "no_field"}
        )
    ctx = conditions.EvalContext(
        session=session,
        contact=contact,
        trigger_payload=_trigger_payload(run),
    )
    resolver = conditions._FIELD_RESOLVERS.get(field)
    if resolver is None:
        return StepResult(
            branch_label="default", result={"reason": "unknown_field"}
        )
    actual = resolver(ctx)
    for i, expected in enumerate(cases):
        if actual == expected:
            return StepResult(
                branch_label=f"case_{i}", result={"matched": expected}
            )
    return StepResult(branch_label="default", result={"matched": None})


# ---------------------------------------------------------------------
# Actions — Contact mutations
# ---------------------------------------------------------------------


@register_step("action_add_tag")
def _step_add_tag(session, run, step, contact) -> StepResult:
    _ = run
    cfg = _config(step)
    tag_name = (cfg.get("tag") or "").strip()
    if not tag_name:
        return StepResult(status="skipped", error="empty_tag")
    # `Contact.tags` es CSV — el campo legacy. Lo respetamos para no
    # divergir del resto del CRM (la M:N tabla `contact_tags` también
    # existe pero esto basta para Bloque 1).
    current = [
        t.strip() for t in (contact.tags or "").split(",") if t.strip()
    ]
    if tag_name.lower() not in {t.lower() for t in current}:
        current.append(tag_name)
        contact.tags = ",".join(current)
    return StepResult(result={"added_tag": tag_name})


@register_step("action_remove_tag")
def _step_remove_tag(session, run, step, contact) -> StepResult:
    _ = session, run
    cfg = _config(step)
    tag_name = (cfg.get("tag") or "").strip().lower()
    if not tag_name:
        return StepResult(status="skipped", error="empty_tag")
    current = [
        t.strip() for t in (contact.tags or "").split(",") if t.strip()
    ]
    new = [t for t in current if t.lower() != tag_name]
    contact.tags = ",".join(new)
    return StepResult(result={"removed_tag": tag_name})


@register_step("action_change_lifecycle_status")
def _step_change_lifecycle_status(
    session, run, step, contact
) -> StepResult:
    _ = session, run
    cfg = _config(step)
    new = (cfg.get("status") or "").strip()
    if not new:
        return StepResult(status="skipped", error="empty_status")
    old = contact.commercial_status
    contact.commercial_status = new
    return StepResult(result={"old": old, "new": new})


@register_step("action_set_custom_field")
def _step_set_custom_field(session, run, step, contact) -> StepResult:
    _ = session, run
    cfg = _config(step)
    field = (cfg.get("field") or "").strip()
    value = cfg.get("value")
    if not field:
        return StepResult(status="skipped", error="empty_field")
    raw = {}
    try:
        raw = json.loads(contact.custom_fields or "{}") or {}
    except (TypeError, ValueError):
        raw = {}
    raw[field] = value
    contact.custom_fields = json.dumps(raw, default=str)
    return StepResult(result={"field": field, "value": value})


@register_step("action_change_lead_score")
def _step_change_lead_score(session, run, step, contact) -> StepResult:
    _ = session, run
    cfg = _config(step)
    delta = int(cfg.get("delta") or 0)
    if delta == 0:
        return StepResult(status="skipped", error="zero_delta")
    old = contact.lead_score or 0
    contact.lead_score = old + delta
    return StepResult(result={"old": old, "delta": delta, "new": contact.lead_score})


@register_step("action_assign_owner")
def _step_assign_owner(session, run, step, contact) -> StepResult:
    _ = run
    cfg = _config(step)
    target_user_id = cfg.get("user_id")
    if not target_user_id:
        return StepResult(status="skipped", error="no_user_id")
    user = session.get(User, target_user_id)
    if user is None or not user.is_active:
        return StepResult(status="skipped", error="user_inactive_or_missing")
    old = contact.owner_user_id
    contact.owner_user_id = target_user_id
    return StepResult(result={"old": old, "new": target_user_id})


# ---------------------------------------------------------------------
# Action — Tasks
# ---------------------------------------------------------------------


@register_step("action_create_task")
def _step_create_task(session, run, step, contact) -> StepResult:
    cfg = _config(step)
    ctx = variables.build_context(
        session=session,
        contact=contact,
        trigger_payload=_trigger_payload(run),
    )
    title = variables.render(
        cfg.get("title") or "Tarea automática",
        ctx,
        is_html=False,
    )[:200]
    description = variables.render(
        cfg.get("description") or "",
        ctx,
        is_html=False,
    )
    priority_raw = (cfg.get("priority") or "medium").lower()
    try:
        priority = TaskPriority(priority_raw)
    except ValueError:
        priority = TaskPriority.MEDIUM
    due_days = int(cfg.get("due_in_days") or 1)
    due_at = datetime.now(UTC) + timedelta(days=due_days)

    assignee_id = (
        cfg.get("assign_to_user_id") or contact.owner_user_id
    )
    if not assignee_id:
        return StepResult(
            status="skipped", error="no_assignee_no_owner"
        )
    task = Task(
        title=title,
        description=description or None,
        contact_id=contact.id,
        assigned_user_id=assignee_id,
        created_by_user_id=assignee_id,
        priority=priority,
        due_at=due_at,
        status=TaskStatus.PENDING,
    )
    session.add(task)
    session.flush()
    return StepResult(
        result={"task_id": task.id, "title": title, "due_at": due_at.isoformat()}
    )


# ---------------------------------------------------------------------
# Action — Opportunity / Pipeline
# ---------------------------------------------------------------------


@register_step("action_move_opportunity_stage")
def _step_move_opportunity_stage(
    session, run, step, contact
) -> StepResult:
    _ = run
    cfg = _config(step)
    target_stage_id = cfg.get("stage_id")
    if not target_stage_id:
        return StepResult(status="skipped", error="no_stage_id")
    row = session.scalar(
        select(ContactPipelineStage)
        .where(ContactPipelineStage.contact_id == contact.id)
        .order_by(ContactPipelineStage.entered_stage_at.desc())
        .limit(1)
    )
    if row is None:
        return StepResult(status="skipped", error="no_opportunity")
    old = row.stage_id
    row.stage_id = target_stage_id
    row.entered_stage_at = datetime.now(UTC)
    return StepResult(result={"old_stage": old, "new_stage": target_stage_id})


# ---------------------------------------------------------------------
# Action — Email
# ---------------------------------------------------------------------


EMAIL_DAILY_CAP = 400  # margen sobre Gmail 500/día


def _emails_sent_today_by_user(session, user_id: str) -> int:
    """Cuenta envíos del owner hoy. Reutilizamos `email_messages`
    sin nueva tabla."""
    today_start = datetime.now(UTC).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return int(
        session.scalar(
            select(func.count(EmailMessage.id)).where(
                EmailMessage.direction == EmailDirection.OUTBOUND,
                EmailMessage.created_by_user_id == user_id,
                EmailMessage.sent_at >= today_start,
            )
        )
        or 0
    )


def _next_run_after_cap_reset() -> datetime:
    """Cuando el cap del owner está saturado, defer al día siguiente
    00:01 UTC. (Bart puede tunear si el VPS está en otra timezone.)"""
    now = datetime.now(UTC)
    tomorrow = (now + timedelta(days=1)).replace(
        hour=0, minute=1, second=0, microsecond=0
    )
    return tomorrow


@register_step("action_send_email")
def _step_send_email(session, run, step, contact) -> StepResult:
    """Envía email usando template del CRM o cuerpo inline. Config:
      {"template_id": "...",     # OR
       "subject": "...",
       "body_html": "...",
       "from_alias": "info@bomedia.net",
       "from_name": "Bart"}
    """
    cfg = _config(step)
    if not contact.email:
        return StepResult(status="skipped", error="contact_no_email")
    if not contact.is_active:
        return StepResult(status="skipped", error="contact_inactive")
    if contact.marketing_consent and getattr(
        contact.marketing_consent, "value", str(contact.marketing_consent)
    ) == "unsubscribed":
        return StepResult(
            status="skipped", error="contact_unsubscribed"
        )

    owner_id = contact.owner_user_id
    if not owner_id:
        return StepResult(status="skipped", error="contact_no_owner")

    # Cap diario del owner.
    if _emails_sent_today_by_user(session, owner_id) >= EMAIL_DAILY_CAP:
        log.info(
            "workflows email cap reached for user=%s; deferring",
            owner_id,
        )
        return StepResult(
            status="deferred",
            wake_at=_next_run_after_cap_reset(),
            error="email_cap_reached",
        )

    # PR-Fixes #8. Si el step usa `template_id`, resolvemos la
    # plantilla actual y rellenamos subject/body desde ahí. Las
    # ediciones de la plantilla quedan reflejadas automáticamente
    # porque leemos siempre fresco. Si la plantilla se ha borrado
    # del CRM, marcamos skipped — no es razón para fallar el run.
    raw_subject = cfg.get("subject") or ""
    raw_body = cfg.get("body_html") or ""
    template_id = cfg.get("template_id")
    if template_id:
        from app.email_templates.models import EmailTemplate  # noqa: PLC0415

        template = session.get(EmailTemplate, template_id)
        if template is None:
            return StepResult(
                status="skipped",
                error=f"template_not_found:{template_id}",
            )
        raw_subject = template.subject or ""
        raw_body = template.body_html or ""

    # Render template.
    ctx_vars = variables.build_context(
        session=session,
        contact=contact,
        trigger_payload=_trigger_payload(run),
    )
    subject = variables.render(
        raw_subject,
        ctx_vars,
        is_html=False,
    )[:500]
    body_html = variables.render(
        raw_body,
        ctx_vars,
        is_html=True,
    )
    from_alias = cfg.get("from_alias")
    from_name = cfg.get("from_name")

    # Delega en gmail_service.send_email. Si falla (Gmail no
    # conectado, etc.), marcamos como skipped — no rompe el run.
    try:
        from app.integrations.gmail.service import (  # noqa: PLC0415
            GmailNotConnectedError,
            GmailScopeMissingError,
            send_email,
        )

        message = send_email(
            session,
            sender_user_id=owner_id,
            from_alias=from_alias or "",
            from_name=from_name,
            to=[contact.email],
            cc=None,
            bcc=None,
            subject=subject,
            body_html=body_html,
            body_text=None,
            contact_id=contact.id,
            in_reply_to_message_id=None,
            include_unsubscribe=False,
        )
    except (GmailNotConnectedError, GmailScopeMissingError) as exc:
        return StepResult(
            status="skipped", error=f"gmail_not_ready:{exc}"
        )
    except Exception as exc:  # noqa: BLE001
        return StepResult(status="failed", error=str(exc)[:500])

    return StepResult(
        result={
            "message_id": message.id,
            "subject": subject,
            "to": contact.email,
        }
    )


# ---------------------------------------------------------------------
# Action — Notify
# ---------------------------------------------------------------------


@register_step("action_notify_owner")
def _step_notify_owner(session, run, step, contact) -> StepResult:
    """Mete una notificación in-app + envía email de aviso al owner.
    Reutiliza el helper `notifications` si existe. Si no, simplemente
    audit + email helper."""
    _ = run
    cfg = _config(step)
    owner_id = contact.owner_user_id
    if not owner_id:
        return StepResult(status="skipped", error="contact_no_owner")
    user = session.get(User, owner_id)
    if user is None:
        return StepResult(status="skipped", error="owner_missing")

    ctx_vars = variables.build_context(
        session=session,
        contact=contact,
        trigger_payload=_trigger_payload(run),
    )
    message = variables.render(
        cfg.get("message") or f"Workflow: revisa {contact.email}",
        ctx_vars,
        is_html=False,
    )[:500]
    # No bloqueamos en errores de email — la notificación in-app es
    # el canal principal. Aquí dejamos solo el audit; un sprint
    # futuro puede añadir notifications table.
    log.info(
        "workflows notify_owner user=%s contact=%s msg=%s",
        owner_id,
        contact.id,
        message,
    )
    return StepResult(
        result={"owner_id": owner_id, "message": message}
    )


@register_step("action_notify_manager")
def _step_notify_manager(session, run, step, contact) -> StepResult:
    """Notifica al manager del owner — del primer manager activo
    encontrado por role."""
    _ = run
    cfg = _config(step)
    from app.models.crm import UserRole  # noqa: PLC0415

    manager = session.scalar(
        select(User)
        .where(User.role == UserRole.MANAGER, User.is_active.is_(True))
        .limit(1)
    )
    if manager is None:
        return StepResult(status="skipped", error="no_manager")
    ctx_vars = variables.build_context(
        session=session,
        contact=contact,
        trigger_payload=_trigger_payload(run),
    )
    message = variables.render(
        cfg.get("message") or f"Workflow escala: {contact.email}",
        ctx_vars,
        is_html=False,
    )[:500]
    log.info(
        "workflows notify_manager manager=%s contact=%s msg=%s",
        manager.id,
        contact.id,
        message,
    )
    return StepResult(
        result={"manager_id": manager.id, "message": message}
    )


# ---------------------------------------------------------------------
# Action — Sync externos
# ---------------------------------------------------------------------


@register_step("action_push_to_brevo")
def _step_push_to_brevo(session, run, step, contact) -> StepResult:
    """Encola push del contacto a Brevo. Lazy import del helper para no
    arrastrar el módulo en tests que no lo necesitan."""
    _ = run, step
    if not contact.email:
        return StepResult(status="skipped", error="contact_no_email")
    try:
        from rq import Queue  # noqa: PLC0415

        from app.workers.queues import (  # noqa: PLC0415
            queue_name,
            redis_connection,
        )

        queue = Queue(
            queue_name("brevo", "push_contact"),
            connection=redis_connection(),
        )
        queue.enqueue(
            "app.integrations.brevo.tasks.push_contact",
            contact.id,
        )
        return StepResult(result={"enqueued_brevo_push": contact.id})
    except Exception as exc:  # noqa: BLE001
        return StepResult(status="skipped", error=str(exc)[:200])


@register_step("action_force_agilecrm_resync")
def _step_force_agilecrm_resync(
    session, run, step, contact
) -> StepResult:
    """Marca external_references del contacto para sync forzado en el
    próximo tick del scheduler Agile."""
    _ = run, step
    from app.models.crm import ExternalReference  # noqa: PLC0415

    rows = list(
        session.scalars(
            select(ExternalReference).where(
                ExternalReference.contact_id == contact.id,
                ExternalReference.system == "agilecrm",
            )
        )
    )
    for row in rows:
        row.external_status = "force_resync"
    return StepResult(
        result={"marked": len(rows)},
    )


# ---------------------------------------------------------------------
# Exit nodes
# ---------------------------------------------------------------------


@register_step("exit_natural")
def _step_exit_natural(session, run, step, contact) -> StepResult:
    _ = session, run, step, contact
    return StepResult(exit_kind=WorkflowExitKind.NATURAL)


@register_step("exit_won")
def _step_exit_won(session, run, step, contact) -> StepResult:
    _ = session, run, step, contact
    return StepResult(exit_kind=WorkflowExitKind.WON)


@register_step("exit_lost")
def _step_exit_lost(session, run, step, contact) -> StepResult:
    _ = session, run, step, contact
    return StepResult(exit_kind=WorkflowExitKind.LOST)


# ---------------------------------------------------------------------
# Catalog para el frontend
# ---------------------------------------------------------------------


STEP_CATALOG: list[dict[str, Any]] = [
    # Lógica
    {"type": "wait_time", "category": "wait", "label": "Esperar tiempo fijo"},
    {"type": "wait_until", "category": "wait", "label": "Esperar hasta fecha"},
    {
        "type": "wait_for_event",
        "category": "wait",
        "label": "Esperar evento (con timeout)",
    },
    {"type": "condition", "category": "logic", "label": "Condición if/else"},
    {"type": "switch", "category": "logic", "label": "Switch N ramas"},
    # Acciones contacto
    {"type": "action_add_tag", "category": "contact", "label": "Añadir tag"},
    {"type": "action_remove_tag", "category": "contact", "label": "Quitar tag"},
    {
        "type": "action_change_lifecycle_status",
        "category": "contact",
        "label": "Cambiar estado del ciclo",
    },
    {
        "type": "action_set_custom_field",
        "category": "contact",
        "label": "Modificar custom field",
    },
    {
        "type": "action_change_lead_score",
        "category": "contact",
        "label": "Sumar/Restar lead score",
    },
    {
        "type": "action_assign_owner",
        "category": "contact",
        "label": "Asignar propietario",
    },
    # Tareas
    {"type": "action_create_task", "category": "task", "label": "Crear tarea"},
    # Email
    {"type": "action_send_email", "category": "email", "label": "Enviar email"},
    # Oportunidades
    {
        "type": "action_move_opportunity_stage",
        "category": "opportunity",
        "label": "Mover oportunidad a stage",
    },
    # Notificaciones
    {
        "type": "action_notify_owner",
        "category": "notify",
        "label": "Notificar propietario",
    },
    {
        "type": "action_notify_manager",
        "category": "notify",
        "label": "Notificar manager",
    },
    # Sync externos
    {
        "type": "action_push_to_brevo",
        "category": "sync",
        "label": "Push contacto a Brevo",
    },
    {
        "type": "action_force_agilecrm_resync",
        "category": "sync",
        "label": "Forzar próximo sync AgileCRM",
    },
    # Salidas
    {"type": "exit_natural", "category": "exit", "label": "Salida natural"},
    {"type": "exit_won", "category": "exit", "label": "Salida ganada"},
    {"type": "exit_lost", "category": "exit", "label": "Salida perdida"},
]
