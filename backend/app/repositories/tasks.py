"""Tasks repository — CRUD + activity-event mirrors.

Every mutation that observably changes the task (creation, completion,
due-date change, reassignment) writes an `activity_events` row when
the task is attached to a contact, so the contact's timeline picks
the change up alongside emails, notes and pipeline moves. Mutations
without a contact stay invisible to the activity feed but still
audit.

The repository owns the "completed_at follows status" invariant so
both the PATCH and the dedicated `complete` endpoint can route here
instead of duplicating it at the route layer.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session, selectinload

from app.models.crm import (
    ActivityEvent,
    Contact,
    Task,
    TaskPriority,
    TaskStatus,
    User,
)


def list_tasks(
    session: Session,
    *,
    assigned_user_id: str | None = None,
    contact_id: str | None = None,
    status: TaskStatus | None = None,
    statuses: list[TaskStatus] | None = None,
    due_from: datetime | None = None,
    due_to: datetime | None = None,
    skip: int = 0,
    limit: int = 50,
    order: str = "due_at",
) -> list[Task]:
    stmt = select(Task).options(
        selectinload(Task.assigned_user),
        selectinload(Task.contact),
    )
    if assigned_user_id:
        stmt = stmt.where(Task.assigned_user_id == assigned_user_id)
    if contact_id:
        stmt = stmt.where(Task.contact_id == contact_id)
    if status is not None:
        stmt = stmt.where(Task.status == status)
    if statuses:
        stmt = stmt.where(Task.status.in_(statuses))
    if due_from:
        stmt = stmt.where(Task.due_at >= due_from)
    if due_to:
        stmt = stmt.where(Task.due_at <= due_to)
    if order == "due_at":
        # `due_at IS NULL` last so a backlog without a date doesn't
        # crowd the "next" widget.
        stmt = stmt.order_by(Task.due_at.is_(None), Task.due_at.asc())
    else:
        stmt = stmt.order_by(Task.created_at.desc())
    return list(session.scalars(stmt.offset(skip).limit(limit)))


def count_tasks(
    session: Session,
    *,
    assigned_user_id: str | None = None,
    statuses: list[TaskStatus] | None = None,
) -> int:
    stmt = select(func.count()).select_from(Task)
    if assigned_user_id:
        stmt = stmt.where(Task.assigned_user_id == assigned_user_id)
    if statuses:
        stmt = stmt.where(Task.status.in_(statuses))
    return int(session.scalar(stmt) or 0)


def get_task(session: Session, task_id: str) -> Task | None:
    return session.get(Task, task_id)


def create_task(
    session: Session,
    *,
    title: str,
    description: str | None,
    due_at: datetime | None,
    status: TaskStatus,
    priority: TaskPriority,
    assigned_user_id: str,
    contact_id: str | None,
    company_id: str | None,
    pipeline_stage_id: str | None,
    created_by_user_id: str,
    reminder_minutes_before: int | None,
) -> Task:
    task = Task(
        title=title.strip(),
        description=(description or None),
        due_at=due_at,
        status=status,
        priority=priority,
        assigned_user_id=assigned_user_id,
        contact_id=contact_id,
        company_id=company_id,
        pipeline_stage_id=pipeline_stage_id,
        created_by_user_id=created_by_user_id,
        reminder_minutes_before=reminder_minutes_before,
    )
    session.add(task)
    session.flush()
    _emit_activity(
        session,
        task=task,
        event_type="task.created",
        body=task.title,
        extra={
            "due_at": task.due_at.isoformat() if task.due_at else None,
            "priority": task.priority.value,
            "assigned_user_id": task.assigned_user_id,
        },
    )
    return task


def update_task(
    session: Session,
    *,
    task: Task,
    changes: dict[str, Any],
) -> Task:
    """Apply a partial update + emit the relevant activity events.

    Recognised keys: title, description, due_at, status, priority,
    assigned_user_id, contact_id, company_id, pipeline_stage_id,
    reminder_minutes_before. Unknown keys are ignored silently — the
    schema layer is the gatekeeper, the repository is permissive."""
    activities: list[tuple[str, str | None, dict[str, Any]]] = []
    previous_status = task.status
    previous_due = task.due_at
    previous_assignee = task.assigned_user_id

    if "title" in changes and changes["title"] is not None:
        task.title = changes["title"].strip()
    if "description" in changes:
        task.description = changes["description"] or None
    if "due_at" in changes:
        task.due_at = changes["due_at"]
    if "status" in changes and changes["status"] is not None:
        task.status = TaskStatus(changes["status"])
    if "priority" in changes and changes["priority"] is not None:
        task.priority = TaskPriority(changes["priority"])
    if "assigned_user_id" in changes and changes["assigned_user_id"]:
        task.assigned_user_id = changes["assigned_user_id"]
    if "contact_id" in changes:
        task.contact_id = changes["contact_id"]
    if "company_id" in changes:
        task.company_id = changes["company_id"]
    if "pipeline_stage_id" in changes:
        task.pipeline_stage_id = changes["pipeline_stage_id"]
    if "reminder_minutes_before" in changes:
        task.reminder_minutes_before = changes["reminder_minutes_before"]
    if "google_event_id" in changes:
        task.google_event_id = changes["google_event_id"]
    if "google_calendar_id" in changes:
        task.google_calendar_id = changes["google_calendar_id"]

    # Completed_at follows status: setting status=done stamps it,
    # moving away clears it. The route layer doesn't have to remember
    # this invariant.
    if task.status == TaskStatus.DONE and previous_status != TaskStatus.DONE:
        task.completed_at = datetime.now(UTC)
        activities.append(
            ("task.completed", task.title, {"task_id": task.id})
        )
    elif task.status != TaskStatus.DONE and previous_status == TaskStatus.DONE:
        task.completed_at = None
        activities.append(
            ("task.reopened", task.title, {"task_id": task.id})
        )

    if previous_due != task.due_at and task.status != TaskStatus.DONE:
        activities.append(
            (
                "task.due_changed",
                task.title,
                {
                    "task_id": task.id,
                    "from": previous_due.isoformat() if previous_due else None,
                    "to": task.due_at.isoformat() if task.due_at else None,
                },
            )
        )
    if previous_assignee != task.assigned_user_id:
        activities.append(
            (
                "task.assigned_changed",
                task.title,
                {
                    "task_id": task.id,
                    "from": previous_assignee,
                    "to": task.assigned_user_id,
                },
            )
        )

    session.flush()
    for event_type, body, extra in activities:
        _emit_activity(
            session, task=task, event_type=event_type, body=body, extra=extra
        )
    return task


def complete_task(session: Session, task: Task) -> Task:
    return update_task(session, task=task, changes={"status": "done"})


def delete_task(session: Session, task: Task) -> None:
    # Emit a final activity event BEFORE deleting so the contact
    # timeline keeps the trace.
    _emit_activity(
        session,
        task=task,
        event_type="task.deleted",
        body=task.title,
        extra={"task_id": task.id},
    )
    session.delete(task)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _emit_activity(
    session: Session,
    *,
    task: Task,
    event_type: str,
    body: str | None,
    extra: dict[str, Any],
) -> None:
    """Mirror a task mutation as an `activity_events` row on the
    contact. Tasks without a contact are skipped — the audit log
    already records the action separately."""
    if not task.contact_id:
        return
    session.add(
        ActivityEvent(
            contact_id=task.contact_id,
            system="crm",
            account_id="tasks",
            external_id=f"task:{task.id}:{event_type}:{datetime.now(UTC).timestamp()}",
            event_type=event_type,
            subject=task.title[:200],
            body=body[:200] if body else None,
            metadata_json=json.dumps(extra, default=str),
            occurred_at=datetime.now(UTC),
            synced_at=datetime.now(UTC),
        )
    )


def resolve_assignee(session: Session, user_id: str) -> User | None:
    return session.get(User, user_id)


def resolve_contact(session: Session, contact_id: str | None) -> Contact | None:
    if not contact_id:
        return None
    return session.get(Contact, contact_id)


def buckets_for_user(
    session: Session,
    user_id: str | None,
    *,
    limit_per_bucket: int = 25,
) -> dict[str, list[Task]]:
    """Group open tasks into "overdue / today / tomorrow / later /
    no_date" buckets for the dashboard widget.

    QoL sprint — `user_id=None` devuelve los buckets del EQUIPO entero
    (filtro `assigned_user_id` desaparece). Esto es lo que pinta el
    toggle "Todo el equipo" del manager.
    """
    from datetime import timedelta as _timedelta  # noqa: PLC0415

    now = datetime.now(UTC)
    today_end = now.replace(hour=23, minute=59, second=59, microsecond=0)
    tomorrow_end = today_end + _timedelta(days=1)
    open_statuses = [TaskStatus.PENDING, TaskStatus.IN_PROGRESS]
    base = select(Task).where(Task.status.in_(open_statuses))
    if user_id is not None:
        base = base.where(Task.assigned_user_id == user_id)

    overdue = list(
        session.scalars(
            base.where(and_(Task.due_at.is_not(None), Task.due_at < now))
            .order_by(Task.due_at.asc())
            .limit(limit_per_bucket)
        )
    )
    today = list(
        session.scalars(
            base.where(and_(Task.due_at >= now, Task.due_at <= today_end))
            .order_by(Task.due_at.asc())
            .limit(limit_per_bucket)
        )
    )
    tomorrow = list(
        session.scalars(
            base.where(and_(Task.due_at > today_end, Task.due_at <= tomorrow_end))
            .order_by(Task.due_at.asc())
            .limit(limit_per_bucket)
        )
    )
    later = list(
        session.scalars(
            base.where(Task.due_at > tomorrow_end)
            .order_by(Task.due_at.asc())
            .limit(limit_per_bucket)
        )
    )
    no_date = list(
        session.scalars(
            base.where(Task.due_at.is_(None))
            .order_by(Task.created_at.desc())
            .limit(limit_per_bucket)
        )
    )
    return {
        "overdue": overdue,
        "today": today,
        "tomorrow": tomorrow,
        "later": later,
        "no_date": no_date,
    }
