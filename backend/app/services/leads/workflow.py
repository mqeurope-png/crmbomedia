"""El workflow «Respuesta a leads (Fase 1)», montado sobre el motor.

Se crea desde Configuración ERP → Respuesta a leads («Crear el workflow») en
estado BORRADOR: Bart lo revisa en la pantalla de Workflows, lo cambia si
quiere (plazos, textos, a quién va la tarea) y lo activa. Los ids del pipeline
y de las etapas se resuelven por NOMBRE al crearlo («Ventas B2B» → «Nuevo
lead» / «Descartado / spam»), que es lo que una plantilla fija no puede hacer.

El flujo (lo que hace la Fase 1 con cada lead):

    trigger lead.received
      → clasificar
          [spam]    → añadir a Ventas B2B · Descartado / spam → salida perdida
          [omitido] → salida natural
          [ok]      → esperar 12 h (de 9 a 18, laborables)
                    → preparar borrador (plantilla por interés × idioma,
                      remitente de la web del lead)
                    → añadir a Ventas B2B · Nuevo lead
                    → crear tarea (clasificación, confianza, enlace al borrador)
                    → salida natural

Ni un paso de enviar.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.crm import Pipeline, PipelineStage
from app.models.workflows import (
    Workflow,
    WorkflowEdge,
    WorkflowStatus,
    WorkflowStep,
)
from app.services.leads.config import configuracion

NOMBRE = "Respuesta a leads (Fase 1)"
PIPELINE = "Ventas B2B"
ETAPA_NUEVO = "Nuevo lead"
ETAPA_SPAM = "Descartado / spam"
ESPERA_MINUTOS = 12 * 60


class WorkflowYaExiste(Exception):
    def __init__(self, workflow_id: str) -> None:
        super().__init__(f"El workflow «{NOMBRE}» ya existe ({workflow_id}).")
        self.workflow_id = workflow_id


def _plano(texto: str) -> str:
    return " ".join((texto or "").lower().replace("/", " ").split())


def pipeline_ventas(session: Session) -> tuple[Pipeline, dict[str, PipelineStage]]:
    """«Ventas B2B» con sus etapas por nombre. `LookupError` legible si falta
    algo: no se crea un pipeline ni una etapa desde aquí."""
    pipeline = next((
        p for p in session.scalars(select(Pipeline).where(Pipeline.is_active.is_(True)))
        if _plano(p.name) == _plano(PIPELINE)
    ), None)
    if pipeline is None:
        raise LookupError(
            f"No existe el pipeline «{PIPELINE}» (activo). Créalo en Pipelines antes."
        )
    etapas = {_plano(e.name): e for e in pipeline.stages}
    faltan = [n for n in (ETAPA_NUEVO, ETAPA_SPAM) if _plano(n) not in etapas]
    if faltan:
        raise LookupError(
            f"Al pipeline «{PIPELINE}» le faltan las etapas: {', '.join(faltan)}."
        )
    return pipeline, {n: etapas[_plano(n)] for n in (ETAPA_NUEVO, ETAPA_SPAM)}


def existente(session: Session) -> Workflow | None:
    return session.scalar(
        select(Workflow)
        .where(Workflow.name == NOMBRE, Workflow.status != WorkflowStatus.ARCHIVED)
        .order_by(Workflow.created_at.desc())
        .limit(1)
    )


def definicion(session: Session, *, asignar_a: str | None) -> dict[str, Any]:
    """Los pasos y las aristas del flujo, con los ids reales."""
    pipeline, etapas = pipeline_ventas(session)
    conf = configuracion(session)
    pasos: list[dict[str, Any]] = [
        {"client_id": "trigger", "type": "trigger", "config": {}, "x": 120, "y": 60,
         "is_entry": True},
        {"client_id": "clasificar", "type": "action_classify_lead",
         "config": {"max_age_hours": conf["antiguedad_horas"]}, "x": 120, "y": 200},
        {"client_id": "descartar", "type": "action_add_to_pipeline",
         "config": {"pipeline_id": pipeline.id, "stage_id": etapas[ETAPA_SPAM].id},
         "x": 460, "y": 340},
        {"client_id": "salida_spam", "type": "exit_lost", "config": {}, "x": 460, "y": 480},
        {"client_id": "salida_omitido", "type": "exit_natural", "config": {}, "x": 760,
         "y": 340},
        {"client_id": "esperar", "type": "wait_time",
         "config": {"duration_minutes": ESPERA_MINUTOS, "window": conf["ventana"]},
         "x": 120, "y": 340},
        {"client_id": "borrador", "type": "action_prepare_email_draft",
         "config": {"template_mode": "por_interes", "from_mode": "web_del_lead",
                    "owner_mode": "propietario", "user_id": asignar_a or ""},
         "x": 120, "y": 480},
        {"client_id": "pipeline", "type": "action_add_to_pipeline",
         "config": {"pipeline_id": pipeline.id, "stage_id": etapas[ETAPA_NUEVO].id},
         "x": 120, "y": 620},
        {"client_id": "tarea", "type": "action_create_task",
         "config": {
             "title": "Revisar lead: {{ lead.interes_texto }} ({{ lead.confianza }}) · "
                      "{{ contact.full_name }}",
             "description": (
                 "Lead de {{ lead.web }} en {{ lead.idioma }}.\n"
                 "Interés: {{ lead.interes_texto }} · confianza {{ lead.confianza }} · "
                 "{{ lead.motivo }}\n"
                 "Productos marcados: {{ lead.productos }}\n"
                 "Plantilla: {{ lead.plantilla }} · remitente: {{ lead.remitente }}\n"
                 "Borrador preparado (sin enviar): {{ lead.borrador_url }}\n"
                 "Consulta: {{ contact.email }}"
             ),
             "priority": "high", "assign_to_user_id": asignar_a or "",
             "due_mode": "relative", "duration_amount": 1, "duration_unit": "days",
         },
         "x": 120, "y": 760},
        {"client_id": "salida", "type": "exit_natural", "config": {}, "x": 120, "y": 900},
    ]
    aristas = [
        ("trigger", "clasificar", "default"),
        ("clasificar", "descartar", "spam"),
        ("descartar", "salida_spam", "default"),
        ("clasificar", "salida_omitido", "omitido"),
        ("clasificar", "esperar", "ok"),
        ("esperar", "borrador", "default"),
        ("borrador", "pipeline", "default"),
        ("pipeline", "tarea", "default"),
        ("tarea", "salida", "default"),
    ]
    return {"steps": pasos, "edges": aristas}


def crear_workflow(session: Session, *, actor_user_id: str | None) -> Workflow:
    """Crea el workflow en BORRADOR. Si ya hay uno con ese nombre (sin
    archivar), `WorkflowYaExiste` con su id."""
    from app.workflows.hashing import compute_exact_hash  # noqa: PLC0415

    previo = existente(session)
    if previo is not None:
        raise WorkflowYaExiste(previo.id)
    d = definicion(session, asignar_a=actor_user_id)
    ahora = datetime.now(UTC)
    wf = Workflow(
        name=NOMBRE,
        description=(
            "Fase 1: clasifica el lead, deja un borrador preparado, lo coloca en "
            "Ventas B2B y crea una tarea. No envía ningún correo al cliente."
        ),
        status=WorkflowStatus.DRAFT, trigger_type="lead.received",
        trigger_config_json=json.dumps({}), allow_reentry=False,
        created_by_user_id=actor_user_id, owner_user_id=None,
    )
    session.add(wf)
    session.flush()
    ids: dict[str, str] = {}
    filas: list[WorkflowStep] = []
    for p in d["steps"]:
        step = WorkflowStep(
            id=str(uuid4()), workflow_id=wf.id, type=p["type"],
            config_json=json.dumps(p["config"], default=str),
            position_x=float(p["x"]), position_y=float(p["y"]),
            is_entry=bool(p.get("is_entry")),
        )
        step.created_at = ahora
        step.updated_at = ahora
        ids[p["client_id"]] = step.id
        filas.append(step)
    session.add_all(filas)
    session.flush()
    aristas = [
        WorkflowEdge(workflow_id=wf.id, from_step_id=ids[a], to_step_id=ids[b], branch_label=rama)
        for a, b, rama in d["edges"]
    ]
    session.add_all(aristas)
    session.flush()
    wf.definition_hash = compute_exact_hash(wf, filas, aristas)
    try:
        from app.core.audit import record_event  # noqa: PLC0415

        record_event(
            session, action="workflow.created", target_type="workflow", target_id=wf.id,
            metadata={"name": NOMBRE, "via": "respuesta_leads", "actor_id": actor_user_id},
        )
    except Exception:  # noqa: BLE001 — la auditoría nunca bloquea
        pass
    session.flush()
    return wf
