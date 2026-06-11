"""Tasks endpoints — productivity layer.

CRUD + a couple of read-optimised helpers:

- `GET /api/tasks` — generic list, filterable by assignee / contact /
  status / due range.
- `GET /api/tasks/my-buckets` — overdue / today / tomorrow / later /
  no_date for the dashboard widget. Cheap because it caps each
  bucket and we never need a global count of "later".
- `GET /api/tasks/calendar?from=&to=` — a calendar slice used by the
  /tasks page; the contract is the same as the list but tighter
  bounds.
- `GET /api/contacts/{id}/tasks` — proxy into the list with the
  contact id pinned, lives here so it ships alongside the rest of
  the task surface.

Authorisation rules:
- Any signed-in user can READ tasks (viewer included). Hiding tasks
  from viewers would defeat the dashboard widget for that role.
- Only the assignee, the creator, or an admin/manager can MUTATE a
  task. Non-admin users can't reassign a task to someone else either.
"""
from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.core.audit import Action, record_event
from app.core.auth import require_user, require_viewer
from app.core.errors import not_found
from app.db.session import get_session
from app.integrations.google_calendar import service as google_service
from app.models.crm import (
    Task,
    TaskPriority,
    TaskStatus,
    User,
    UserRole,
)
from app.repositories import tasks as tasks_repository
from app.schemas.crm import (
    TaskBuckets,
    TaskCompleteResponse,
    TaskCreate,
    TaskListPage,
    TaskRead,
    TaskUpdate,
)

router = APIRouter(prefix="/api/tasks", tags=["crm"])
logger = logging.getLogger(__name__)


def _require_mutator(task: Task, current_user: User) -> None:
    """The creator, the current assignee, or an admin/manager can
    mutate. Everyone else is locked out."""
    if current_user.role in {UserRole.ADMIN, UserRole.MANAGER}:
        return
    if (
        current_user.id == task.assigned_user_id
        or current_user.id == task.created_by_user_id
    ):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="No tienes permiso para modificar esta tarea.",
    )


def _coerce_status(raw: str | None) -> TaskStatus | None:
    if raw is None:
        return None
    try:
        return TaskStatus(raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Estado de tarea desconocido: {raw!r}",
        ) from exc


def _coerce_priority(raw: str) -> TaskPriority:
    try:
        return TaskPriority(raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Prioridad desconocida: {raw!r}",
        ) from exc


@router.get("", response_model=TaskListPage)
def list_tasks_endpoint(
    assigned_user_id: str | None = Query(default=None),
    contact_id: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    due_from: datetime | None = Query(default=None, alias="from"),
    due_to: datetime | None = Query(default=None, alias="to"),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    order: str = Query(default="due_at"),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> TaskListPage:
    _ = current_user
    parsed_status = _coerce_status(status_filter)
    items = tasks_repository.list_tasks(
        session,
        assigned_user_id=assigned_user_id,
        contact_id=contact_id,
        status=parsed_status,
        due_from=due_from,
        due_to=due_to,
        skip=skip,
        limit=limit,
        order=order,
    )
    total = tasks_repository.count_tasks(
        session,
        assigned_user_id=assigned_user_id,
        statuses=[parsed_status] if parsed_status else None,
    )
    return TaskListPage(
        items=[TaskRead.model_validate(t) for t in items],
        total=total,
        limit=limit,
        offset=skip,
    )


@router.get("/my-buckets", response_model=TaskBuckets)
def my_buckets(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> TaskBuckets:
    """Dashboard widget feed: my open tasks grouped by urgency."""
    buckets = tasks_repository.buckets_for_user(session, current_user.id)
    total_open = tasks_repository.count_tasks(
        session,
        assigned_user_id=current_user.id,
        statuses=[TaskStatus.PENDING, TaskStatus.IN_PROGRESS],
    )
    return TaskBuckets(
        overdue=[TaskRead.model_validate(t) for t in buckets["overdue"]],
        today=[TaskRead.model_validate(t) for t in buckets["today"]],
        tomorrow=[TaskRead.model_validate(t) for t in buckets["tomorrow"]],
        later=[TaskRead.model_validate(t) for t in buckets["later"]],
        no_date=[TaskRead.model_validate(t) for t in buckets["no_date"]],
        total_open=total_open,
    )


@router.get("/calendar", response_model=list[TaskRead])
def calendar_slice(
    due_from: datetime = Query(alias="from"),
    due_to: datetime = Query(alias="to"),
    assigned_user_id: str | None = Query(default=None),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> list[TaskRead]:
    _ = current_user
    if due_to < due_from:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="`to` debe ser posterior a `from`.",
        )
    items = tasks_repository.list_tasks(
        session,
        assigned_user_id=assigned_user_id,
        due_from=due_from,
        due_to=due_to,
        limit=500,
    )
    return [TaskRead.model_validate(t) for t in items]


@router.get("/{task_id}", response_model=TaskRead)
def get_task_endpoint(
    task_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> TaskRead:
    _ = current_user
    task = tasks_repository.get_task(session, task_id)
    if task is None:
        raise not_found("Task")
    return TaskRead.model_validate(task)


@router.post(
    "",
    response_model=TaskRead,
    status_code=status.HTTP_201_CREATED,
)
def create_task_endpoint(
    payload: TaskCreate,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> TaskRead:
    assigned_user_id = payload.assigned_user_id or current_user.id
    assignee = tasks_repository.resolve_assignee(session, assigned_user_id)
    if assignee is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El usuario asignado no existe.",
        )
    if payload.contact_id and tasks_repository.resolve_contact(
        session, payload.contact_id
    ) is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El contacto asociado no existe.",
        )
    task = tasks_repository.create_task(
        session,
        title=payload.title,
        description=payload.description,
        due_at=payload.due_at,
        status=_coerce_status(payload.status) or TaskStatus.PENDING,
        priority=_coerce_priority(payload.priority),
        assigned_user_id=assigned_user_id,
        contact_id=payload.contact_id,
        company_id=payload.company_id,
        pipeline_stage_id=payload.pipeline_stage_id,
        created_by_user_id=current_user.id,
        reminder_minutes_before=payload.reminder_minutes_before,
    )
    record_event(
        session,
        action=Action.TASK_CREATED,
        target_type="task",
        target_id=task.id,
        actor=current_user,
        metadata={
            "title": task.title,
            "contact_id": task.contact_id,
            "assigned_user_id": task.assigned_user_id,
        },
        request=request,
    )
    if payload.sync_with_google_calendar:
        # Best-effort: a Google outage or a user without Google
        # connected must not block the task.
        google_service.sync_task_to_calendar(session, task)
    session.commit()
    session.refresh(task)
    return TaskRead.model_validate(task)


@router.patch("/{task_id}", response_model=TaskRead)
def update_task_endpoint(
    task_id: str,
    payload: TaskUpdate,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> TaskRead:
    task = tasks_repository.get_task(session, task_id)
    if task is None:
        raise not_found("Task")
    _require_mutator(task, current_user)
    changes = payload.model_dump(exclude_unset=True)
    # Non-admins cannot reassign — moving a task off your own
    # backlog requires admin/manager privilege.
    if (
        "assigned_user_id" in changes
        and changes["assigned_user_id"]
        and changes["assigned_user_id"] != task.assigned_user_id
        and current_user.role not in {UserRole.ADMIN, UserRole.MANAGER}
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo admin/manager puede reasignar tareas.",
        )
    # Pop the sync flag before the repo sees it — the repo only deals
    # with model columns.
    sync_flag = changes.pop("sync_with_google_calendar", None)
    tasks_repository.update_task(session, task=task, changes=changes)
    record_event(
        session,
        action=Action.TASK_UPDATED,
        target_type="task",
        target_id=task.id,
        actor=current_user,
        metadata={"changes": list(changes.keys())},
        request=request,
    )
    # Sync side effects:
    # - sync_flag True + no event yet → create the event.
    # - sync_flag False + event present → delete the event and
    #   clear `google_event_id`/`google_calendar_id`.
    # - sync_flag None (omitted) + event present → patch the
    #   existing event with the new fields.
    if sync_flag is True and not task.google_event_id:
        google_service.sync_task_to_calendar(session, task)
    elif sync_flag is False and task.google_event_id:
        google_service.delete_task_event(session, task)
        task.google_event_id = None
        task.google_calendar_id = None
        session.flush()
    elif task.google_event_id and task.google_calendar_id:
        google_service.update_task_event(session, task)
    session.commit()
    session.refresh(task)
    return TaskRead.model_validate(task)


@router.post("/{task_id}/complete", response_model=TaskCompleteResponse)
def complete_task_endpoint(
    task_id: str,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> TaskCompleteResponse:
    task = tasks_repository.get_task(session, task_id)
    if task is None:
        raise not_found("Task")
    _require_mutator(task, current_user)
    tasks_repository.complete_task(session, task)
    record_event(
        session,
        action=Action.TASK_COMPLETED,
        target_type="task",
        target_id=task.id,
        actor=current_user,
        metadata={"title": task.title},
        request=request,
    )
    session.commit()
    session.refresh(task)
    return TaskCompleteResponse(task=TaskRead.model_validate(task))


@router.delete("/{task_id}")
def delete_task_endpoint(
    task_id: str,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> dict[str, str]:
    task = tasks_repository.get_task(session, task_id)
    if task is None:
        raise not_found("Task")
    _require_mutator(task, current_user)
    record_event(
        session,
        action=Action.TASK_DELETED,
        target_type="task",
        target_id=task.id,
        actor=current_user,
        metadata={"title": task.title},
        request=request,
    )
    if task.google_event_id and task.google_calendar_id:
        # Drop the calendar event before the DB row goes away so we
        # still have the ids to dispatch the API call. Failures are
        # swallowed inside the service — the local delete proceeds.
        google_service.delete_task_event(session, task)
    tasks_repository.delete_task(session, task)
    session.commit()
    return {"message": "Task deleted"}
