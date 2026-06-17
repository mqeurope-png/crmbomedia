"""Dashboard widget endpoints.

Mini-PR C Fase 3. Lightweight read-only endpoints that the front-end
calls once per widget render. Each one returns the smallest payload
that the widget needs — paginated where it matters, capped where it
doesn't (the dashboard never paginates).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session, selectinload

from app.core.auth import require_viewer
from app.db.session import get_session
from app.integrations.google_calendar import service as google_service
from app.integrations.google_calendar.client import (
    GoogleAuthExpiredError,
    GoogleCalendarClient,
)
from app.models.crm import (
    ActivityEvent,
    Contact,
    ContactAssignment,
    ContactPipelineStage,
    Pipeline,
    TaskStatus,
    User,
)
from app.repositories import tasks as tasks_repository
from app.schemas.crm import TaskRead

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


def _assigned_to_user_predicate(user_id: str):
    """Sprint Reglas-Assign PR-B. "Contactos del usuario" ahora incluye
    primary + secundarios — EXISTS sobre contact_assignments en vez del
    chequeo escalar contra el caché owner_user_id, que sólo cubre el
    primary."""
    return Contact.id.in_(
        select(ContactAssignment.contact_id).where(
            ContactAssignment.user_id == user_id
        )
    )


@router.get("/tasks-pending", response_model=list[TaskRead])
def tasks_pending(
    limit: int = Query(default=8, ge=1, le=50),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> list[TaskRead]:
    """Next N open tasks for the current user, ordered by due_at
    with no-date at the bottom — the same shape the /tasks page
    uses for its "today / tomorrow" buckets, trimmed for the
    dashboard."""
    items = tasks_repository.list_tasks(
        session,
        assigned_user_id=current_user.id,
        statuses=[TaskStatus.PENDING, TaskStatus.IN_PROGRESS],
        limit=limit,
        order="due_at",
    )
    return [TaskRead.model_validate(t) for t in items]


@router.get("/google-calendar-events")
def google_calendar_events(
    limit: int = Query(default=5, ge=1, le=25),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> dict[str, Any]:
    """Next events from the user's selected Google Calendar.

    Returns `{configured, connected, events}`. The widget decides
    which CTA to show based on the first two flags — no calendar
    call is attempted when either is False.
    """
    integration = google_service.get_integration(session, current_user.id)
    if integration is None or integration.selected_calendar_id is None:
        return {
            "connected": False,
            "events": [],
            "calendar_summary": None,
        }
    now = datetime.now(UTC)
    horizon = now + timedelta(days=14)
    try:
        client = GoogleCalendarClient(session, integration)
        service = client._build_service()  # noqa: SLF001 - internal facade
        response = (
            service.events()
            .list(
                calendarId=integration.selected_calendar_id,
                timeMin=now.isoformat(),
                timeMax=horizon.isoformat(),
                maxResults=limit,
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
    except GoogleAuthExpiredError:
        session.delete(integration)
        session.commit()
        return {
            "connected": False,
            "events": [],
            "calendar_summary": None,
        }
    except Exception:  # noqa: BLE001
        return {
            "connected": True,
            "events": [],
            "calendar_summary": integration.selected_calendar_summary,
        }
    events: list[dict[str, Any]] = []
    for item in response.get("items", []):
        start_obj = item.get("start", {})
        start = start_obj.get("dateTime") or start_obj.get("date")
        end_obj = item.get("end", {})
        end = end_obj.get("dateTime") or end_obj.get("date")
        events.append(
            {
                "id": item.get("id"),
                "summary": item.get("summary"),
                "start": start,
                "end": end,
                "html_link": item.get("htmlLink"),
                "all_day": "date" in start_obj,
            }
        )
    return {
        "connected": True,
        "events": events,
        "calendar_summary": integration.selected_calendar_summary,
    }


@router.get("/pipeline-summary")
def pipeline_summary(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> list[dict[str, Any]]:
    """Per active pipeline, contacts owned by the user grouped by
    stage. The widget shows one bar chart per pipeline.

    Fase 3 closing fix: the first version referenced
    `PipelineStage.is_archived` (which only exists on
    `ContactPipelineStage`) and `ContactPipelineStage.pipeline_stage_id`
    (the column is `stage_id`). Both raised AttributeError → 500.
    """
    pipelines = list(
        session.scalars(
            select(Pipeline)
            .where(Pipeline.is_active.is_(True))
            .options(selectinload(Pipeline.stages))
            .order_by(Pipeline.name.asc())
        )
    )
    if not pipelines:
        return []
    out: list[dict[str, Any]] = []
    for pipeline in pipelines:
        stages = sorted(pipeline.stages, key=lambda s: s.position)
        rows = session.execute(
            select(
                ContactPipelineStage.stage_id,
                func.count(Contact.id).label("contact_count"),
            )
            .join(Contact, Contact.id == ContactPipelineStage.contact_id)
            .where(
                ContactPipelineStage.pipeline_id == pipeline.id,
                ContactPipelineStage.is_archived.is_(False),
                _assigned_to_user_predicate(current_user.id),
                Contact.is_active.is_(True),
            )
            .group_by(ContactPipelineStage.stage_id)
        ).all()
        counts = {row.stage_id: int(row.contact_count) for row in rows}
        out.append(
            {
                "pipeline_id": pipeline.id,
                "pipeline_name": pipeline.name,
                "pipeline_color": pipeline.color,
                "stages": [
                    {
                        "id": stage.id,
                        "name": stage.name,
                        "color": stage.color,
                        "count": counts.get(stage.id, 0),
                    }
                    for stage in stages
                ],
            }
        )
    return out


@router.get("/upcoming-tasks", response_model=list[TaskRead])
def upcoming_tasks(
    limit: int = Query(default=8, ge=1, le=50),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> list[TaskRead]:
    """Próximas tareas del current_user con `due_at >= NOW()` ordenadas
    ascendente. Reemplaza al widget "Próximos eventos" del dashboard
    (PR-E2) — Bart quiere ver tareas CRM, no eventos Google Calendar.

    Filtra por `status` open (PENDING + IN_PROGRESS) y `assigned_user_
    id = current_user`.
    """
    from app.models.crm import Task  # noqa: PLC0415

    now = datetime.now(UTC)
    stmt = (
        select(Task)
        .where(
            Task.assigned_user_id == current_user.id,
            Task.status.in_([TaskStatus.PENDING, TaskStatus.IN_PROGRESS]),
            Task.due_at.isnot(None),
            Task.due_at >= now,
        )
        .order_by(Task.due_at.asc())
        .limit(limit)
        .options(selectinload(Task.contact))
    )
    rows = list(session.scalars(stmt))
    return [TaskRead.model_validate(t) for t in rows]


_PERIOD_TO_DAYS: dict[str, int] = {
    "7d": 7,
    "14d": 14,
    "30d": 30,
}


def _period_days(period: str | None, default: int = 14) -> int:
    return _PERIOD_TO_DAYS.get(period or "", default)


@router.get("/priority-leads")
def priority_leads(
    period: str = Query(default="14d", regex="^(7d|14d|30d)$"),
    limit: int = Query(default=10, ge=1, le=50),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> list[dict[str, Any]]:
    """Leads prioritarios del current_user — sustituye al widget legacy
    "Leads sin atender" (PR-E2). Criterio: contactos asignados al user
    que cumplan AL MENOS UNA condición dentro del `period`:

    - **Recién creados**: `Contact.created_at >= since`.
    - **Recién asignados**: `ContactAssignment.assigned_at >= since`
      para este user.
    - **Activo**: tienen al menos un `ActivityEvent` desde `since`.

    Devuelve los top N por la fecha de la señal más reciente, con un
    tag `reason ∈ {recent, assigned, active}` que la UI usa para el
    chip.
    """
    days = _period_days(period)
    since = datetime.now(UTC) - timedelta(days=days)

    # Subqueries de candidatos por motivo. Cada una sólo devuelve
    # `(contact_id, signal_at)` — luego mezclamos en Python para
    # ordenar por la más reciente entre las 3 señales.
    recent_created = (
        select(
            Contact.id.label("contact_id"),
            Contact.created_at.label("signal_at"),
        )
        .join(
            ContactAssignment,
            ContactAssignment.contact_id == Contact.id,
        )
        .where(
            ContactAssignment.user_id == current_user.id,
            Contact.is_active.is_(True),
            Contact.created_at >= since,
        )
    )
    recent_assigned = (
        select(
            ContactAssignment.contact_id.label("contact_id"),
            ContactAssignment.assigned_at.label("signal_at"),
        ).where(
            ContactAssignment.user_id == current_user.id,
            ContactAssignment.assigned_at >= since,
        )
    )
    recent_activity = (
        select(
            ActivityEvent.contact_id.label("contact_id"),
            func.max(ActivityEvent.occurred_at).label("signal_at"),
        )
        .join(
            ContactAssignment,
            ContactAssignment.contact_id == ActivityEvent.contact_id,
        )
        .where(
            ContactAssignment.user_id == current_user.id,
            ActivityEvent.occurred_at >= since,
        )
        .group_by(ActivityEvent.contact_id)
    )

    created_rows = list(session.execute(recent_created))
    assigned_rows = list(session.execute(recent_assigned))
    activity_rows = list(session.execute(recent_activity))

    # Merge en memoria — cada lead se queda con la señal más reciente.
    # `reason` prioriza activity > assigned > recent (más informativo
    # primero — "Activo" es mejor pista que "Recién asignado").
    best: dict[str, tuple[datetime, str]] = {}
    for cid, signal_at in activity_rows:
        if signal_at is None:
            continue
        best[cid] = (signal_at, "active")
    for cid, signal_at in assigned_rows:
        if signal_at is None:
            continue
        if cid not in best or signal_at > best[cid][0]:
            best[cid] = (signal_at, "assigned")
    for cid, signal_at in created_rows:
        if signal_at is None:
            continue
        if cid not in best:
            best[cid] = (signal_at, "recent")

    if not best:
        return []

    top_ids = sorted(best.items(), key=lambda x: x[1][0], reverse=True)[:limit]
    contact_rows = {
        c.id: c
        for c in session.scalars(
            select(Contact).where(Contact.id.in_([cid for cid, _ in top_ids]))
        )
    }

    out: list[dict[str, Any]] = []
    for cid, (signal_at, reason) in top_ids:
        c = contact_rows.get(cid)
        if c is None:
            continue
        out.append(
            {
                "id": c.id,
                "first_name": c.first_name,
                "last_name": c.last_name,
                "email": c.email,
                "phone": c.phone,
                "signal_at": signal_at,
                "reason": reason,
            }
        )
    return out


@router.get("/user-campaign-stats")
def user_campaign_stats(
    period: str = Query(default="30d", regex="^(7d|14d|30d)$"),
    limit: int = Query(default=5, ge=1, le=20),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> list[dict[str, Any]]:
    """Ranking de usuarios por leads conseguidos en campañas Brevo
    durante el `period`. PR-E2.

    "Lead conseguido" = contacto **asignado** al user que abrió o
    clickó al menos una campaña en el período. Devolvemos el top N
    users con `{user_id, full_name, email, leads, conversion_pct}`,
    donde `conversion_pct = (leads que clickaron / leads que
    abrieron) * 100`.

    Cualquier usuario puede llamar al endpoint — los datos son
    agregados por user. Útil tanto para el operador (ver su posición)
    como para el manager (cuadro de mandos).
    """
    _ = current_user
    days = _period_days(period, default=30)
    since = datetime.now(UTC) - timedelta(days=days)

    # 1 row por (user, contact, event_type) — luego agregamos.
    base = (
        select(
            ContactAssignment.user_id,
            ContactAssignment.contact_id,
            ActivityEvent.event_type,
        )
        .join(
            ActivityEvent,
            ActivityEvent.contact_id == ContactAssignment.contact_id,
        )
        .where(
            ActivityEvent.campaign_brevo_id.isnot(None),
            ActivityEvent.occurred_at >= since,
        )
    )
    rows = list(session.execute(base))

    per_user: dict[str, dict[str, set[str]]] = {}
    for user_id, contact_id, event_type in rows:
        slot = per_user.setdefault(
            user_id, {"opened": set(), "clicked": set()}
        )
        et = (event_type or "").lower()
        if "click" in et:
            slot["clicked"].add(contact_id)
            slot["opened"].add(contact_id)  # click implica open
        elif "open" in et:
            slot["opened"].add(contact_id)

    if not per_user:
        return []

    users_lookup = {
        u.id: u
        for u in session.scalars(
            select(User).where(User.id.in_(per_user.keys()))
        )
    }
    out: list[dict[str, Any]] = []
    for uid, slots in per_user.items():
        user = users_lookup.get(uid)
        if user is None:
            continue
        leads = len(slots["opened"])
        clicks = len(slots["clicked"])
        out.append(
            {
                "user_id": uid,
                "full_name": user.full_name,
                "email": user.email,
                "leads": leads,
                "clicks": clicks,
                "conversion_pct": round((clicks / leads) * 100, 1)
                if leads
                else 0.0,
            }
        )
    out.sort(key=lambda x: x["leads"], reverse=True)
    return out[:limit]


@router.get("/recent-interactions")
def recent_interactions(
    scope: Literal["mine", "team"] = Query(default="mine"),
    limit: int = Query(default=20, ge=1, le=50),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> list[dict[str, Any]]:
    """Timeline mixto de últimas interacciones de los contactos del
    user (`scope=mine`) o de todo el equipo (`scope=team`). PR-E2.

    Mezcla `activity_events` de tipos {email, call, note, task} en
    una sola línea de tiempo ordenada desc por `occurred_at`. La
    forma del row es paralela a `recent-email-activity` para reciclar
    el render del widget.
    """
    stmt = (
        select(ActivityEvent, Contact)
        .join(Contact, Contact.id == ActivityEvent.contact_id)
        .order_by(ActivityEvent.occurred_at.desc())
        .limit(limit)
    )
    if scope == "mine":
        stmt = stmt.where(_assigned_to_user_predicate(current_user.id))

    rows = list(session.execute(stmt))
    out: list[dict[str, Any]] = []
    for event, contact in rows:
        name = " ".join(
            [contact.first_name, contact.last_name or ""],
        ).strip() or contact.email
        out.append(
            {
                "id": event.id,
                "event_type": event.event_type,
                "subject": event.subject,
                "body": event.body,
                "occurred_at": event.occurred_at,
                "contact_id": contact.id,
                "contact_name": name,
                "contact_email": contact.email,
                "campaign_brevo_id": event.campaign_brevo_id,
            }
        )
    return out


@router.get("/unattended-leads")
def unattended_leads(
    limit: int = Query(default=10, ge=1, le=50),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> list[dict[str, Any]]:
    """Contacts marked `new` created in the last 14 days that nobody
    owns AND have no open task. The widget calls these "leads sin
    atender".

    Sprint Reglas-Assign PR-D: cambio de OR a AND. La versión legacy
    incluía cualquier contacto sin tareas abiertas, así que tras pulsar
    "Asignarme" en el widget el lead seguía apareciendo (no tenía
    tareas). Con multi-comercial el indicador correcto es: "no hay
    NINGUNA asignación". Las tareas abiertas dejan de ser señal de
    atención (las gestiona el widget de tareas separado).
    """
    _ = current_user
    since = datetime.now(UTC) - timedelta(days=14)
    stmt = (
        select(Contact)
        .where(
            Contact.is_active.is_(True),
            Contact.commercial_status == "new",
            Contact.created_at >= since,
            ~Contact.id.in_(select(ContactAssignment.contact_id)),
        )
        .order_by(Contact.created_at.desc())
        .limit(limit)
    )
    rows = list(session.scalars(stmt))
    return [
        {
            "id": c.id,
            "first_name": c.first_name,
            "last_name": c.last_name,
            "email": c.email,
            "phone": c.phone,
            "owner_user_id": c.owner_user_id,
            "created_at": c.created_at,
        }
        for c in rows
    ]


_RANGE_TO_DAYS: dict[str, int] = {
    "7d": 7,
    "30d": 30,
    "90d": 90,
}


@router.get("/leads-stats")
def leads_stats(
    range_: Literal["7d", "30d", "90d"] = Query(default="30d", alias="range"),
    bucket: Literal["day", "week", "month"] = Query(default="day"),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> dict[str, Any]:
    """Time-series of new leads + comparison with the previous
    equivalent window. Returns the buckets the chart needs along
    with the headline KPIs ("total this period", "vs previous",
    "% qualified", "% closed_won")."""
    _ = current_user
    days = _RANGE_TO_DAYS[range_]
    now = datetime.now(UTC)
    start = now - timedelta(days=days)
    prev_start = start - timedelta(days=days)

    # Bucketed counts in [start, now). Generic Python aggregation so
    # the same shape works on SQLite (CI) and MySQL (prod) without
    # dialect-specific date_trunc.
    contacts = list(
        session.scalars(
            select(Contact).where(
                Contact.created_at >= prev_start,
                Contact.created_at <= now,
            )
        )
    )

    def _bucket_key(at: datetime) -> str:
        if bucket == "day":
            return at.date().isoformat()
        if bucket == "week":
            iso = at.isocalendar()
            return f"{iso.year}-W{iso.week:02d}"
        return f"{at.year}-{at.month:02d}"

    def _as_aware(value: datetime) -> datetime:
        """MySQL DATETIME columns lose the tz on read even when the
        SQLAlchemy column is declared `timezone=True`. Normalise to
        UTC so comparing against `start`/`prev_start` doesn't blow
        up with `can't compare offset-naive and offset-aware`."""
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)

    series: dict[str, int] = {}
    qualified_now = 0
    closed_won_now = 0
    new_now = 0
    new_prev = 0
    for contact in contacts:
        created = _as_aware(contact.created_at)
        if created >= start:
            new_now += 1
            key = _bucket_key(created)
            series[key] = series.get(key, 0) + 1
            status = (contact.commercial_status or "").lower()
            if status in ("qualified", "qualified_lead", "qualified-lead"):
                qualified_now += 1
            if status in ("won", "closed_won"):
                closed_won_now += 1
        else:
            new_prev += 1

    return {
        "range": range_,
        "bucket": bucket,
        "series": [
            {"bucket": key, "count": series[key]}
            for key in sorted(series.keys())
        ],
        "totals": {
            "leads_current": new_now,
            "leads_previous": new_prev,
            "delta_pct": (
                round(((new_now - new_prev) / new_prev) * 100, 1)
                if new_prev
                else None
            ),
            "qualified_pct": (
                round((qualified_now / new_now) * 100, 1) if new_now else 0.0
            ),
            "closed_won_pct": (
                round((closed_won_now / new_now) * 100, 1) if new_now else 0.0
            ),
        },
    }


_EMAIL_EVENT_TYPES: tuple[str, ...] = (
    "email_sent",
    "email_opened",
    "email_clicked",
    "email_bounced",
    "email_unsubscribed",
    "EMAIL_SENT",
    "EMAIL_OPENED",
    "EMAIL_CLICKED",
)


@router.get("/recent-email-activity")
def recent_email_activity(
    limit: int = Query(default=15, ge=1, le=100),
    scope: Literal["mine", "all"] = Query(default="all"),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> list[dict[str, Any]]:
    """Latest email-related activity_events. `scope=mine` only
    keeps events whose contact has `owner_user_id == current_user`."""
    stmt = (
        select(ActivityEvent, Contact)
        .join(Contact, Contact.id == ActivityEvent.contact_id)
        .where(ActivityEvent.event_type.in_(_EMAIL_EVENT_TYPES))
    )
    if scope == "mine":
        stmt = stmt.where(_assigned_to_user_predicate(current_user.id))
    stmt = stmt.order_by(ActivityEvent.occurred_at.desc()).limit(limit)
    rows = list(session.execute(stmt).all())
    return [
        {
            "id": evt.id,
            "event_type": evt.event_type,
            "subject": evt.subject,
            "occurred_at": evt.occurred_at,
            "contact_id": contact.id,
            "contact_name": " ".join(
                p for p in (contact.first_name, contact.last_name) if p
            )
            or contact.email,
            "contact_email": contact.email,
        }
        for evt, contact in rows
    ]


# Imported at the bottom to avoid the unused-import lint when `and_`
# isn't needed in some future trimming.
_ = and_  # noqa: F841
