"""Sprint Workflows — definición canónica de cada trigger.

Única fuente de verdad consumida por el dispatcher (matcher de runtime),
el estimator (fuente de conteo 30d) y el catálogo del wizard
(disponibilidad). Antes cada capa tenía su propia lista/mapping y
divergían (auditoría docs/audit-workflows-2026-07-29.md §1-§3).

Contrato por trigger:
  - `kind`: "event" (bus dispatch_event), "state" (evaluado por sweep) o
    "schedule" (cron).
  - `available`: False → el wizard lo muestra deshabilitado, la
    activación de workflows NUEVOS con ese trigger se rechaza y el
    estimator devuelve None ("—" en UI). Los workflows ya activos no se
    tocan (compat).
  - `matcher(cfg, payload)`: filtros de config aplicados en RUNTIME
    sobre el payload del evento. El estimator aplica los MISMOS filtros
    en su query — tests de mutación cubren que no diverjan.
  - `estimator(session, cfg, cutoff)`: count real de eventos 30d con los
    filtros de config aplicados, o None si no hay fuente honesta.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from sqlalchemy import Text, cast, func, select
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

Matcher = Callable[[dict[str, Any], dict[str, Any]], bool]
Estimator = Callable[[Session, dict[str, Any], datetime], "int | None"]


@dataclass(frozen=True)
class TriggerDef:
    type: str
    label: str
    kind: str  # event | state | schedule
    available: bool = True
    unavailable_reason: str | None = None
    matcher: Matcher | None = None
    estimator: Estimator | None = None
    config_keys: tuple[str, ...] = field(default=())


# ---------------------------------------------------------------------
# Helpers de matching (runtime)
# ---------------------------------------------------------------------


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _match_brevo(cfg: dict[str, Any], payload: dict[str, Any]) -> bool:
    """campaign_id (str del wizard) vs payload campaign_brevo_id (int);
    account_id vs sufijo de source "brevo:{account}"."""
    wanted = _as_int(cfg.get("campaign_id")) if cfg.get("campaign_id") else None
    if wanted is not None and _as_int(payload.get("campaign_brevo_id")) != wanted:
        return False
    account = cfg.get("account_id")
    if account:
        source = str(payload.get("source") or "")
        if source.startswith("brevo:") and source.split(":", 1)[1] != account:
            return False
    return True


def _match_brevo_clicked(cfg: dict[str, Any], payload: dict[str, Any]) -> bool:
    if not _match_brevo(cfg, payload):
        return False
    link = cfg.get("link_url")
    if link and (payload.get("link") or "") != link:
        return False
    return True


def _match_contact_updated(cfg: dict[str, Any], payload: dict[str, Any]) -> bool:
    wanted_field = cfg.get("field")
    changed = payload.get("changed_fields") or []
    if wanted_field:
        if wanted_field not in changed:
            return False
        new_value = cfg.get("new_value")
        if new_value not in (None, ""):
            changes = payload.get("changes") or {}
            actual = changes.get(wanted_field)
            actual_new = (
                actual[1]
                if isinstance(actual, (list, tuple)) and len(actual) == 2
                else None
            )
            if str(actual_new) != str(new_value):
                return False
    return True


def _match_lifecycle(cfg: dict[str, Any], payload: dict[str, Any]) -> bool:
    from_s, to_s = cfg.get("from_status"), cfg.get("to_status")
    if from_s and str(payload.get("from_status")) != str(from_s):
        return False
    if to_s and str(payload.get("to_status")) != str(to_s):
        return False
    return True


def _match_task(cfg: dict[str, Any], payload: dict[str, Any]) -> bool:
    priority = cfg.get("priority")
    if priority and str(payload.get("priority")) != str(priority):
        return False
    return True


def _match_crm_email(cfg: dict[str, Any], payload: dict[str, Any]) -> bool:
    """owner_user_id = user CRM que envió el email trackeado.
    `template_id` no tiene columna de respaldo → se ignora (retirado de
    la UI en este sprint)."""
    owner = cfg.get("owner_user_id")
    if owner and str(payload.get("owner_user_id")) != str(owner):
        return False
    return True


def _match_crm_clicked(cfg: dict[str, Any], payload: dict[str, Any]) -> bool:
    if not _match_crm_email(cfg, payload):
        return False
    link = cfg.get("link_url")
    if link and (payload.get("url") or "") != link:
        return False
    return True


# ---------------------------------------------------------------------
# Estimators (fuentes reales 30d, con los MISMOS filtros que el matcher)
# ---------------------------------------------------------------------


def _est_contact_created(session: Session, cfg: dict, cutoff: datetime) -> int:
    from app.models.crm import Contact  # noqa: PLC0415

    return int(
        session.scalar(
            select(func.count(Contact.id)).where(Contact.created_at >= cutoff)
        )
        or 0
    )


def _est_contact_updated(session: Session, cfg: dict, cutoff: datetime) -> int:
    from app.models.crm import AuditLog  # noqa: PLC0415

    clauses = [
        AuditLog.action.in_(["contact.updated", "contact.bulk_updated"]),
        AuditLog.created_at >= cutoff,
    ]
    wanted = cfg.get("field")
    if wanted:
        clauses.append(cast(AuditLog.metadata_json, Text).like(f'%"{wanted}"%'))
    return int(session.scalar(select(func.count(AuditLog.id)).where(*clauses)) or 0)


def _est_lifecycle(session: Session, cfg: dict, cutoff: datetime) -> int:
    from app.models.crm import AuditLog  # noqa: PLC0415

    return int(
        session.scalar(
            select(func.count(AuditLog.id)).where(
                AuditLog.action.in_(["contact.updated", "contact.bulk_updated"]),
                AuditLog.created_at >= cutoff,
                cast(AuditLog.metadata_json, Text).like("%commercial_status%"),
            )
        )
        or 0
    )


def _est_activity(event_types: list[str]) -> Estimator:
    def _fn(session: Session, cfg: dict, cutoff: datetime) -> int:
        from app.models.crm import ActivityEvent  # noqa: PLC0415

        clauses = [
            ActivityEvent.event_type.in_(event_types),
            ActivityEvent.occurred_at >= cutoff,
        ]
        wanted = _as_int(cfg.get("campaign_id")) if cfg.get("campaign_id") else None
        if wanted is not None:
            clauses.append(ActivityEvent.campaign_brevo_id == wanted)
        return int(
            session.scalar(select(func.count(ActivityEvent.id)).where(*clauses)) or 0
        )

    return _fn


def _est_brevo_clicked(session: Session, cfg: dict, cutoff: datetime) -> int:
    from app.models.crm import ActivityEvent  # noqa: PLC0415

    clauses = [
        ActivityEvent.event_type == "email.clicked",
        ActivityEvent.occurred_at >= cutoff,
    ]
    wanted = _as_int(cfg.get("campaign_id")) if cfg.get("campaign_id") else None
    if wanted is not None:
        clauses.append(ActivityEvent.campaign_brevo_id == wanted)
    link = cfg.get("link_url")
    if link:
        clauses.append(ActivityEvent.body == str(link))
    return int(session.scalar(select(func.count(ActivityEvent.id)).where(*clauses)) or 0)


def _est_crm_email(event_type: str) -> Estimator:
    def _fn(session: Session, cfg: dict, cutoff: datetime) -> int:
        from app.models.crm import EmailMessage, EmailMessageEvent  # noqa: PLC0415

        clauses = [
            EmailMessageEvent.event_type == event_type,
            EmailMessageEvent.occurred_at >= cutoff,
        ]
        stmt = select(func.count(EmailMessageEvent.id)).where(*clauses)
        owner = cfg.get("owner_user_id")
        if owner:
            stmt = stmt.join(
                EmailMessage, EmailMessage.id == EmailMessageEvent.message_id
            ).where(EmailMessage.gmail_account_user_id == str(owner))
        link = cfg.get("link_url")
        if link and event_type == "click":
            stmt = stmt.where(
                cast(EmailMessageEvent.metadata_json, Text).like(f'%{link}%')
            )
        return int(session.scalar(stmt) or 0)

    return _fn


def _est_crm_replied(session: Session, cfg: dict, cutoff: datetime) -> int:
    from app.models.crm import ActivityEvent  # noqa: PLC0415

    return int(
        session.scalar(
            select(func.count(ActivityEvent.id)).where(
                ActivityEvent.event_type == "email.reply_received",
                ActivityEvent.occurred_at >= cutoff,
            )
        )
        or 0
    )


def _est_task(column_name: str) -> Estimator:
    def _fn(session: Session, cfg: dict, cutoff: datetime) -> int:
        from app.models.crm import Task  # noqa: PLC0415

        column = getattr(Task, column_name)
        clauses = [column >= cutoff, Task.contact_id.is_not(None)]
        priority = cfg.get("priority")
        if priority:
            clauses.append(Task.priority == priority)
        return int(session.scalar(select(func.count(Task.id)).where(*clauses)) or 0)

    return _fn


def _est_engagement(session: Session, cfg: dict, cutoff: datetime) -> int:
    """Contactos ÚNICOS que cumplen min_opens+min_clicks en la ventana —
    misma semántica que el evaluador de runtime (1 disparo/contacto)."""
    from sqlalchemy import case  # noqa: PLC0415

    from app.models.crm import ActivityEvent  # noqa: PLC0415

    min_opens = int(cfg.get("min_opens") or 0)
    min_clicks = int(cfg.get("min_clicks") or 0)
    opens = func.sum(case((ActivityEvent.event_type == "email.opened", 1), else_=0))
    clicks = func.sum(case((ActivityEvent.event_type == "email.clicked", 1), else_=0))
    sub = (
        select(ActivityEvent.contact_id)
        .where(
            ActivityEvent.event_type.in_(["email.opened", "email.clicked"]),
            ActivityEvent.occurred_at >= cutoff,
        )
        .group_by(ActivityEvent.contact_id)
        .having(opens >= min_opens, clicks >= min_clicks)
        .subquery()
    )
    return int(session.scalar(select(func.count()).select_from(sub)) or 0)


def _est_matches_conditions(session: Session, cfg: dict, cutoff: datetime) -> None:
    """Las ENTRADAS futuras no son estimables desde histórico → None
    ("—"). El count de "cumplen ahora" lo da matching_contacts_now."""
    _ = (session, cfg, cutoff)
    return None


# ---------------------------------------------------------------------
# Registro canónico
# ---------------------------------------------------------------------

_UNAVAILABLE_NO_ENTITY = "No disponible: el CRM no tiene entidad de oportunidades."
_UNAVAILABLE_NO_DETECTOR = "No disponible: sin detector de vencimiento (sprint futuro)."
_UNAVAILABLE_NO_COLUMNS = (
    "No disponible: los contactos no tienen campos de fecha "
    "(cumpleaños/aniversario)."
)

TRIGGER_DEFS: dict[str, TriggerDef] = {
    d.type: d
    for d in [
        TriggerDef("contact.created", "Contacto creado", "event",
                   estimator=_est_contact_created),
        TriggerDef("contact.updated", "Contacto actualizado", "event",
                   matcher=_match_contact_updated, estimator=_est_contact_updated,
                   config_keys=("field", "new_value")),
        TriggerDef("contact.lifecycle_changed", "Contacto cambia de estado del ciclo",
                   "event", matcher=_match_lifecycle, estimator=_est_lifecycle,
                   config_keys=("from_status", "to_status")),
        TriggerDef("contact.unsubscribed", "Contacto se da de baja", "event",
                   estimator=_est_activity(
                       ["email.unsubscribed", "email.spam_complaint"])),
        TriggerDef("email.crm.opened", "Email del CRM abierto", "event",
                   matcher=_match_crm_email, estimator=_est_crm_email("open"),
                   config_keys=("owner_user_id",)),
        TriggerDef("email.crm.clicked", "Link de email CRM cliqueado", "event",
                   matcher=_match_crm_clicked, estimator=_est_crm_email("click"),
                   config_keys=("owner_user_id", "link_url")),
        TriggerDef("email.crm.replied", "Email del CRM respondido", "event",
                   matcher=_match_crm_email, estimator=_est_crm_replied,
                   config_keys=("owner_user_id",)),
        TriggerDef("email.brevo.opened", "Email campaña Brevo abierto", "event",
                   matcher=_match_brevo, estimator=_est_activity(["email.opened"]),
                   config_keys=("account_id", "campaign_id")),
        TriggerDef("email.brevo.clicked", "Link campaña Brevo cliqueado", "event",
                   matcher=_match_brevo_clicked, estimator=_est_brevo_clicked,
                   config_keys=("account_id", "campaign_id", "link_url")),
        TriggerDef("engagement.brevo.composed",
                   "Engagement Brevo compuesto (N aperturas + N clicks en X días)",
                   "state", estimator=_est_engagement,
                   config_keys=("min_opens", "min_clicks", "window_days")),
        TriggerDef("task.created", "Tarea creada", "event",
                   matcher=_match_task, estimator=_est_task("created_at"),
                   config_keys=("priority",)),
        TriggerDef("task.completed", "Tarea completada", "event",
                   matcher=_match_task, estimator=_est_task("completed_at"),
                   config_keys=("priority",)),
        TriggerDef("task.overdue", "Tarea vencida", "event",
                   available=False, unavailable_reason=_UNAVAILABLE_NO_DETECTOR),
        TriggerDef("opportunity.created", "Oportunidad creada", "event",
                   available=False, unavailable_reason=_UNAVAILABLE_NO_ENTITY),
        TriggerDef("opportunity.stage_changed", "Oportunidad cambia de stage", "event",
                   available=False, unavailable_reason=_UNAVAILABLE_NO_ENTITY),
        TriggerDef("opportunity.won", "Oportunidad ganada", "event",
                   available=False, unavailable_reason=_UNAVAILABLE_NO_ENTITY),
        TriggerDef("opportunity.lost", "Oportunidad perdida", "event",
                   available=False, unavailable_reason=_UNAVAILABLE_NO_ENTITY),
        TriggerDef("contact.date_field",
                   "Fecha del contacto (cumpleaños, aniversario...)", "state",
                   available=False, unavailable_reason=_UNAVAILABLE_NO_COLUMNS),
        TriggerDef("cron.recurring", "Horario fijo", "schedule",
                   config_keys=("preset", "hour")),
        # Sprint Ficha 360. Solo se dispara via
        # POST /contacts/{id}/workflows/{wf}/run - nunca por eventos
        # (ningun productor emite "contact.manual") ni por sweep.
        TriggerDef("contact.manual", "Ejecución manual", "manual"),
        TriggerDef("contact.matches_conditions",
                   "Contacto pasa a cumplir condiciones", "state",
                   estimator=_est_matches_conditions,
                   config_keys=("filter",)),
    ]
}


def get_def(trigger_type: str) -> TriggerDef | None:
    return TRIGGER_DEFS.get(trigger_type)


def config_matches(
    trigger_type: str, cfg: dict[str, Any], payload: dict[str, Any]
) -> bool:
    """Aplica el matcher específico del trigger. Sin matcher → True."""
    definition = TRIGGER_DEFS.get(trigger_type)
    if definition is None or definition.matcher is None:
        return True
    try:
        return definition.matcher(cfg, payload)
    except Exception:  # noqa: BLE001 — un matcher roto no debe tumbar el dispatch
        log.warning("workflows.trigger_matcher error type=%s", trigger_type,
                    exc_info=True)
        return True


def estimate_runs_30d(
    session: Session, trigger_type: str, cfg: dict[str, Any], cutoff: datetime
) -> int | None:
    """Count 30d desde la fuente real, o None si no hay fuente honesta
    (la UI muestra "—")."""
    definition = TRIGGER_DEFS.get(trigger_type)
    if definition is None or not definition.available:
        return None
    if definition.estimator is None:
        return None
    try:
        return definition.estimator(session, cfg, cutoff)
    except Exception:  # noqa: BLE001
        log.warning("workflows.trigger_estimator error type=%s", trigger_type,
                    exc_info=True)
        return None


def catalog() -> list[dict[str, Any]]:
    """Catálogo para el wizard, con disponibilidad."""
    return [
        {
            "type": d.type,
            "label": d.label,
            "kind": d.kind,
            "available": d.available,
            "unavailable_reason": d.unavailable_reason,
        }
        for d in TRIGGER_DEFS.values()
    ]
