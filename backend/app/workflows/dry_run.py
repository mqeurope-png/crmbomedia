"""Simulador de workflow sin commitear nada — Sprint UX-Workflows.

`simulate_workflow(session, workflow_id, contact_id)` recorre el grafo
del workflow en orden, evaluando condiciones contra el contacto real
pero **sin** ejecutar las acciones. Devuelve una lista ordenada de
`SimulationStep` describiendo qué pasaría.

Nada toca la BD: waits no duermen, emails no se envían, tags no se
añaden. Es la mejor verificación previa antes de activar un workflow.

Diseño:

1. Cargamos workflow + steps + edges en memoria.
2. Empezamos por el step entry y avanzamos siguiendo edges.
3. Cada step se "ejecuta" en modo describe-only:
   - `wait_time` → describe la duración resolviendo unidades.
   - `condition` → evalúa el árbol contra el contacto real y devuelve
     qué rama tomaría.
   - `action_*` → describe qué acción haría (subject del email
     renderizado con variables, tag a añadir, etc.).
4. Si una rama hace loop o supera 50 pasos abortamos con `looped`.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.crm import Contact
from app.models.workflows import (
    Workflow,
    WorkflowEdge,
    WorkflowStep,
)
from app.workflows import conditions, variables

log = logging.getLogger(__name__)

MAX_SIMULATED_STEPS = 50


@dataclass
class SimulationStep:
    step_id: str
    step_type: str
    display_name: str | None
    label: str
    description: str
    branch_taken: str | None = None
    config_summary: dict[str, Any] = field(default_factory=dict)


@dataclass
class SimulationResult:
    contact_id: str
    contact_email: str | None
    workflow_id: str
    steps: list[SimulationStep] = field(default_factory=list)
    truncated: bool = False
    error: str | None = None


def _humanize_duration_minutes(minutes: int) -> str:
    """Réplica server-side del helper de frontend. Acepta entradas
    pequeñas o muy grandes con salida coherente en español."""
    if minutes < 0:
        return f"{minutes} min"
    if minutes < 60:
        return f"{minutes} minuto{'s' if minutes != 1 else ''}"
    if minutes < 60 * 24:
        h = minutes // 60
        rest = minutes % 60
        out = f"{h} hora{'s' if h != 1 else ''}"
        if rest:
            out += f" y {rest} min"
        return out
    if minutes < 60 * 24 * 7:
        d = minutes // (60 * 24)
        return f"{d} día{'s' if d != 1 else ''}"
    if minutes < 60 * 24 * 60:
        w = minutes // (60 * 24 * 7)
        return f"{w} semana{'s' if w != 1 else ''}"
    months = minutes // (60 * 24 * 30)
    return f"~{months} mes{'es' if months != 1 else ''}"


def _describe_step(
    session: Session,
    step: WorkflowStep,
    contact: Contact,
    trigger_payload: dict[str, Any],
) -> tuple[str, str, str | None, dict[str, Any]]:
    """Devuelve `(label, description, branch_taken, summary)`."""
    try:
        cfg = json.loads(step.config_json or "{}")
    except (TypeError, ValueError):
        cfg = {}

    if step.type == "trigger":
        return ("Inicio", "Punto de entrada del workflow", None, {})

    if step.type == "wait_time":
        minutes = int(cfg.get("duration_minutes") or 0)
        human = _humanize_duration_minutes(minutes)
        return (
            f"Esperar {human}",
            f"El workflow se pausaría {human}.",
            None,
            {"duration": human, "minutes": minutes},
        )

    if step.type == "wait_until":
        return (
            "Esperar hasta fecha",
            "Pausaría hasta la fecha configurada.",
            None,
            {"field": cfg.get("field"), "offset_days": cfg.get("offset_days")},
        )

    if step.type == "wait_for_event":
        event_type = cfg.get("event_type") or "evento"
        timeout = int(cfg.get("timeout_minutes") or 0)
        return (
            f"Esperar evento '{event_type}'",
            f"Esperaría hasta {timeout} min a que ocurra {event_type}.",
            None,
            {"event_type": event_type, "timeout_minutes": timeout},
        )

    if step.type == "condition":
        ctx = conditions.EvalContext(
            session=session,
            contact=contact,
            trigger_payload=trigger_payload,
        )
        matched = conditions.evaluate(cfg.get("condition"), ctx)
        branch = "true" if matched else "false"
        return (
            "Condición",
            f"Tomaría la rama '{branch}'.",
            branch,
            {"matched": matched},
        )

    if step.type == "switch":
        ctx = conditions.EvalContext(
            session=session,
            contact=contact,
            trigger_payload=trigger_payload,
        )
        field_name = cfg.get("field")
        resolver = conditions._FIELD_RESOLVERS.get(field_name or "")
        if resolver is None:
            return (
                "Switch",
                "Campo desconocido — tomaría la rama 'default'.",
                "default",
                {"field": field_name},
            )
        actual = resolver(ctx)
        cases = cfg.get("cases") or []
        for i, c in enumerate(cases):
            if actual == c:
                return (
                    f"Switch sobre {field_name}",
                    f"Valor='{actual}' → rama 'case_{i}'.",
                    f"case_{i}",
                    {"field": field_name, "value": actual},
                )
        return (
            f"Switch sobre {field_name}",
            f"Valor='{actual}' no matchea ningún caso → rama 'default'.",
            "default",
            {"field": field_name, "value": actual},
        )

    if step.type == "action_send_email":
        ctx_vars = variables.build_context(
            session=session, contact=contact, trigger_payload=trigger_payload
        )
        subject = variables.render(
            cfg.get("subject") or "", ctx_vars, is_html=False
        )[:200]
        return (
            "Enviar email",
            f"Enviaría a {contact.email or '(sin email)'} con asunto «{subject}».",
            None,
            {"subject": subject, "to": contact.email},
        )

    if step.type == "action_add_tag":
        tag = cfg.get("tag") or ""
        return (
            f"Añadir tag '{tag}'",
            f"Añadiría el tag '{tag}' al contacto.",
            None,
            {"tag": tag},
        )

    if step.type == "action_remove_tag":
        tag = cfg.get("tag") or ""
        return (
            f"Quitar tag '{tag}'",
            f"Quitaría el tag '{tag}' del contacto.",
            None,
            {"tag": tag},
        )

    if step.type == "action_change_lifecycle_status":
        new = cfg.get("status") or ""
        return (
            f"Cambiar estado del ciclo a '{new}'",
            f"Estado actual: '{contact.commercial_status}' → '{new}'.",
            None,
            {"from": contact.commercial_status, "to": new},
        )

    if step.type == "action_set_custom_field":
        return (
            f"Modificar custom field '{cfg.get('field')}'",
            f"Pondría valor: {cfg.get('value')}.",
            None,
            {"field": cfg.get("field"), "value": cfg.get("value")},
        )

    if step.type == "action_change_lead_score":
        delta = int(cfg.get("delta") or 0)
        current = contact.lead_score or 0
        verb = "Sumaría" if delta >= 0 else "Restaría"
        return (
            f"{verb} {abs(delta)} puntos lead score",
            f"{current} → {current + delta}.",
            None,
            {"delta": delta, "current": current, "new": current + delta},
        )

    if step.type == "action_assign_owner":
        target = cfg.get("user_id")
        return (
            "Asignar propietario",
            f"Cambiaría owner a {target}.",
            None,
            {"new_owner_id": target},
        )

    if step.type == "action_create_task":
        ctx_vars = variables.build_context(
            session=session, contact=contact, trigger_payload=trigger_payload
        )
        title = variables.render(
            cfg.get("title") or "", ctx_vars, is_html=False
        )[:120]
        days = int(cfg.get("due_in_days") or 1)
        return (
            f"Crear tarea: «{title}»",
            f"Vencimiento: en {days} día{'s' if days != 1 else ''}.",
            None,
            {"title": title, "due_in_days": days},
        )

    if step.type == "action_move_opportunity_stage":
        return (
            "Mover oportunidad de stage",
            f"Cambiaría a stage_id={cfg.get('stage_id')}.",
            None,
            {"stage_id": cfg.get("stage_id")},
        )

    if step.type == "action_notify_owner":
        return (
            "Notificar al propietario",
            "Enviaría notificación in-app + email al owner.",
            None,
            {},
        )

    if step.type == "action_notify_manager":
        return (
            "Notificar al manager",
            "Enviaría notificación al primer manager activo.",
            None,
            {},
        )

    if step.type == "action_push_to_brevo":
        return (
            "Push contacto a Brevo",
            "Encolaría sync inmediato con Brevo.",
            None,
            {},
        )

    if step.type == "action_force_agilecrm_resync":
        return (
            "Forzar resync AgileCRM",
            "Marcaría external_references para sync forzado.",
            None,
            {},
        )

    if step.type.startswith("exit_"):
        kind = step.type.replace("exit_", "")
        return (
            f"Salida {kind}",
            f"El workflow terminaría con estado '{kind}'.",
            None,
            {"kind": kind},
        )

    return (
        f"Paso {step.type}",
        f"Ejecutaría el paso tipo {step.type}.",
        None,
        {},
    )


def _next_step_id(
    edges: Iterable[WorkflowEdge],
    from_step_id: str,
    branch_label: str | None,
) -> str | None:
    """Sigue la edge correspondiente al branch_label. Si no hay, prueba
    'default'."""
    branch = branch_label or "default"
    candidates = [e for e in edges if e.from_step_id == from_step_id]
    for e in candidates:
        if e.branch_label == branch:
            return e.to_step_id
    if branch != "default":
        for e in candidates:
            if e.branch_label == "default":
                return e.to_step_id
    return None


def simulate_workflow(
    session: Session,
    workflow_id: str,
    contact_id: str,
) -> SimulationResult:
    workflow = session.get(Workflow, workflow_id)
    contact = session.get(Contact, contact_id)
    if workflow is None:
        return SimulationResult(
            contact_id=contact_id,
            contact_email=None,
            workflow_id=workflow_id,
            error="workflow_not_found",
        )
    if contact is None:
        return SimulationResult(
            contact_id=contact_id,
            contact_email=None,
            workflow_id=workflow_id,
            error="contact_not_found",
        )

    steps = list(
        session.scalars(
            select(WorkflowStep).where(WorkflowStep.workflow_id == workflow_id)
        )
    )
    edges = list(
        session.scalars(
            select(WorkflowEdge).where(WorkflowEdge.workflow_id == workflow_id)
        )
    )
    by_id = {s.id: s for s in steps}
    entry = next((s for s in steps if s.is_entry), None)
    if entry is None:
        return SimulationResult(
            contact_id=contact_id,
            contact_email=contact.email,
            workflow_id=workflow_id,
            error="no_entry_step",
        )

    result = SimulationResult(
        contact_id=contact_id,
        contact_email=contact.email,
        workflow_id=workflow_id,
    )

    trigger_payload = {"event_type": workflow.trigger_type, "_simulated": True}
    current_id: str | None = entry.id
    visited = 0
    while current_id and visited < MAX_SIMULATED_STEPS:
        step = by_id.get(current_id)
        if step is None:
            break
        try:
            label, description, branch, summary = _describe_step(
                session, step, contact, trigger_payload
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("workflows.dry_run describe failed")
            result.steps.append(
                SimulationStep(
                    step_id=step.id,
                    step_type=step.type,
                    display_name=step.display_name,
                    label="Error",
                    description=f"No se pudo simular el paso: {exc}",
                )
            )
            break
        result.steps.append(
            SimulationStep(
                step_id=step.id,
                step_type=step.type,
                display_name=step.display_name,
                label=label,
                description=description,
                branch_taken=branch,
                config_summary=summary,
            )
        )
        if step.type.startswith("exit_"):
            break
        current_id = _next_step_id(edges, step.id, branch)
        visited += 1

    if visited >= MAX_SIMULATED_STEPS:
        result.truncated = True
    return result
