"""Sprint Ficha 360 — Bloque 1: Registrar llamada.

- POST   /api/contacts/{contact_id}/calls      crea el call_log + acciones
- GET    /api/contacts/{contact_id}/calls      lista paginada desc
- DELETE /api/contacts/{contact_id}/calls/{id} solo autor o admin
- POST   /api/contacts/{contact_id}/workflows/{workflow_id}/run
         ejecución manual de un workflow con trigger `contact.manual`
         (Bloque 3 — vive aquí por el prefix /api/contacts).

Las acciones post-llamada se ejecutan EN ORDEN: pipeline → lead_score →
tarea de rellamada (su id queda en follow_up_task_id) → workflows.
Cada acción deja su propia fila de audit.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.audit import Action, record_event
from app.core.auth import require_user
from app.core.errors import not_found
from app.db.session import get_session
from app.models.crm import (
    ActivityEvent,
    CallLog,
    Contact,
    Note,
    TaskPriority,
    TaskStatus,
    User,
    UserRole,
)
from app.models.workflows import Workflow, WorkflowStatus

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/contacts", tags=["call-logs"])

RESULT_CODES = {
    "contacted", "no_answer", "voicemail", "call_back",
    "interested", "not_interested", "info_requested", "other",
}
DURATION_BUCKETS = {"lt_1min", "1_to_5min", "5_to_30min", "gt_30min"}

#: Etiqueta legible por `result_code`, para el subject de la actividad. La
#: pestaña Historial tiene su propia copia (contact_timeline._RESULT_LABELS);
#: se mantienen en paralelo a propósito — una es del feed, otra del timeline.
RESULT_LABELS = {
    "contacted": "Contactado", "no_answer": "No contesta",
    "voicemail": "Buzón de voz", "call_back": "Volver a llamar",
    "interested": "Interesado", "not_interested": "No interesado",
    "info_requested": "Pidió información", "other": "Otro",
}


class PipelineChange(BaseModel):
    pipeline_id: str | None = None
    stage_id: str | None = None


class FollowUpTask(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    due_at: datetime | None = None
    assigned_to_user_id: str | None = None


class CallActions(BaseModel):
    pipeline_change: PipelineChange | None = None
    lead_score_delta: int | None = None
    # CRM-1: nuevo valor absoluto del star rating (1-5). A diferencia del lead
    # score, que es un delta, aquí el operador fija la valoración final.
    adjust_star_score: int | None = Field(default=None, ge=1, le=5)
    follow_up_task: FollowUpTask | None = None
    trigger_workflow_ids: list[str] = Field(default_factory=list)


class CallLogCreate(BaseModel):
    result_code: str
    result_custom: str | None = Field(default=None, max_length=150)
    subject: str | None = Field(default=None, max_length=255)
    notes: str | None = None
    duration_bucket: str | None = None
    called_at: datetime | None = None
    actions: CallActions = Field(default_factory=CallActions)


class CallLogRead(BaseModel):
    id: str
    contact_id: str
    user_id: str
    result_code: str
    result_custom: str | None
    subject: str | None
    notes: str | None
    duration_bucket: str | None
    called_at: datetime
    follow_up_task_id: str | None
    created_at: datetime
    actions_executed: list[str] = Field(default_factory=list)


def _serialise(row: CallLog, actions: list[str] | None = None) -> CallLogRead:
    return CallLogRead(
        id=row.id, contact_id=row.contact_id, user_id=row.user_id,
        result_code=row.result_code, result_custom=row.result_custom,
        subject=row.subject, notes=row.notes,
        duration_bucket=row.duration_bucket, called_at=row.called_at,
        follow_up_task_id=row.follow_up_task_id, created_at=row.created_at,
        actions_executed=actions or [],
    )


def _get_contact(session: Session, contact_id: str) -> Contact:
    contact = session.get(Contact, contact_id)
    if contact is None:
        raise not_found("Contact")
    return contact


@router.post("/{contact_id}/calls", response_model=CallLogRead, status_code=201)
def create_call_log(
    contact_id: str,
    payload: CallLogCreate,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> CallLogRead:
    contact = _get_contact(session, contact_id)
    if payload.result_code not in RESULT_CODES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"result_code inválido: {payload.result_code!r}",
        )
    if payload.result_code == "other" and not (payload.result_custom or "").strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="result_custom es obligatorio cuando result_code='other'.",
        )
    if payload.duration_bucket and payload.duration_bucket not in DURATION_BUCKETS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"duration_bucket inválido: {payload.duration_bucket!r}",
        )

    row = CallLog(
        contact_id=contact.id,
        user_id=current_user.id,
        result_code=payload.result_code,
        result_custom=(payload.result_custom or "").strip() or None,
        subject=payload.subject,
        notes=payload.notes,
        duration_bucket=payload.duration_bucket,
        called_at=payload.called_at or datetime.now(UTC),
    )
    session.add(row)
    session.flush()
    record_event(
        session, action="call_log.created", target_type="call_log",
        target_id=row.id, actor=current_user,
        metadata={"contact_id": contact.id, "result_code": row.result_code},
        request=request,
    )

    # Acciones en ORDEN: pipeline → lead_score → star_score → tarea → workflows.
    # `executed` es lo que devuelve la respuesta; `actions_taken` es lo que se
    # PERSISTE en la fila (con sus valores) para poder filtrar contactos por la
    # acción posterior de sus llamadas (CRM-1) y para rehacer el resumen. Las
    # claves de `actions_taken` son las canónicas del filtro, distintas de los
    # nombres del payload.
    executed: list[str] = []
    actions_taken: dict[str, object] = {}
    acts = payload.actions
    if acts.pipeline_change and acts.pipeline_change.pipeline_id:
        _do_pipeline_change(
            session, contact, acts.pipeline_change, current_user, request
        )
        executed.append("pipeline_change")
        actions_taken["change_pipeline"] = True
    if acts.lead_score_delta:
        contact.lead_score = (contact.lead_score or 0) + acts.lead_score_delta
        record_event(
            session, action="call_log.lead_score_adjusted",
            target_type="contact", target_id=contact.id, actor=current_user,
            metadata={"delta": acts.lead_score_delta,
                      "new_score": contact.lead_score, "call_log_id": row.id},
            request=request,
        )
        executed.append("lead_score_delta")
        actions_taken["adjust_lead_score"] = acts.lead_score_delta
    if acts.adjust_star_score is not None:
        previous_stars = contact.star_rating
        contact.star_rating = acts.adjust_star_score
        record_event(
            session, action="call_log.star_score_adjusted",
            target_type="contact", target_id=contact.id, actor=current_user,
            metadata={"from": previous_stars, "to": acts.adjust_star_score,
                      "call_log_id": row.id},
            request=request,
        )
        executed.append("adjust_star_score")
        actions_taken["adjust_star_score"] = acts.adjust_star_score
    if acts.follow_up_task:
        task = _do_follow_up_task(
            session, contact, acts.follow_up_task, current_user, request
        )
        row.follow_up_task_id = task.id
        executed.append("follow_up_task")
        actions_taken["create_callback_task"] = True
    for wf_id in acts.trigger_workflow_ids:
        _run_manual_workflow(
            session, contact, wf_id, current_user, request,
            source=f"call_log:{row.id}",
        )
        executed.append(f"workflow:{wf_id}")
    if acts.trigger_workflow_ids:
        actions_taken["add_to_workflow"] = list(acts.trigger_workflow_ids)

    row.actions_taken = json.dumps(actions_taken) if actions_taken else None

    # La nota de la llamada se propaga al timeline del contacto como una `Note`
    # (`source='call_log'`), para que aparezca en la pestaña Notas y en el
    # Historial sin que el operador la reescriba. Se enlaza a la llamada; si la
    # llamada se borra, la nota se conserva (FK ON DELETE SET NULL).
    if (payload.notes or "").strip():
        session.add(Note(
            contact_id=contact.id,
            body=payload.notes.strip(),
            source="call_log",
            call_log_id=row.id,
            created_by_user_id=current_user.id,
            author_user_id=current_user.id,
            created_at=row.called_at,
            updated_at=row.called_at,
        ))

    # La llamada entra en «Actividad reciente» de la ficha, que lee
    # `activity_events` (el resumen no consulta `call_logs`). El bucket
    # `CALL_LOG` ya lo pinta el frontend.
    _emit_call_activity_event(session, contact, row)

    session.commit()
    session.refresh(row)
    return _serialise(row, executed)


def _emit_call_activity_event(
    session: Session, contact: Contact, row: CallLog,
) -> None:
    """Registra la llamada en `activity_events` para el feed de la ficha.

    `activity_events` tiene UNIQUE `(system, account_id, external_id)`; se usa
    `crm/calls/call_log:<id>` para que un re-registro nunca duplique."""
    label = RESULT_LABELS.get(row.result_code, row.result_code)
    if row.result_code == "other" and row.result_custom:
        label = row.result_custom
    session.add(ActivityEvent(
        contact_id=contact.id,
        system="crm",
        account_id="calls",
        external_id=f"call_log:{row.id}",
        event_type="CALL_LOG",
        subject=f"Llamada: {label}",
        body=(row.notes or None),
        metadata_json=json.dumps({
            "call_log_id": row.id,
            "result_code": row.result_code,
            "duration_bucket": row.duration_bucket,
        }, default=str),
        occurred_at=row.called_at,
        synced_at=datetime.now(UTC),
    ))


def _do_pipeline_change(
    session: Session, contact: Contact, change: PipelineChange,
    current_user: User, request: Request,
) -> None:
    from app.models.crm import Pipeline  # noqa: PLC0415
    from app.repositories import pipelines as pipelines_repository  # noqa: PLC0415

    pipeline = session.get(Pipeline, change.pipeline_id)
    if pipeline is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El pipeline indicado no existe.",
        )
    try:
        pipelines_repository.add_contact_to_pipeline(
            session, contact=contact, pipeline=pipeline,
            stage_id=change.stage_id, note=None,
            moved_by_user_id=current_user.id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    record_event(
        session, action=Action.CONTACT_PIPELINE_STAGE_CHANGED,
        target_type="contact", target_id=contact.id, actor=current_user,
        metadata={"pipeline_id": change.pipeline_id,
                  "stage_id": change.stage_id, "via": "call_log"},
        request=request,
    )


def _do_follow_up_task(
    session: Session, contact: Contact, spec: FollowUpTask,
    current_user: User, request: Request,
):
    from app.repositories import tasks as tasks_repository  # noqa: PLC0415

    assignee = spec.assigned_to_user_id or current_user.id
    task = tasks_repository.create_task(
        session,
        title=spec.title,
        description=None,
        due_at=spec.due_at,
        status=TaskStatus.PENDING,
        priority=TaskPriority.MEDIUM,
        assigned_user_id=assignee,
        contact_id=contact.id,
        company_id=None,
        pipeline_stage_id=None,
        created_by_user_id=current_user.id,
        reminder_minutes_before=None,
    )
    record_event(
        session, action=Action.TASK_CREATED, target_type="task",
        target_id=task.id, actor=current_user,
        metadata={"contact_id": contact.id, "via": "call_log"},
        request=request,
    )
    return task


def _run_manual_workflow(
    session: Session, contact: Contact, workflow_id: str,
    current_user: User, request: Request, *, source: str,
) -> str:
    """Núcleo compartido del trigger manual (Bloque 3)."""
    from app.services.ownership import can_user_see_resource  # noqa: PLC0415
    from app.workflows.engine import (  # noqa: PLC0415
        ManualStartError,
        advance_run,
        start_manual_run,
    )

    workflow = session.get(Workflow, workflow_id)
    if workflow is None or not can_user_see_resource(current_user, workflow):
        raise not_found("Workflow")
    if workflow.trigger_type != "contact.manual":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Este workflow no tiene trigger de ejecución manual.",
        )
    if workflow.status != WorkflowStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El workflow no está activo.",
        )
    try:
        run = start_manual_run(
            session, workflow, contact, actor_user_id=current_user.id
        )
    except ManualStartError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=exc.message,
        ) from exc
    advance_run(session, run.id)
    record_event(
        session, action="workflow.run_manual", target_type="workflow_run",
        target_id=run.id, actor=current_user,
        metadata={"workflow_id": workflow.id, "contact_id": contact.id,
                  "source": source},
        request=request,
    )
    return run.id


@router.post("/{contact_id}/workflows/{workflow_id}/run")
def run_manual_workflow_endpoint(
    contact_id: str,
    workflow_id: str,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> dict[str, str]:
    contact = _get_contact(session, contact_id)
    run_id = _run_manual_workflow(
        session, contact, workflow_id, current_user, request, source="ficha"
    )
    session.commit()
    return {"run_id": run_id}


@router.get("/{contact_id}/calls", response_model=list[CallLogRead])
def list_call_logs(
    contact_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> list[CallLogRead]:
    _ = current_user
    _get_contact(session, contact_id)
    rows = list(
        session.scalars(
            select(CallLog)
            .where(CallLog.contact_id == contact_id)
            .order_by(CallLog.called_at.desc())
            .offset(offset)
            .limit(limit)
        )
    )
    return [_serialise(r) for r in rows]


@router.delete("/{contact_id}/calls/{call_id}")
def delete_call_log(
    contact_id: str,
    call_id: str,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> dict[str, str]:
    row = session.get(CallLog, call_id)
    if row is None or row.contact_id != contact_id:
        raise not_found("CallLog")
    if row.user_id != current_user.id and current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo quien registró la llamada (o un admin) puede borrarla.",
        )
    record_event(
        session, action="call_log.deleted", target_type="call_log",
        target_id=row.id, actor=current_user,
        metadata={"contact_id": contact_id, "result_code": row.result_code},
        request=request,
    )
    # La nota de la llamada se CONSERVA (menos destructivo que perder lo que se
    # escribió); solo se suelta el enlace. Se hace explícito aquí en vez de
    # confiar en el `ON DELETE SET NULL` de la FK: SQLite no lo aplica sin
    # PRAGMA y así el comportamiento es idéntico en test y en prod.
    session.execute(
        Note.__table__.update()  # type: ignore[attr-defined]
        .where(Note.call_log_id == row.id)
        .values(call_log_id=None)
    )
    session.delete(row)
    session.commit()
    return {"message": "deleted"}


# Conteo auxiliar para tests/validación rápida.
def count_calls(session: Session, contact_id: str) -> int:
    return int(
        session.scalar(
            select(func.count(CallLog.id)).where(CallLog.contact_id == contact_id)
        )
        or 0
    )
