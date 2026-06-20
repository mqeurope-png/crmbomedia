"""Sprint Workflows — motor de automatización tipo HubSpot Workflows.

Modelos:

- `Workflow` — definición global (nombre, estado, settings, configuración
  del trigger raíz). Una fila por workflow lógico.
- `WorkflowStep` — cada nodo del grafo (tipo + config). `step_index`
  ordena pasos huérfanos; las conexiones reales viven en
  `WorkflowEdge`.
- `WorkflowEdge` — aristas del grafo. `branch_label` discrimina las
  salidas múltiples (condition → "true"/"false", switch → "case_X",
  wait_for_event → "matched"/"timeout").
- `WorkflowRun` — instancia por contacto. Estado, paso actual,
  `wake_at` para scheduler, `active_dedup_key` para el reentry
  guard, `split_buckets_json` para A/B determinístico.
- `WorkflowRunHistory` — append-only de cada acción ejecutada
  (decisión: tabla separada de `audit_logs` para no saturar la auditoría
  humana).
- `WorkflowEventWait` — runs esperando un evento concreto. El
  dispatcher lo consulta cuando llega ese tipo de evento.

Decisión clave de la conversación: los triggers se cuelgan
explícitamente desde los endpoints existentes (POST /contacts,
PATCH /contacts/X, /tasks, /opportunities). Nunca via SQLAlchemy
event listeners — no queremos que bulk imports AgileCRM disparen
workflows.
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.crm import Base, TimestampMixin, enum_values


class WorkflowStatus(StrEnum):
    """Estado del workflow como entidad (no del run)."""

    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


class WorkflowRunState(StrEnum):
    """Estado de una ejecución individual."""

    RUNNING = "running"
    WAITING = "waiting"
    WAITING_FOR_EVENT = "waiting_for_event"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    # Soft-cancel intermedio. El executor lo lee en cada step boundary y
    # transiciona a CANCELLED limpio. Evita race conditions al cancelar
    # un run con un step RQ ya en flight.
    CANCELLING = "cancelling"


class WorkflowExitKind(StrEnum):
    """Cómo terminó un run en estado COMPLETED."""

    NATURAL = "natural"
    WON = "won"
    LOST = "lost"
    TIMEOUT = "timeout"


class Workflow(TimestampMixin, Base):
    """Definición del workflow. Un workflow tiene exactamente UN trigger
    raíz codificado en `trigger_config_json`; los pasos cuelgan del
    primer `WorkflowStep` con `is_entry=True`."""

    __tablename__ = "workflows"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[WorkflowStatus] = mapped_column(
        Enum(
            WorkflowStatus,
            native_enum=False,
            values_callable=enum_values,
            length=16,
        ),
        default=WorkflowStatus.DRAFT,
        nullable=False,
        index=True,
    )
    # Tipo del trigger raíz. Determina qué dispatcher lo evalúa.
    trigger_type: Mapped[str] = mapped_column(
        String(80), nullable=False, index=True
    )
    # JSON con filtros del trigger + parámetros (cron preset, ventana
    # engagement, etc.). El esquema concreto vive en cada trigger handler.
    trigger_config_json: Mapped[str] = mapped_column(Text, default="{}")
    # Allow reentry: si False (default), el active_dedup_key bloquea
    # entradas concurrentes del mismo contacto.
    allow_reentry: Mapped[bool] = mapped_column(
        default=False, nullable=False
    )
    # Lista de events que cancelan automáticamente runs activos. Default
    # ["contact.unsubscribed"] vía seed en el handler.
    cancellation_events_json: Mapped[str] = mapped_column(
        Text, default='["contact.unsubscribed"]'
    )
    # Estadísticas materializadas — actualizadas por el executor para
    # evitar contar a cada GET de /workflows.
    total_entered: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_completed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_won: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_cancelled: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_failed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    # Sprint UX-Workflows-Editor. SHA-256 truncado de la definición
    # estructural (trigger_type + trigger_config + steps + edges) usado
    # para detectar duplicados exactos al guardar. NULL hasta que se
    # llame `recompute_definition_hash` en el save.
    definition_hash: Mapped[str | None] = mapped_column(String(64))


class WorkflowStep(TimestampMixin, Base):
    """Nodo del grafo. `type` discrimina la familia (trigger, wait,
    condition, action_*, exit_*). `config_json` lleva los parámetros
    específicos del tipo."""

    __tablename__ = "workflow_steps"
    __table_args__ = (
        Index("ix_workflow_steps_workflow_id", "workflow_id"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    workflow_id: Mapped[str] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(String(80), nullable=False)
    config_json: Mapped[str] = mapped_column(Text, default="{}")
    # Posición visual en el canvas — el editor React Flow lo lee y
    # persiste tal cual. El motor no lo usa.
    position_x: Mapped[float] = mapped_column(default=0.0, nullable=False)
    position_y: Mapped[float] = mapped_column(default=0.0, nullable=False)
    # El nodo raíz que un dispatcher conecta cuando el trigger matchea.
    # Exactamente uno por workflow tiene is_entry=True.
    is_entry: Mapped[bool] = mapped_column(default=False, nullable=False)
    # Sprint UX-Workflows-Editor. Nombre custom asignado por el
    # operador via doble-click en el canvas. NULL → el frontend
    # calcula el label con `humanizeStepConfig()`.
    display_name: Mapped[str | None] = mapped_column(String(120))


class WorkflowEdge(TimestampMixin, Base):
    """Arista del grafo. Discriminador `branch_label` permite múltiples
    salidas por nodo (condition: true/false; switch: case_X; wait: matched/timeout)."""

    __tablename__ = "workflow_edges"
    __table_args__ = (
        Index(
            "ix_workflow_edges_workflow_id_from",
            "workflow_id",
            "from_step_id",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    workflow_id: Mapped[str] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False
    )
    from_step_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_steps.id", ondelete="CASCADE"), nullable=False
    )
    to_step_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_steps.id", ondelete="CASCADE"), nullable=False
    )
    # "default" para nodos con una sola salida. Para condition: "true" /
    # "false". Switch: "case_0" / "case_1" / "default". Wait_for_event:
    # "matched" / "timeout".
    branch_label: Mapped[str] = mapped_column(
        String(40), default="default", nullable=False
    )


class WorkflowRun(TimestampMixin, Base):
    """Una instancia del workflow para un contacto concreto."""

    __tablename__ = "workflow_runs"
    __table_args__ = (
        # Reentry guard. Cuando el run termina sobrescribimos
        # active_dedup_key a "archived:{id}" para liberar el slot.
        UniqueConstraint(
            "active_dedup_key", name="uq_workflow_runs_dedup"
        ),
        Index(
            "ix_workflow_runs_scheduler",
            "state",
            "wake_at",
        ),
        Index(
            "ix_workflow_runs_contact",
            "contact_id",
            "state",
        ),
        Index(
            "ix_workflow_runs_workflow",
            "workflow_id",
            "state",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    workflow_id: Mapped[str] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False
    )
    contact_id: Mapped[str] = mapped_column(
        ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False
    )
    current_step_id: Mapped[str | None] = mapped_column(
        ForeignKey("workflow_steps.id", ondelete="SET NULL")
    )
    state: Mapped[WorkflowRunState] = mapped_column(
        Enum(
            WorkflowRunState,
            native_enum=False,
            values_callable=enum_values,
            length=24,
        ),
        default=WorkflowRunState.RUNNING,
        nullable=False,
    )
    exit_kind: Mapped[WorkflowExitKind | None] = mapped_column(
        Enum(
            WorkflowExitKind,
            native_enum=False,
            values_callable=enum_values,
            length=16,
        )
    )
    # Cuándo despierta el scheduler. NULL para runs ya terminados o en
    # ejecución continua.
    wake_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    # Slot único para reentry guard. Activo:
    # `f"{workflow_id}:{contact_id}"` (no permite reentry simultáneo).
    # Terminado: `f"archived:{run_id}"` (libera el slot).
    active_dedup_key: Mapped[str] = mapped_column(
        # PR-Fix-Dedup-Key-Varchar. Antes VARCHAR(80) cubría
        # `{workflow_id}:{contact_id}` (73 chars) pero no la variante
        # con run_id `{workflow_id}:{contact_id}:{run_id}` (110 chars)
        # que usa la entrada manual con skip_dedup. 120 deja margen.
        String(120), nullable=False
    )
    # Estado de A/B splits + memoizaciones varias. Mapeado como
    # `{step_id: bucket}` para A/B.
    split_buckets_json: Mapped[str] = mapped_column(Text, default="{}")
    # JSON con el payload del evento que disparó el run (campos del
    # trigger accesibles vía `{{ trigger.* }}` en plantillas).
    trigger_payload_json: Mapped[str] = mapped_column(Text, default="{}")
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    error_summary: Mapped[str | None] = mapped_column(Text)


class WorkflowRunHistory(TimestampMixin, Base):
    """Append-only log de cada acción ejecutada por el workflow run.
    Tabla separada de `audit_logs` para no saturarla — Bart estimó
    +150k entries/mes a volumen Bomedia."""

    __tablename__ = "workflow_run_history"
    __table_args__ = (
        Index(
            "ix_workflow_run_history_run",
            "run_id",
            "executed_at",
        ),
        Index(
            "ix_workflow_run_history_contact",
            "contact_id",
            "executed_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    run_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False
    )
    workflow_id: Mapped[str] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False
    )
    contact_id: Mapped[str] = mapped_column(
        ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False
    )
    step_id: Mapped[str | None] = mapped_column(
        ForeignKey("workflow_steps.id", ondelete="SET NULL")
    )
    # Tipo del step ejecutado para auditoría sin necesidad de join.
    step_type: Mapped[str] = mapped_column(String(80), nullable=False)
    # "ok" / "skipped" / "failed" / "deferred".
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    # JSON con el resultado (email enviado, tag añadido, etc.) + razón
    # de skip / defer cuando aplica.
    result_json: Mapped[str | None] = mapped_column(Text)
    error_summary: Mapped[str | None] = mapped_column(Text)
    executed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class WorkflowEventWait(TimestampMixin, Base):
    """Runs esperando que ocurra un evento específico antes de avanzar.
    El dispatcher consulta esta tabla cuando llega un evento del tipo
    referenciado y matchea los criterios del wait."""

    __tablename__ = "workflow_event_waits"
    __table_args__ = (
        Index(
            "ix_workflow_event_waits_event",
            "event_type",
            "timeout_at",
        ),
        Index(
            "ix_workflow_event_waits_run",
            "run_id",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    run_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False
    )
    workflow_id: Mapped[str] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False
    )
    contact_id: Mapped[str] = mapped_column(
        ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False
    )
    step_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_steps.id", ondelete="CASCADE"), nullable=False
    )
    # Tipo del evento esperado: "email.crm.opened", "contact.updated", etc.
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    # JSON con condiciones adicionales sobre el evento (link específico,
    # campo concreto, etc.).
    condition_json: Mapped[str | None] = mapped_column(Text)
    # Cuando expira el wait. El scheduler también lo recoge para
    # resumir la rama de timeout.
    timeout_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
