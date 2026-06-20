"""HTTP layer del motor de workflows.

Rutas montadas en `/api/workflows`:

- `GET /` lista de workflows + métricas materializadas.
- `POST /` crear (status=draft).
- `GET /{id}` detail con steps + edges.
- `PUT /{id}` reemplazo estructural completo (decisión: editor manda
  todo el grafo de una; backend valida + reemplaza). Solo permitido si
  status ∈ {draft, paused}.
- `DELETE /{id}` archivado lógico (no borra runs).
- `POST /{id}/activate` + `pause` + `archive` + `cancel-run`.
- `POST /{id}/cost-estimate` previo a activar.
- `GET /{id}/runs` paginado.
- `GET /runs/{id}` con timeline de history.
- `POST /{id}/add-contact/{contact_id}` manual add (admin/manager).
- `GET /_catalog` triggers + step types + variables para el editor.
- `GET /_contacts/{contact_id}/workflows` runs del contacto (para
  pestaña ficha).
"""
from __future__ import annotations

import json
import logging
from typing import Any
from uuid import uuid4

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.core.audit import record_event
from app.core.auth import require_admin, require_manager, require_user
from app.core.errors import not_found
from app.db.session import get_session
from app.models.crm import Contact, User, UserRole
from app.models.workflows import (
    Workflow,
    WorkflowEdge,
    WorkflowRun,
    WorkflowRunHistory,
    WorkflowRunState,
    WorkflowStatus,
    WorkflowStep,
)
from app.schemas.workflows import (
    WorkflowActivateRequest,
    WorkflowCatalogResponse,
    WorkflowCostEstimate,
    WorkflowCreate,
    WorkflowDetail,
    WorkflowDryRunRequest,
    WorkflowDryRunResponse,
    WorkflowDryRunStep,
    WorkflowEdgeRead,
    WorkflowRead,
    WorkflowRunDetail,
    WorkflowRunHistoryRead,
    WorkflowRunRead,
    WorkflowStepRead,
    WorkflowTemplate,
    WorkflowUpdate,
)
from app.workflows import conditions, variables
from app.workflows.dispatcher import TRIGGER_CATALOG
from app.workflows.engine import (
    cancel_run as engine_cancel_run,
)
from app.workflows.steps import STEP_CATALOG

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/workflows", tags=["workflows"])


# ---------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------


def _workflow_to_read(
    workflow: Workflow, *, session: Session | None = None
) -> WorkflowRead:
    return WorkflowRead(
        id=workflow.id,
        name=workflow.name,
        description=workflow.description,
        status=workflow.status,
        trigger_type=workflow.trigger_type,
        trigger_config=_safe_json(workflow.trigger_config_json),
        allow_reentry=workflow.allow_reentry,
        cancellation_events=_safe_json_list(
            workflow.cancellation_events_json
        ),
        total_entered=workflow.total_entered,
        total_completed=workflow.total_completed,
        total_won=workflow.total_won,
        total_cancelled=workflow.total_cancelled,
        total_failed=workflow.total_failed,
        total_completed_with_skipped=_count_completed_with_skipped(
            session, workflow.id
        )
        if session is not None
        else 0,
        created_by_user_id=workflow.created_by_user_id,
        definition_hash=workflow.definition_hash,
        created_at=workflow.created_at,
        updated_at=workflow.updated_at,
    )


def _count_completed_with_skipped(
    session: Session, workflow_id: str
) -> int:
    """PR-Backlog-Consolidado A6. Cuántos runs completados de este
    workflow llegaron al final habiendo saltado >=1 step. Marcador
    persistido por el motor en `WorkflowRun.error_summary` con prefijo
    `completed_with_skipped:`."""
    from sqlalchemy import func  # noqa: PLC0415

    return int(
        session.scalar(
            select(func.count(WorkflowRun.id)).where(
                WorkflowRun.workflow_id == workflow_id,
                WorkflowRun.state == WorkflowRunState.COMPLETED,
                WorkflowRun.error_summary.like("completed_with_skipped:%"),
            )
        )
        or 0
    )


def _workflow_to_detail(
    session: Session, workflow: Workflow
) -> WorkflowDetail:
    base = _workflow_to_read(workflow, session=session)
    steps = list(
        session.scalars(
            select(WorkflowStep).where(
                WorkflowStep.workflow_id == workflow.id
            )
        )
    )
    edges = list(
        session.scalars(
            select(WorkflowEdge).where(
                WorkflowEdge.workflow_id == workflow.id
            )
        )
    )
    warnings = _duplicate_warnings(session, workflow, steps, edges)
    return WorkflowDetail(
        **base.model_dump(),
        steps=[
            WorkflowStepRead(
                id=s.id,
                type=s.type,
                config=_safe_json(s.config_json),
                position_x=s.position_x,
                position_y=s.position_y,
                is_entry=s.is_entry,
                display_name=s.display_name,
            )
            for s in steps
        ],
        edges=[
            WorkflowEdgeRead(
                id=e.id,
                from_step_id=e.from_step_id,
                to_step_id=e.to_step_id,
                branch_label=e.branch_label,
            )
            for e in edges
        ],
        duplicate_warnings=warnings,
    )


def _duplicate_warnings(
    session: Session,
    workflow: Workflow,
    steps: list,
    edges: list,
) -> list[dict[str, Any]]:
    """Devuelve advertencias de duplicado para workflows en draft/active
    distintos de éste. `kind="exact"` si comparten hash exacto;
    `kind="similar"` si comparten hash de topología (mismo skeleton).
    """
    from app.workflows.hashing import (  # noqa: PLC0415
        compute_exact_hash,
        compute_similarity_hash,
    )

    if not steps:
        return []
    exact = compute_exact_hash(workflow, steps, edges)
    similar = compute_similarity_hash(workflow, steps, edges)
    out: list[dict[str, Any]] = []
    # Exacta — mismas configs y topología.
    rows = list(
        session.scalars(
            select(Workflow).where(
                Workflow.id != workflow.id,
                Workflow.definition_hash == exact,
                Workflow.status.in_(
                    [WorkflowStatus.DRAFT, WorkflowStatus.ACTIVE]
                ),
            )
        )
    )
    for other in rows:
        out.append(
            {
                "workflow_id": other.id,
                "workflow_name": other.name,
                "kind": "exact",
                "created_by_user_id": other.created_by_user_id,
                "created_at": other.created_at.isoformat(),
            }
        )

    # Similar — mismo skeleton pero distintas configs. Esta detección
    # es O(N) sobre los workflows del mismo trigger_type — barata a
    # volumen Bomedia.
    other_workflows = list(
        session.scalars(
            select(Workflow).where(
                Workflow.id != workflow.id,
                Workflow.trigger_type == workflow.trigger_type,
                Workflow.status.in_(
                    [WorkflowStatus.DRAFT, WorkflowStatus.ACTIVE]
                ),
            )
        )
    )
    already_flagged = {o["workflow_id"] for o in out}
    for other in other_workflows:
        if other.id in already_flagged:
            continue
        other_steps = list(
            session.scalars(
                select(WorkflowStep).where(
                    WorkflowStep.workflow_id == other.id
                )
            )
        )
        other_edges = list(
            session.scalars(
                select(WorkflowEdge).where(
                    WorkflowEdge.workflow_id == other.id
                )
            )
        )
        if compute_similarity_hash(other, other_steps, other_edges) == similar:
            out.append(
                {
                    "workflow_id": other.id,
                    "workflow_name": other.name,
                    "kind": "similar",
                    "created_by_user_id": other.created_by_user_id,
                    "created_at": other.created_at.isoformat(),
                }
            )
    return out


def _safe_json(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}


def _safe_json_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    except (TypeError, ValueError):
        pass
    return []


def _run_to_read(run: WorkflowRun, *, workflow: Workflow | None = None) -> WorkflowRunRead:
    return WorkflowRunRead(
        id=run.id,
        workflow_id=run.workflow_id,
        workflow_name=workflow.name if workflow else None,
        contact_id=run.contact_id,
        state=run.state,
        exit_kind=run.exit_kind,
        current_step_id=run.current_step_id,
        started_at=run.started_at,
        completed_at=run.completed_at,
        wake_at=run.wake_at,
        error_summary=run.error_summary,
    )


# ---------------------------------------------------------------------
# Permissions helpers
# ---------------------------------------------------------------------


def _is_admin_or_manager(user: User) -> bool:
    return user.role in (UserRole.ADMIN, UserRole.MANAGER)


# ---------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------


@router.get("", response_model=list[WorkflowRead])
def list_workflows(
    status_filter: WorkflowStatus | None = Query(default=None, alias="status"),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> list[WorkflowRead]:
    _ = current_user
    stmt = select(Workflow).order_by(desc(Workflow.updated_at))
    if status_filter is not None:
        stmt = stmt.where(Workflow.status == status_filter)
    rows = list(session.scalars(stmt))
    return [_workflow_to_read(w, session=session) for w in rows]


@router.post("", response_model=WorkflowDetail, status_code=201)
def create_workflow(
    payload: WorkflowCreate,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> WorkflowDetail:
    workflow = Workflow(
        name=payload.name,
        description=payload.description,
        trigger_type=payload.trigger_type,
        trigger_config_json=json.dumps(payload.trigger_config or {}, default=str),
        allow_reentry=payload.allow_reentry,
        cancellation_events_json=json.dumps(payload.cancellation_events or [], default=str),
        status=WorkflowStatus.DRAFT,
        created_by_user_id=current_user.id,
    )
    session.add(workflow)
    session.flush()

    # Seed entry step automáticamente — el editor lo expone como
    # "Trigger" en el canvas. Sin él, el motor no puede arrancar.
    entry = WorkflowStep(
        workflow_id=workflow.id,
        type="trigger",
        config_json="{}",
        position_x=120,
        position_y=80,
        is_entry=True,
    )
    session.add(entry)

    record_event(
        session,
        action="workflow.created",
        target_type="workflow",
        target_id=workflow.id,
        actor=current_user,
        metadata={"name": workflow.name, "trigger": workflow.trigger_type},
        request=request,
    )
    session.commit()
    session.refresh(workflow)
    return _workflow_to_detail(session, workflow)


@router.get("/_catalog", response_model=WorkflowCatalogResponse)
def get_catalog(
    current_user: User = Depends(require_user),
) -> WorkflowCatalogResponse:
    _ = current_user
    return WorkflowCatalogResponse(
        triggers=TRIGGER_CATALOG,
        steps=STEP_CATALOG,
        fields=conditions.available_fields(),
        variables=variables.available_variables(),
    )


@router.get("/_contacts/{contact_id}/runs", response_model=list[WorkflowRunRead])
def list_contact_runs(
    contact_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> list[WorkflowRunRead]:
    """Para la pestaña Workflows en la ficha del contacto."""
    _ = current_user
    runs = list(
        session.scalars(
            select(WorkflowRun)
            .where(WorkflowRun.contact_id == contact_id)
            .order_by(desc(WorkflowRun.started_at))
            .limit(100)
        )
    )
    if not runs:
        return []
    wf_by_id = {
        w.id: w
        for w in session.scalars(
            select(Workflow).where(
                Workflow.id.in_({r.workflow_id for r in runs})
            )
        )
    }
    return [_run_to_read(r, workflow=wf_by_id.get(r.workflow_id)) for r in runs]


@router.get("/runs/{run_id}", response_model=WorkflowRunDetail)
def get_run_detail(
    run_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> WorkflowRunDetail:
    _ = current_user
    run = session.get(WorkflowRun, run_id)
    if run is None:
        raise not_found("WorkflowRun")
    workflow = session.get(Workflow, run.workflow_id)
    history_rows = list(
        session.scalars(
            select(WorkflowRunHistory)
            .where(WorkflowRunHistory.run_id == run.id)
            .order_by(WorkflowRunHistory.executed_at.asc())
        )
    )
    base = _run_to_read(run, workflow=workflow)
    return WorkflowRunDetail(
        **base.model_dump(),
        history=[
            WorkflowRunHistoryRead(
                id=h.id,
                step_id=h.step_id,
                step_type=h.step_type,
                status=h.status,
                result=_safe_json(h.result_json) or None,
                error_summary=h.error_summary,
                executed_at=h.executed_at,
            )
            for h in history_rows
        ],
    )


@router.post("/runs/{run_id}/cancel")
def cancel_run_route(
    run_id: str,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> dict[str, str]:
    run = session.get(WorkflowRun, run_id)
    if run is None:
        raise not_found("WorkflowRun")
    if not _is_admin_or_manager(current_user) and current_user.id != run.contact_id:
        # Comerciales solo pueden cancelar runs de SUS contactos.
        contact = session.get(Contact, run.contact_id)
        if contact is None or contact.owner_user_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No tienes permiso para cancelar este run.",
            )
    engine_cancel_run(session, run_id, reason="manual")
    record_event(
        session,
        action="workflow.run_cancelled_manually",
        target_type="workflow_run",
        target_id=run.id,
        actor=current_user,
        metadata={"workflow_id": run.workflow_id, "contact_id": run.contact_id},
        request=request,
    )
    session.commit()
    return {"status": "cancelling"}


# Sprint UX. Las rutas `_templates` se declaran AQUÍ — antes de
# `/{workflow_id}` — porque FastAPI resuelve por orden de declaración
# y `/{workflow_id}` capturaría "_templates" como un id de workflow si
# las dejáramos abajo (test_list_templates_returns_3_seed → 404).
@router.get("/_templates", response_model=list[WorkflowTemplate])
def list_templates_route(
    current_user: User = Depends(require_user),
) -> list[WorkflowTemplate]:
    _ = current_user
    from app.workflows.templates import list_templates  # noqa: PLC0415

    return [WorkflowTemplate(**t) for t in list_templates()]


@router.post(
    "/_templates/{template_id}/use",
    response_model=WorkflowDetail,
    status_code=201,
)
def use_template_route(
    template_id: str,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> WorkflowDetail:
    """Clona la plantilla a una nueva fila draft."""
    from app.workflows.templates import get_template  # noqa: PLC0415

    template = get_template(template_id)
    if template is None:
        raise not_found("Template")

    new_workflow = Workflow(
        name=f"{template['name']} (copia)",
        description=template.get("description"),
        trigger_type=template["trigger_type"],
        trigger_config_json=json.dumps(template.get("trigger_config") or {}),
        allow_reentry=False,
        cancellation_events_json='["contact.unsubscribed"]',
        status=WorkflowStatus.DRAFT,
        created_by_user_id=current_user.id,
    )
    session.add(new_workflow)
    session.flush()

    id_map: dict[str, str] = {}
    for s in template["steps"]:
        new_id = str(uuid4())
        id_map[s["client_id"]] = new_id
        session.add(
            WorkflowStep(
                id=new_id,
                workflow_id=new_workflow.id,
                type=s["type"],
                config_json=json.dumps(s.get("config") or {}, default=str),
                position_x=s.get("position_x", 0.0),
                position_y=s.get("position_y", 0.0),
                is_entry=s.get("is_entry", False),
            )
        )
    session.flush()
    for e in template["edges"]:
        from_db = id_map.get(e["from_client_id"])
        to_db = id_map.get(e["to_client_id"])
        if not from_db or not to_db:
            continue
        session.add(
            WorkflowEdge(
                workflow_id=new_workflow.id,
                from_step_id=from_db,
                to_step_id=to_db,
                branch_label=e.get("branch_label") or "default",
            )
        )
    session.flush()
    _recompute_definition_hash(session, new_workflow)
    record_event(
        session,
        action="workflow.created_from_template",
        target_type="workflow",
        target_id=new_workflow.id,
        actor=current_user,
        metadata={"template_id": template_id},
        request=request,
    )
    session.commit()
    session.refresh(new_workflow)
    return _workflow_to_detail(session, new_workflow)


@router.get("/{workflow_id}", response_model=WorkflowDetail)
def get_workflow(
    workflow_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> WorkflowDetail:
    _ = current_user
    workflow = session.get(Workflow, workflow_id)
    if workflow is None:
        raise not_found("Workflow")
    return _workflow_to_detail(session, workflow)


@router.put("/{workflow_id}", response_model=WorkflowDetail)
def update_workflow(
    workflow_id: str,
    payload: WorkflowUpdate,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> WorkflowDetail:
    workflow = session.get(Workflow, workflow_id)
    if workflow is None:
        raise not_found("Workflow")

    # Cambios estructurales (steps / edges) requieren DRAFT o PAUSED.
    structural = payload.steps is not None or payload.edges is not None
    if structural and workflow.status == WorkflowStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Pausa el workflow antes de editar pasos o aristas. "
                "Las ejecuciones activas continuarán con la versión "
                "vigente al inicio."
            ),
        )

    if payload.name is not None:
        workflow.name = payload.name
    if payload.description is not None:
        workflow.description = payload.description
    if payload.trigger_type is not None:
        workflow.trigger_type = payload.trigger_type
    if payload.trigger_config is not None:
        workflow.trigger_config_json = json.dumps(
            payload.trigger_config or {}, default=str
        )
    if payload.allow_reentry is not None:
        workflow.allow_reentry = payload.allow_reentry
    if payload.cancellation_events is not None:
        workflow.cancellation_events_json = json.dumps(
            payload.cancellation_events or [], default=str
        )

    if structural:
        _replace_steps_and_edges(
            session,
            workflow,
            steps=payload.steps or [],
            edges=payload.edges or [],
        )

    record_event(
        session,
        action="workflow.updated",
        target_type="workflow",
        target_id=workflow.id,
        actor=current_user,
        metadata={"structural": structural},
        request=request,
    )
    session.commit()
    session.refresh(workflow)
    return _workflow_to_detail(session, workflow)


def _replace_steps_and_edges(
    session: Session,
    workflow: Workflow,
    *,
    steps: list,
    edges: list,
) -> None:
    """Atomic replace. Borra los actuales + inserta los nuevos. El
    mapeo `client_id → DB id` se mantiene en `id_map` para las
    aristas."""
    # Cascade delete via FK → borrar el workflow_steps cascade los edges.
    session.execute(
        WorkflowEdge.__table__.delete().where(
            WorkflowEdge.workflow_id == workflow.id
        )
    )
    session.execute(
        WorkflowStep.__table__.delete().where(
            WorkflowStep.workflow_id == workflow.id
        )
    )
    session.flush()

    id_map: dict[str, str] = {}
    entry_seen = False
    for step in steps:
        new_id = str(uuid4())
        id_map[step.client_id] = new_id
        if step.is_entry:
            entry_seen = True
        session.add(
            WorkflowStep(
                id=new_id,
                workflow_id=workflow.id,
                type=step.type,
                config_json=json.dumps(step.config or {}, default=str),
                position_x=step.position_x,
                position_y=step.position_y,
                is_entry=step.is_entry,
                display_name=getattr(step, "display_name", None),
            )
        )
    if not entry_seen and steps:
        # El primer step pasa a entry por defecto si el editor no lo
        # marcó. Garantiza que advance_run encuentre el inicio.
        first_client = steps[0].client_id
        first_db_id = id_map[first_client]
        for s in session.scalars(
            select(WorkflowStep).where(WorkflowStep.id == first_db_id)
        ):
            s.is_entry = True
    session.flush()

    for edge in edges:
        from_db = id_map.get(edge.from_client_id)
        to_db = id_map.get(edge.to_client_id)
        if not from_db or not to_db:
            continue
        session.add(
            WorkflowEdge(
                workflow_id=workflow.id,
                from_step_id=from_db,
                to_step_id=to_db,
                branch_label=edge.branch_label or "default",
            )
        )
    session.flush()
    # Sprint UX. Recalculamos hash en cada save estructural para
    # detección de duplicados barata.
    _recompute_definition_hash(session, workflow)


def _recompute_definition_hash(session: Session, workflow: Workflow) -> None:
    """Llamado tras cada cambio estructural (PUT, duplicate,
    from-template). Lee steps + edges frescos de BD."""
    from app.workflows.hashing import compute_exact_hash  # noqa: PLC0415

    steps = list(
        session.scalars(
            select(WorkflowStep).where(WorkflowStep.workflow_id == workflow.id)
        )
    )
    edges = list(
        session.scalars(
            select(WorkflowEdge).where(WorkflowEdge.workflow_id == workflow.id)
        )
    )
    workflow.definition_hash = compute_exact_hash(workflow, steps, edges)


@router.post("/{workflow_id}/activate", response_model=WorkflowDetail)
def activate_workflow(
    workflow_id: str,
    payload: WorkflowActivateRequest,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> WorkflowDetail:
    workflow = session.get(Workflow, workflow_id)
    if workflow is None:
        raise not_found("Workflow")
    errors = _validate_workflow_structure(session, workflow)
    if errors:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"errors": errors},
        )
    # Sprint UX. Aseguramos hash actualizado y rechazamos duplicados
    # exactos antes de activar. El operador puede confirmar
    # "similares" desde el modal frontend.
    steps = list(
        session.scalars(
            select(WorkflowStep).where(
                WorkflowStep.workflow_id == workflow.id
            )
        )
    )
    edges = list(
        session.scalars(
            select(WorkflowEdge).where(
                WorkflowEdge.workflow_id == workflow.id
            )
        )
    )
    _recompute_definition_hash(session, workflow)
    warnings = _duplicate_warnings(session, workflow, steps, edges)
    exact_dupes = [w for w in warnings if w["kind"] == "exact"]
    if exact_dupes:
        other = exact_dupes[0]
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "duplicate_exact",
                "message": (
                    f"Este workflow es idéntico al workflow "
                    f"'{other['workflow_name']}'. No se puede crear "
                    "duplicado exacto."
                ),
                "duplicate_of": other,
            },
        )
    workflow.status = WorkflowStatus.ACTIVE
    record_event(
        session,
        action="workflow.activated",
        target_type="workflow",
        target_id=workflow.id,
        actor=current_user,
        metadata={"acknowledged_estimate": payload.acknowledged_estimate},
        request=request,
    )
    session.commit()
    session.refresh(workflow)
    return _workflow_to_detail(session, workflow)


@router.post("/{workflow_id}/pause", response_model=WorkflowDetail)
def pause_workflow(
    workflow_id: str,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> WorkflowDetail:
    workflow = session.get(Workflow, workflow_id)
    if workflow is None:
        raise not_found("Workflow")
    workflow.status = WorkflowStatus.PAUSED
    record_event(
        session,
        action="workflow.paused",
        target_type="workflow",
        target_id=workflow.id,
        actor=current_user,
        metadata={},
        request=request,
    )
    session.commit()
    session.refresh(workflow)
    return _workflow_to_detail(session, workflow)


@router.post("/{workflow_id}/archive", response_model=WorkflowDetail)
def archive_workflow(
    workflow_id: str,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> WorkflowDetail:
    workflow = session.get(Workflow, workflow_id)
    if workflow is None:
        raise not_found("Workflow")
    workflow.status = WorkflowStatus.ARCHIVED
    record_event(
        session,
        action="workflow.archived",
        target_type="workflow",
        target_id=workflow.id,
        actor=current_user,
        metadata={},
        request=request,
    )
    session.commit()
    session.refresh(workflow)
    return _workflow_to_detail(session, workflow)


@router.delete("/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_workflow(
    workflow_id: str,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> Response:
    workflow = session.get(Workflow, workflow_id)
    if workflow is None:
        raise not_found("Workflow")
    # Cancel runs activos + delete cascade vía FK.
    active_runs = list(
        session.scalars(
            select(WorkflowRun).where(
                WorkflowRun.workflow_id == workflow.id,
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
    for run in active_runs:
        engine_cancel_run(session, run.id, reason="workflow_deleted")
    session.delete(workflow)
    record_event(
        session,
        action="workflow.deleted",
        target_type="workflow",
        target_id=workflow_id,
        actor=current_user,
        metadata={"active_runs_cancelled": len(active_runs)},
        request=request,
    )
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------
# Sprint UX — duplicate, dry-run, templates
# ---------------------------------------------------------------------


@router.post("/{workflow_id}/duplicate", response_model=WorkflowDetail, status_code=201)
def duplicate_workflow_route(
    workflow_id: str,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> WorkflowDetail:
    """Clona name + trigger + todos los steps + edges a una nueva fila
    en estado DRAFT. El operador edita la copia desde el editor."""
    original = session.get(Workflow, workflow_id)
    if original is None:
        raise not_found("Workflow")

    new_workflow = Workflow(
        name=f"{original.name} (copia)",
        description=original.description,
        trigger_type=original.trigger_type,
        trigger_config_json=original.trigger_config_json or "{}",
        allow_reentry=original.allow_reentry,
        cancellation_events_json=(
            original.cancellation_events_json or '["contact.unsubscribed"]'
        ),
        status=WorkflowStatus.DRAFT,
        created_by_user_id=current_user.id,
    )
    session.add(new_workflow)
    session.flush()

    # Clone steps + edges manteniendo topología.
    original_steps = list(
        session.scalars(
            select(WorkflowStep).where(
                WorkflowStep.workflow_id == workflow_id
            )
        )
    )
    original_edges = list(
        session.scalars(
            select(WorkflowEdge).where(
                WorkflowEdge.workflow_id == workflow_id
            )
        )
    )
    id_map: dict[str, str] = {}
    for step in original_steps:
        new_id = str(uuid4())
        id_map[step.id] = new_id
        session.add(
            WorkflowStep(
                id=new_id,
                workflow_id=new_workflow.id,
                type=step.type,
                config_json=step.config_json or "{}",
                position_x=step.position_x,
                position_y=step.position_y,
                is_entry=step.is_entry,
                display_name=step.display_name,
            )
        )
    session.flush()
    for edge in original_edges:
        from_db = id_map.get(edge.from_step_id)
        to_db = id_map.get(edge.to_step_id)
        if not from_db or not to_db:
            continue
        session.add(
            WorkflowEdge(
                workflow_id=new_workflow.id,
                from_step_id=from_db,
                to_step_id=to_db,
                branch_label=edge.branch_label or "default",
            )
        )
    session.flush()
    _recompute_definition_hash(session, new_workflow)
    record_event(
        session,
        action="workflow.duplicated",
        target_type="workflow",
        target_id=new_workflow.id,
        actor=current_user,
        metadata={"source_workflow_id": original.id},
        request=request,
    )
    session.commit()
    session.refresh(new_workflow)
    return _workflow_to_detail(session, new_workflow)


@router.post("/{workflow_id}/dry-run", response_model=WorkflowDryRunResponse)
def dry_run_route(
    workflow_id: str,
    payload: WorkflowDryRunRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> WorkflowDryRunResponse:
    """Simula el workflow contra `contact_id` sin commitear nada."""
    _ = current_user
    from app.workflows.dry_run import simulate_workflow  # noqa: PLC0415

    result = simulate_workflow(session, workflow_id, payload.contact_id)
    return WorkflowDryRunResponse(
        workflow_id=result.workflow_id,
        contact_id=result.contact_id,
        contact_email=result.contact_email,
        steps=[
            WorkflowDryRunStep(
                step_id=s.step_id,
                step_type=s.step_type,
                display_name=s.display_name,
                label=s.label,
                description=s.description,
                branch_taken=s.branch_taken,
                config_summary=s.config_summary,
            )
            for s in result.steps
        ],
        truncated=result.truncated,
        error=result.error,
    )


# ---------------------------------------------------------------------
# Cost estimate
# ---------------------------------------------------------------------


@router.post("/{workflow_id}/cost-estimate", response_model=WorkflowCostEstimate)
def cost_estimate(
    workflow_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> WorkflowCostEstimate:
    _ = current_user
    workflow = session.get(Workflow, workflow_id)
    if workflow is None:
        raise not_found("Workflow")

    errors = _validate_workflow_structure(session, workflow)

    # PR-Fixes #4. Bart: el estimador devolvía "20003 contactos" para
    # `contact.created` y eso es engañoso — ese trigger solo dispara
    # sobre CREACIONES futuras, no sobre los contactos que ya existen.
    # Distinguimos dos familias:
    #
    # - **Evento puntual**: contact.created/updated, email.*, task.*,
    #   opportunity.*. `matching_contacts_now = 0` porque no aplica
    #   retroactivo. La estimación 30d se basa en histórico de runs
    #   del workflow + heurística suave.
    # - **Estado actual**: contact.date_field (cumpleaños hoy),
    #   engagement.brevo.composed (N aperturas en X días), cron
    #   (universo de contactos activos). Aquí sí cuenta contactos.
    EVENT_TRIGGERS = {
        "contact.created",
        "contact.updated",
        "contact.lifecycle_changed",
        "contact.unsubscribed",
        "email.crm.opened",
        "email.crm.clicked",
        "email.crm.replied",
        "email.brevo.opened",
        "email.brevo.clicked",
        "task.created",
        "task.completed",
        "task.overdue",
        "opportunity.created",
        "opportunity.stage_changed",
        "opportunity.won",
        "opportunity.lost",
    }
    STATE_TRIGGERS = {
        "contact.date_field",
        "engagement.brevo.composed",
        "cron.recurring",
    }

    try:
        trigger_cfg = json.loads(workflow.trigger_config_json or "{}")
    except (TypeError, ValueError):
        trigger_cfg = {}

    matching = 0
    if workflow.trigger_type in STATE_TRIGGERS:
        from sqlalchemy import func as _func  # noqa: PLC0415

        from app.workflows.conditions import (  # noqa: PLC0415
            EvalContext,
            evaluate,
        )

        if trigger_cfg.get("filter"):
            contacts = list(
                session.scalars(
                    select(Contact).where(Contact.is_active.is_(True))
                )
            )
            for c in contacts:
                ctx = EvalContext(session=session, contact=c)
                if evaluate(trigger_cfg["filter"], ctx):
                    matching += 1
        else:
            matching = int(
                session.scalar(
                    select(_func.count(Contact.id)).where(
                        Contact.is_active.is_(True)
                    )
                )
                or 0
            )

    # Heurística para 30d. Para event-based, miramos cuántos runs ha
    # tenido este workflow históricamente y proyectamos. Si es nuevo
    # (sin histórico), devolvemos 0 — el operador sabrá que la
    # estimación no es fiable hasta que active.
    steps = list(
        session.scalars(
            select(WorkflowStep).where(WorkflowStep.workflow_id == workflow_id)
        )
    )
    n_send_email = sum(1 for s in steps if s.type == "action_send_email")
    n_tasks = sum(1 for s in steps if s.type == "action_create_task")

    if workflow.trigger_type == "cron.recurring":
        # Asumimos 1 ejecución por contacto y por día — N=30.
        runs_30d = matching * 30
    elif workflow.trigger_type in STATE_TRIGGERS:
        # Asumimos que los que cumplen hoy se repartirán a lo largo
        # del mes (cumpleaños → 12 al mes / 365 al año).
        runs_30d = matching
    elif workflow.trigger_type in EVENT_TRIGGERS:
        # Histórico: cuántos runs hubo en los últimos 30 días → es
        # también la proyección para los próximos 30. Si es la primera
        # vez que se activa el workflow, runs_30d = 0.
        from datetime import UTC, datetime, timedelta  # noqa: PLC0415

        from app.models.workflows import WorkflowRun  # noqa: PLC0415

        cutoff = datetime.now(UTC) - timedelta(days=30)
        runs_30d = int(
            session.scalar(
                select(__import__("sqlalchemy").func.count(WorkflowRun.id)).where(
                    WorkflowRun.workflow_id == workflow_id,
                    WorkflowRun.started_at >= cutoff,
                )
            )
            or 0
        )
    else:
        runs_30d = 0

    return WorkflowCostEstimate(
        matching_contacts_now=matching,
        estimated_runs_30d=runs_30d,
        estimated_emails_30d=runs_30d * n_send_email,
        estimated_tasks_30d=runs_30d * n_tasks,
        validation_errors=errors,
    )


# ---------------------------------------------------------------------
# Runs listing
# ---------------------------------------------------------------------


@router.get("/{workflow_id}/runs", response_model=list[WorkflowRunRead])
def list_workflow_runs(
    workflow_id: str,
    state_filter: WorkflowRunState | None = Query(default=None, alias="state"),
    limit: int = Query(default=100, ge=1, le=500),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> list[WorkflowRunRead]:
    _ = current_user
    workflow = session.get(Workflow, workflow_id)
    if workflow is None:
        raise not_found("Workflow")
    stmt = (
        select(WorkflowRun)
        .where(WorkflowRun.workflow_id == workflow_id)
        .order_by(desc(WorkflowRun.started_at))
        .limit(limit)
    )
    if state_filter is not None:
        stmt = stmt.where(WorkflowRun.state == state_filter)
    runs = list(session.scalars(stmt))
    return [_run_to_read(r, workflow=workflow) for r in runs]


@router.post("/{workflow_id}/add-contact/{contact_id}")
def manual_add_contact(
    workflow_id: str,
    contact_id: str,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> dict[str, str]:
    """PR-Fix-Añadir-Manual-Workflow. Entrada forzada desde la ficha
    contacto. A diferencia del dispatch automático, aquí saltamos
    EL TRIGGER y arrancamos directo en su sucesor — el motor del PR
    #213 ya lo hacía como anchor del trigger, pero el lookup viejo
    dependía de `is_entry=True` que no era robusto en algunos drafts.

    Salta el cap de reentry porque el admin tomó la decisión expresa.
    Si el workflow es degenerado (trigger sin sucesor), devolvemos
    422 con mensaje específico — antes daba el genérico "no entry
    step" que confundía al operador (sí había entry, era el trigger).
    """
    workflow = session.get(Workflow, workflow_id)
    if workflow is None:
        raise not_found("Workflow")
    contact = session.get(Contact, contact_id)
    if contact is None:
        raise not_found("Contact")

    from app.workflows.engine import (  # noqa: PLC0415
        ManualStartError,
        advance_run,
        start_manual_run,
    )

    try:
        run = start_manual_run(
            session,
            workflow,
            contact,
            actor_user_id=current_user.id,
        )
    except ManualStartError as exc:
        http_status = (
            status.HTTP_422_UNPROCESSABLE_ENTITY
            if exc.code == "workflow_empty"
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(
            status_code=http_status, detail=exc.message
        ) from exc

    advance_run(session, run.id)
    record_event(
        session,
        action="workflow.contact_added_manually",
        target_type="workflow_run",
        target_id=run.id,
        actor=current_user,
        metadata={
            "workflow_id": workflow_id,
            "contact_id": contact_id,
            "manual_entry": True,
        },
        request=request,
    )
    session.commit()
    return {"run_id": run.id}


# ---------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------


def _validate_workflow_structure(
    session: Session, workflow: Workflow
) -> list[str]:
    errors: list[str] = []
    steps = list(
        session.scalars(
            select(WorkflowStep).where(WorkflowStep.workflow_id == workflow.id)
        )
    )
    if not steps:
        errors.append("El workflow no tiene pasos.")
        return errors
    entry = [s for s in steps if s.is_entry]
    if not entry:
        errors.append("El workflow no tiene paso de entrada (is_entry=True).")
    if len(entry) > 1:
        errors.append("Solo puede haber un paso de entrada.")
    # Steps inválidos por tipo desconocido.
    from app.workflows.engine import get_step_handler  # noqa: PLC0415

    for s in steps:
        if get_step_handler(s.type) is None:
            errors.append(f"Paso con tipo desconocido: {s.type}")
        # Conditions deben tener su árbol validado.
        if s.type == "condition":
            tree = conditions.parse_tree(
                json.dumps(_safe_json(s.config_json).get("condition", {}))
            )
            errors.extend(
                conditions.validate_tree(tree) or []
            )

    # PR-Fixes-Pase-3 Bug 1+4: nodos con múltiples ramas deben tener
    # TODAS las salidas conectadas para que el motor sepa qué hacer.
    # Para condition: debe haber al menos una edge con branch_label
    # ∈ {"true","false"}. Mismo principio para wait_for_event y switch.
    edges = list(
        session.scalars(
            select(WorkflowEdge).where(WorkflowEdge.workflow_id == workflow.id)
        )
    )
    edges_by_from: dict[str, set[str]] = {}
    for e in edges:
        edges_by_from.setdefault(e.from_step_id, set()).add(
            e.branch_label or "default"
        )
    for s in steps:
        branches = edges_by_from.get(s.id, set())
        # PR-Fix-Engine-Trigger-Step. El trigger (nodo raíz) debe tener
        # al menos una flecha saliente. Sin ella el workflow es
        # degenerado: el motor lo completaría inmediatamente sin
        # ejecutar nada.
        if s.type == "trigger" and not branches:
            errors.append(
                "El nodo raíz no tiene siguiente paso conectado — el "
                "workflow no haría nada al dispararse."
            )
        if s.type == "condition":
            for label in ("true", "false"):
                if label not in branches:
                    pretty = "Sí" if label == "true" else "No"
                    errors.append(
                        f"El nodo Condición tiene la rama «{pretty}» sin conectar."
                    )
        elif s.type == "wait_for_event":
            for label in ("matched", "timeout"):
                if label not in branches:
                    pretty = (
                        "Ocurrió" if label == "matched" else "Timeout"
                    )
                    errors.append(
                        f"El nodo Esperar evento tiene la rama «{pretty}» sin conectar."
                    )
        elif s.type == "switch":
            cfg = _safe_json(s.config_json)
            cases = cfg.get("cases") or []
            for i in range(len(cases)):
                if f"case_{i}" not in branches:
                    errors.append(
                        f"El nodo Switch tiene el caso «{cases[i]}» sin conectar."
                    )
            if "default" not in branches:
                errors.append(
                    "El nodo Switch tiene la rama «Otros» sin conectar."
                )
    return errors
