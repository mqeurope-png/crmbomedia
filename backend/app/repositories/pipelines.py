"""Repository helpers for pipelines, stages, and contact assignments.

The route layer talks only to these helpers; cross-cutting invariants
(contiguous stage positions, single-stage-per-contact-per-pipeline,
writing a history row on every transition) live here so future
callers — webhooks, automations — can reuse them.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models.crm import (
    Contact,
    ContactPipelineStage,
    ContactStageHistory,
    Pipeline,
    PipelineStage,
)


def _ensure_tz(value: datetime) -> datetime:
    """SQLite drops the tzinfo on `DateTime(timezone=True)` columns;
    coerce to UTC-aware so arithmetic stays type-clean."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value

# ---------------------------------------------------------------------------
# Pipelines
# ---------------------------------------------------------------------------


def list_pipelines(
    session: Session, *, include_inactive: bool = False
) -> list[Pipeline]:
    statement = select(Pipeline).options(
        selectinload(Pipeline.stages)
    ).order_by(Pipeline.name)
    if not include_inactive:
        statement = statement.where(Pipeline.is_active.is_(True))
    return list(session.scalars(statement))


def get_pipeline(session: Session, pipeline_id: str) -> Pipeline | None:
    return session.scalar(
        select(Pipeline)
        .options(selectinload(Pipeline.stages))
        .where(Pipeline.id == pipeline_id)
    )


def contact_count(session: Session, pipeline_id: str) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(ContactPipelineStage)
            .where(
                ContactPipelineStage.pipeline_id == pipeline_id,
                ContactPipelineStage.is_archived.is_(False),
            )
        )
        or 0
    )


def create_pipeline(
    session: Session,
    *,
    owner_user_id: str,
    name: str,
    description: str | None,
    color: str | None,
    is_shared: bool,
    stages: list[dict[str, Any]],
) -> Pipeline:
    pipeline = Pipeline(
        name=name,
        description=description,
        color=color,
        owner_user_id=owner_user_id,
        is_shared=is_shared,
    )
    session.add(pipeline)
    session.flush()
    for index, stage in enumerate(stages):
        session.add(
            PipelineStage(
                pipeline_id=pipeline.id,
                name=stage["name"],
                description=stage.get("description"),
                color=stage.get("color"),
                position=index,
                is_won=bool(stage.get("is_won", False)),
                is_lost=bool(stage.get("is_lost", False)),
                target_days=stage.get("target_days"),
            )
        )
    session.flush()
    return pipeline


def update_pipeline(
    session: Session,
    *,
    pipeline: Pipeline,
    name: str | None = None,
    description: str | None = None,
    color: str | None = None,
    is_shared: bool | None = None,
    is_active: bool | None = None,
) -> Pipeline:
    if name is not None:
        pipeline.name = name
    if description is not None:
        pipeline.description = description
    if color is not None:
        pipeline.color = color
    if is_shared is not None:
        pipeline.is_shared = is_shared
    if is_active is not None:
        pipeline.is_active = is_active
    session.flush()
    return pipeline


def soft_delete_pipeline(session: Session, pipeline: Pipeline) -> None:
    pipeline.is_active = False
    session.flush()


def duplicate_pipeline(
    session: Session,
    *,
    source: Pipeline,
    owner_user_id: str,
    name: str | None,
    include_contacts: bool,
) -> Pipeline:
    """Clone a pipeline + its stages, optionally bringing the contact
    assignments along with their CURRENT stage (history is NOT copied
    — the duplicate starts fresh)."""
    new_pipeline = Pipeline(
        name=name or f"{source.name} (copia)",
        description=source.description,
        color=source.color,
        owner_user_id=owner_user_id,
        is_shared=source.is_shared,
    )
    session.add(new_pipeline)
    session.flush()

    stage_id_map: dict[str, str] = {}
    for stage in sorted(source.stages, key=lambda s: s.position):
        clone = PipelineStage(
            pipeline_id=new_pipeline.id,
            name=stage.name,
            description=stage.description,
            color=stage.color,
            position=stage.position,
            is_won=stage.is_won,
            is_lost=stage.is_lost,
            target_days=stage.target_days,
        )
        session.add(clone)
        session.flush()
        stage_id_map[stage.id] = clone.id

    if include_contacts:
        existing = list(
            session.scalars(
                select(ContactPipelineStage).where(
                    ContactPipelineStage.pipeline_id == source.id,
                    ContactPipelineStage.is_archived.is_(False),
                )
            )
        )
        now = datetime.now(UTC)
        for assignment in existing:
            new_assignment = ContactPipelineStage(
                contact_id=assignment.contact_id,
                pipeline_id=new_pipeline.id,
                stage_id=stage_id_map[assignment.stage_id],
                entered_stage_at=now,
                added_to_pipeline_at=now,
            )
            session.add(new_assignment)
            session.flush()
            session.add(
                ContactStageHistory(
                    contact_pipeline_stage_id=new_assignment.id,
                    from_stage_id=None,
                    to_stage_id=new_assignment.stage_id,
                    moved_at=now,
                    note=f"Duplicated from pipeline {source.id}",
                )
            )
    return new_pipeline


# ---------------------------------------------------------------------------
# Stages
# ---------------------------------------------------------------------------


def add_stage(
    session: Session,
    *,
    pipeline: Pipeline,
    name: str,
    description: str | None,
    color: str | None,
    is_won: bool,
    is_lost: bool,
    target_days: int | None,
    position: int | None,
) -> PipelineStage:
    """Append (or insert) a stage. When `position` is None the stage
    lands at the end; when set, downstream positions shift by one so
    the contiguous-0..N-1 invariant survives."""
    current = sorted(pipeline.stages, key=lambda s: s.position)
    if position is None or position >= len(current):
        new_position = len(current)
    else:
        new_position = max(0, position)
        # Two-pass shift to avoid tripping the UNIQUE(pipeline_id,
        # position) index on SQLite: park every affected row in a
        # high range, flush, then assign the real positions.
        offset = len(current) + 10
        for index, stage in enumerate(current[new_position:]):
            stage.position = offset + index
        session.flush()
        for index, stage in enumerate(current[new_position:]):
            stage.position = new_position + 1 + index
        session.flush()
    stage = PipelineStage(
        pipeline_id=pipeline.id,
        name=name,
        description=description,
        color=color,
        position=new_position,
        is_won=is_won,
        is_lost=is_lost,
        target_days=target_days,
    )
    session.add(stage)
    session.flush()
    return stage


def update_stage(
    session: Session,
    *,
    stage: PipelineStage,
    name: str | None = None,
    description: str | None = None,
    color: str | None = None,
    is_won: bool | None = None,
    is_lost: bool | None = None,
    target_days: int | None = None,
) -> PipelineStage:
    if name is not None:
        stage.name = name
    if description is not None:
        stage.description = description
    if color is not None:
        stage.color = color
    if is_won is not None:
        stage.is_won = is_won
    if is_lost is not None:
        stage.is_lost = is_lost
    if target_days is not None:
        stage.target_days = target_days
    session.flush()
    return stage


def delete_stage(
    session: Session,
    *,
    stage: PipelineStage,
    move_to_stage_id: str | None,
) -> int:
    """Delete a stage. If contacts still live there, the caller MUST
    pass `move_to_stage_id` to relocate them. Returns the count of
    relocated assignments (0 if there were none)."""
    affected = 0
    assignments = list(
        session.scalars(
            select(ContactPipelineStage).where(
                ContactPipelineStage.stage_id == stage.id,
                ContactPipelineStage.is_archived.is_(False),
            )
        )
    )
    if assignments:
        if not move_to_stage_id:
            raise StageHasContactsError(
                f"Stage has {len(assignments)} contact(s); pass move_to_stage_id"
            )
        target = session.get(PipelineStage, move_to_stage_id)
        if target is None or target.pipeline_id != stage.pipeline_id:
            raise StageHasContactsError("move_to_stage_id is not in the same pipeline")
        now = datetime.now(UTC)
        for assignment in assignments:
            duration = int(
                (now - _ensure_tz(assignment.entered_stage_at)).total_seconds()
            )
            session.add(
                ContactStageHistory(
                    contact_pipeline_stage_id=assignment.id,
                    from_stage_id=assignment.stage_id,
                    to_stage_id=target.id,
                    moved_at=now,
                    duration_seconds_in_previous_stage=duration,
                    note="Stage deleted",
                )
            )
            assignment.stage_id = target.id
            assignment.entered_stage_at = now
            affected += 1
        session.flush()
        # Force SQLAlchemy to drop its cached `stage.contact_assignments`
        # so the upcoming DELETE doesn't try to NULL the FK on rows we
        # just relocated.
        session.expire(stage, ["contact_assignments"])
    pipeline_id = stage.pipeline_id
    session.delete(stage)
    session.flush()
    _renormalize_positions(session, pipeline_id)
    return affected


def reorder_stages(
    session: Session,
    *,
    pipeline: Pipeline,
    stage_ids: list[str],
) -> list[PipelineStage]:
    """Apply the new order (must contain EVERY existing stage)."""
    current_ids = {stage.id for stage in pipeline.stages}
    requested_ids = set(stage_ids)
    if requested_ids != current_ids:
        raise InvalidStageOrderError(
            "Reorder must include every stage id exactly once"
        )
    by_id = {stage.id: stage for stage in pipeline.stages}
    # Two-pass write: temporarily push every position into the high
    # range so SQLite's UNIQUE doesn't trip while we assign the new
    # values in any order. MySQL is fine without but the cost is one
    # extra UPDATE — cheap.
    offset = len(stage_ids) + 1
    for index, stage_id in enumerate(stage_ids):
        by_id[stage_id].position = index + offset
    session.flush()
    for index, stage_id in enumerate(stage_ids):
        by_id[stage_id].position = index
    session.flush()
    return [by_id[stage_id] for stage_id in stage_ids]


def _renormalize_positions(session: Session, pipeline_id: str) -> None:
    stages = list(
        session.scalars(
            select(PipelineStage)
            .where(PipelineStage.pipeline_id == pipeline_id)
            .order_by(PipelineStage.position)
        )
    )
    offset = len(stages) + 1
    for index, stage in enumerate(stages):
        stage.position = index + offset
    session.flush()
    for index, stage in enumerate(stages):
        stage.position = index
    session.flush()


class StageHasContactsError(Exception):
    """Raised when trying to delete a stage that still has live
    `ContactPipelineStage` rows without supplying a `move_to_stage_id`."""


class InvalidStageOrderError(Exception):
    """Raised when the reorder request is missing or duplicating
    stages instead of just permuting the existing set."""


# ---------------------------------------------------------------------------
# Contact assignments
# ---------------------------------------------------------------------------


def get_assignment(
    session: Session, assignment_id: str
) -> ContactPipelineStage | None:
    return session.get(ContactPipelineStage, assignment_id)


def get_assignment_for_contact_pipeline(
    session: Session, *, contact_id: str, pipeline_id: str
) -> ContactPipelineStage | None:
    return session.scalar(
        select(ContactPipelineStage).where(
            ContactPipelineStage.contact_id == contact_id,
            ContactPipelineStage.pipeline_id == pipeline_id,
        )
    )


def add_contact_to_pipeline(
    session: Session,
    *,
    contact: Contact,
    pipeline: Pipeline,
    stage_id: str | None,
    note: str | None,
    moved_by_user_id: str | None,
) -> ContactPipelineStage:
    """Add the contact to the pipeline at `stage_id` (or position-0 by
    default). If they were archived in this pipeline before, the row
    is un-archived and a fresh history entry is written rather than
    creating a duplicate ContactPipelineStage."""
    existing = get_assignment_for_contact_pipeline(
        session, contact_id=contact.id, pipeline_id=pipeline.id
    )
    target_stage_id = stage_id
    if not target_stage_id:
        first_stage = next(
            iter(sorted(pipeline.stages, key=lambda s: s.position)), None
        )
        if first_stage is None:
            raise ValueError("Pipeline has no stages")
        target_stage_id = first_stage.id
    target_stage = session.get(PipelineStage, target_stage_id)
    if target_stage is None or target_stage.pipeline_id != pipeline.id:
        raise ValueError("stage does not belong to this pipeline")

    now = datetime.now(UTC)
    if existing is not None:
        existing.is_archived = False
        existing.stage_id = target_stage.id
        existing.entered_stage_at = now
        existing.notes = note
        session.flush()
        assignment = existing
    else:
        assignment = ContactPipelineStage(
            contact_id=contact.id,
            pipeline_id=pipeline.id,
            stage_id=target_stage.id,
            notes=note,
        )
        session.add(assignment)
        session.flush()

    session.add(
        ContactStageHistory(
            contact_pipeline_stage_id=assignment.id,
            from_stage_id=None,
            to_stage_id=target_stage.id,
            moved_by_user_id=moved_by_user_id,
            moved_at=now,
            note=note,
        )
    )
    session.flush()
    return assignment


def move_contact_to_stage(
    session: Session,
    *,
    assignment: ContactPipelineStage,
    new_stage_id: str,
    note: str | None,
    moved_by_user_id: str | None,
) -> ContactPipelineStage:
    """Idempotent: a move to the SAME stage is a no-op and skips the
    history row — UI double-clicks shouldn't pollute the history."""
    if assignment.stage_id == new_stage_id:
        return assignment
    target = session.get(PipelineStage, new_stage_id)
    if target is None or target.pipeline_id != assignment.pipeline_id:
        raise ValueError("stage does not belong to this pipeline")
    now = datetime.now(UTC)
    duration = int(
        (now - _ensure_tz(assignment.entered_stage_at)).total_seconds()
    )
    session.add(
        ContactStageHistory(
            contact_pipeline_stage_id=assignment.id,
            from_stage_id=assignment.stage_id,
            to_stage_id=new_stage_id,
            moved_by_user_id=moved_by_user_id,
            moved_at=now,
            duration_seconds_in_previous_stage=duration,
            note=note,
        )
    )
    assignment.stage_id = new_stage_id
    assignment.entered_stage_at = now
    assignment.last_activity_at = now
    session.flush()
    return assignment


def archive_assignment(
    session: Session, assignment: ContactPipelineStage
) -> ContactPipelineStage:
    assignment.is_archived = True
    session.flush()
    return assignment


def assignments_for_contact(
    session: Session, contact_id: str, *, include_archived: bool = False
) -> list[ContactPipelineStage]:
    statement = select(ContactPipelineStage).where(
        ContactPipelineStage.contact_id == contact_id
    )
    if not include_archived:
        statement = statement.where(ContactPipelineStage.is_archived.is_(False))
    return list(session.scalars(statement))


def list_contacts_grouped_by_stage(
    session: Session, pipeline: Pipeline, *, per_stage_limit: int = 50
) -> list[tuple[PipelineStage, list[tuple[ContactPipelineStage, Contact]], int]]:
    """Return one row per stage with up to `per_stage_limit` live
    assignments + the matching `Contact`. We do two queries per stage
    (one for the page, one for the total) and a single bulk lookup of
    contacts with their tag relationship eager-loaded for the chips."""
    from app.models.crm import ContactTag

    out: list[tuple[PipelineStage, list[tuple[ContactPipelineStage, Contact]], int]] = []
    for stage in sorted(pipeline.stages, key=lambda s: s.position):
        total = int(
            session.scalar(
                select(func.count())
                .select_from(ContactPipelineStage)
                .where(
                    ContactPipelineStage.stage_id == stage.id,
                    ContactPipelineStage.is_archived.is_(False),
                )
            )
            or 0
        )
        assignments = list(
            session.scalars(
                select(ContactPipelineStage)
                .where(
                    ContactPipelineStage.stage_id == stage.id,
                    ContactPipelineStage.is_archived.is_(False),
                )
                .order_by(ContactPipelineStage.entered_stage_at.desc())
                .limit(per_stage_limit)
            )
        )
        pairs: list[tuple[ContactPipelineStage, Contact]] = []
        if assignments:
            contact_ids = [a.contact_id for a in assignments]
            contacts = {
                c.id: c
                for c in session.scalars(
                    select(Contact)
                    .options(
                        selectinload(Contact.tag_assignments).selectinload(
                            ContactTag.tag
                        )
                    )
                    .where(Contact.id.in_(contact_ids))
                )
            }
            for assignment in assignments:
                contact = contacts.get(assignment.contact_id)
                if contact is not None:
                    pairs.append((assignment, contact))
        out.append((stage, pairs, total))
    return out


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


def compute_report(
    session: Session,
    pipeline: Pipeline,
    *,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
) -> dict[str, Any]:
    """Aggregate the basic metrics from `contact_stage_history` per
    stage. Conversion rate is naive (next-stage entries / this-stage
    entries) — Sprint P.3 will plug in a richer funnel model."""
    stages = sorted(pipeline.stages, key=lambda s: s.position)
    metrics: list[dict[str, Any]] = []
    total_contacts = contact_count(session, pipeline.id)
    won_count = 0
    lost_count = 0
    now = datetime.now(UTC)

    history_filter = []
    if from_date:
        history_filter.append(ContactStageHistory.moved_at >= from_date)
    if to_date:
        history_filter.append(ContactStageHistory.moved_at <= to_date)

    for index, stage in enumerate(stages):
        live_count = int(
            session.scalar(
                select(func.count())
                .select_from(ContactPipelineStage)
                .where(
                    ContactPipelineStage.stage_id == stage.id,
                    ContactPipelineStage.is_archived.is_(False),
                )
            )
            or 0
        )
        if stage.is_won:
            won_count += live_count
        if stage.is_lost:
            lost_count += live_count

        # Average duration spent in this stage (only entries that
        # subsequently moved on contribute).
        avg_seconds_stmt = (
            select(
                func.avg(ContactStageHistory.duration_seconds_in_previous_stage)
            )
            .where(ContactStageHistory.from_stage_id == stage.id)
        )
        for cond in history_filter:
            avg_seconds_stmt = avg_seconds_stmt.where(cond)
        avg_seconds = session.scalar(avg_seconds_stmt)

        conversion: float | None = None
        if index + 1 < len(stages):
            next_stage = stages[index + 1]
            entered = int(
                session.scalar(
                    select(func.count(ContactStageHistory.id)).where(
                        ContactStageHistory.from_stage_id == stage.id,
                        ContactStageHistory.to_stage_id == next_stage.id,
                    )
                )
                or 0
            )
            into_this_stage = int(
                session.scalar(
                    select(func.count(ContactStageHistory.id)).where(
                        ContactStageHistory.to_stage_id == stage.id
                    )
                )
                or 0
            )
            if into_this_stage > 0:
                conversion = entered / into_this_stage

        stalled = 0
        if stage.target_days:
            stalled = int(
                session.scalar(
                    select(func.count(ContactPipelineStage.id)).where(
                        ContactPipelineStage.stage_id == stage.id,
                        ContactPipelineStage.is_archived.is_(False),
                        ContactPipelineStage.entered_stage_at
                        < now.replace(microsecond=0)
                        - _days_to_timedelta(stage.target_days),
                    )
                )
                or 0
            )

        metrics.append(
            {
                "stage_id": stage.id,
                "stage_name": stage.name,
                "position": stage.position,
                "contact_count": live_count,
                "avg_seconds_in_stage": (
                    float(avg_seconds) if avg_seconds is not None else None
                ),
                "conversion_to_next": conversion,
                "stalled_count": stalled,
            }
        )

    return {
        "pipeline_id": pipeline.id,
        "pipeline_name": pipeline.name,
        "total_contacts": total_contacts,
        "won_count": won_count,
        "lost_count": lost_count,
        "metrics": metrics,
    }


def _days_to_timedelta(days: int):
    from datetime import timedelta as _td

    return _td(days=days)
