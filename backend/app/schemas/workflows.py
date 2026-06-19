"""Pydantic schemas para `/api/workflows`."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.workflows import (
    WorkflowExitKind,
    WorkflowRunState,
    WorkflowStatus,
)

# ---------------------------------------------------------------------
# Step + Edge — substructure del workflow
# ---------------------------------------------------------------------


class WorkflowStepWrite(BaseModel):
    """Cuando el editor guarda, manda toda la lista de steps + edges.
    `client_id` permite al frontend usar ids temporales (UUIDs nuevos)
    para steps recién creados; el backend los mapea."""

    client_id: str = Field(min_length=1, max_length=80)
    type: str = Field(min_length=1, max_length=80)
    config: dict[str, Any] = Field(default_factory=dict)
    position_x: float = 0.0
    position_y: float = 0.0
    is_entry: bool = False


class WorkflowEdgeWrite(BaseModel):
    from_client_id: str
    to_client_id: str
    branch_label: str = "default"


class WorkflowStepRead(BaseModel):
    id: str
    type: str
    config: dict[str, Any] = Field(default_factory=dict)
    position_x: float
    position_y: float
    is_entry: bool


class WorkflowEdgeRead(BaseModel):
    id: str
    from_step_id: str
    to_step_id: str
    branch_label: str


# ---------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------


class WorkflowCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    trigger_type: str = Field(min_length=1, max_length=80)
    trigger_config: dict[str, Any] = Field(default_factory=dict)
    allow_reentry: bool = False
    cancellation_events: list[str] = Field(
        default_factory=lambda: ["contact.unsubscribed"]
    )


class WorkflowUpdate(BaseModel):
    """Estructura completa para guardar. El editor envía nodes + edges
    juntos; el backend reemplaza atómicamente."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    trigger_type: str | None = Field(default=None, min_length=1, max_length=80)
    trigger_config: dict[str, Any] | None = None
    allow_reentry: bool | None = None
    cancellation_events: list[str] | None = None
    steps: list[WorkflowStepWrite] | None = None
    edges: list[WorkflowEdgeWrite] | None = None


class WorkflowRead(BaseModel):
    id: str
    name: str
    description: str | None
    status: WorkflowStatus
    trigger_type: str
    trigger_config: dict[str, Any] = Field(default_factory=dict)
    allow_reentry: bool
    cancellation_events: list[str] = Field(default_factory=list)
    total_entered: int
    total_completed: int
    total_won: int
    total_cancelled: int
    total_failed: int
    created_by_user_id: str | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class WorkflowDetail(WorkflowRead):
    steps: list[WorkflowStepRead] = Field(default_factory=list)
    edges: list[WorkflowEdgeRead] = Field(default_factory=list)


# ---------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------


class WorkflowRunRead(BaseModel):
    id: str
    workflow_id: str
    workflow_name: str | None = None
    contact_id: str
    state: WorkflowRunState
    exit_kind: WorkflowExitKind | None
    current_step_id: str | None
    started_at: datetime
    completed_at: datetime | None
    wake_at: datetime | None
    error_summary: str | None

    model_config = ConfigDict(from_attributes=True)


class WorkflowRunHistoryRead(BaseModel):
    id: str
    step_id: str | None
    step_type: str
    status: str
    result: dict[str, Any] | None = None
    error_summary: str | None
    executed_at: datetime

    model_config = ConfigDict(from_attributes=True)


class WorkflowRunDetail(WorkflowRunRead):
    history: list[WorkflowRunHistoryRead] = Field(default_factory=list)


# ---------------------------------------------------------------------
# Activation + cost estimate
# ---------------------------------------------------------------------


class WorkflowActivateRequest(BaseModel):
    """Confirma que el operador ha visto el cost-estimate. El backend
    no fuerza el check (operadores pueden activar sin pasar por la UI)
    pero el audit log refleja si vino del modal."""

    acknowledged_estimate: bool = False


class WorkflowCostEstimate(BaseModel):
    matching_contacts_now: int
    estimated_runs_30d: int
    estimated_emails_30d: int
    estimated_tasks_30d: int
    validation_errors: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------
# Catalog — para el editor frontend
# ---------------------------------------------------------------------


class WorkflowCatalogResponse(BaseModel):
    triggers: list[dict[str, Any]]
    steps: list[dict[str, Any]]
    fields: list[str]
    variables: list[str]
