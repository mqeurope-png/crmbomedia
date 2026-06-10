"""Repository helpers for segments + their evaluation.

Lives outside `repositories/crm.py` because the engine is heavy and
we want the import graph in the contacts repo to stay tight.
"""
from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models.crm import Contact, ContactTag, Segment
from app.services.segments.engine import (
    SegmentRuleError,
    build_filter,
)


def list_segments(
    session: Session, *, user_id: str
) -> list[Segment]:
    statement = (
        select(Segment)
        .where(
            (Segment.owner_user_id == user_id) | (Segment.is_shared.is_(True))
        )
        .order_by(
            Segment.owner_user_id != user_id,  # own first
            Segment.name,
        )
    )
    return list(session.scalars(statement))


def get_segment(session: Session, segment_id: str) -> Segment | None:
    return session.get(Segment, segment_id)


def create_segment(
    session: Session,
    *,
    owner_user_id: str,
    name: str,
    description: str | None,
    rules: dict[str, Any],
    is_dynamic: bool,
    static_contact_ids: list[str] | None,
    is_shared: bool,
    color: str | None,
) -> Segment:
    segment = Segment(
        name=name,
        description=description,
        owner_user_id=owner_user_id,
        rules_json=_encode(rules),
        is_dynamic=is_dynamic,
        static_contact_ids=_encode(static_contact_ids),
        is_shared=is_shared,
        color=color,
    )
    session.add(segment)
    session.flush()
    return segment


def update_segment(
    session: Session,
    *,
    segment: Segment,
    name: str | None = None,
    description: str | None = None,
    rules: dict[str, Any] | None = None,
    is_dynamic: bool | None = None,
    static_contact_ids: list[str] | None = None,
    is_shared: bool | None = None,
    color: str | None = None,
) -> Segment:
    if name is not None:
        segment.name = name
    if description is not None:
        segment.description = description
    if is_dynamic is not None:
        segment.is_dynamic = is_dynamic
    if is_shared is not None:
        segment.is_shared = is_shared
    if color is not None:
        segment.color = color
    if rules is not None:
        segment.rules_json = _encode(rules)
    if static_contact_ids is not None:
        segment.static_contact_ids = _encode(static_contact_ids)
    session.flush()
    return segment


def delete_segment(session: Session, segment: Segment) -> None:
    session.delete(segment)


def duplicate_segment(
    session: Session, *, source: Segment, owner_user_id: str, name: str | None
) -> Segment:
    duplicate = Segment(
        name=name or f"{source.name} (copia)",
        description=source.description,
        owner_user_id=owner_user_id,
        rules_json=source.rules_json,
        is_dynamic=source.is_dynamic,
        static_contact_ids=source.static_contact_ids,
        is_shared=False,
        color=source.color,
    )
    session.add(duplicate)
    session.flush()
    return duplicate


def decode_rules(segment: Segment) -> dict[str, Any]:
    return _decode_dict(segment.rules_json)


def decode_static_ids(segment: Segment) -> list[str]:
    raw = _decode_any(segment.static_contact_ids)
    if isinstance(raw, list):
        return [str(item) for item in raw]
    return []


def evaluate_segment(
    session: Session, segment: Segment
) -> tuple[int, float]:
    """Run the segment, populate `cached_count` + `last_evaluated_at`,
    return `(count, duration_seconds)` so the caller can audit slow
    plans."""
    started = time.monotonic()
    if not segment.is_dynamic:
        ids = decode_static_ids(segment)
        count = len(ids)
    else:
        rules = decode_rules(segment)
        try:
            condition = build_filter(rules)
        except SegmentRuleError:
            condition = None
        if condition is None:
            count = 0
        else:
            count = int(
                session.scalar(
                    select(func.count()).select_from(Contact).where(condition)
                )
                or 0
            )
    duration = time.monotonic() - started
    segment.cached_count = count
    segment.last_evaluated_at = datetime.now(UTC)
    session.flush()
    return count, duration


def list_segment_contacts(
    session: Session,
    segment: Segment,
    *,
    skip: int = 0,
    limit: int = 25,
    sort_by: str = "created_at",
    sort_dir: str = "desc",
) -> tuple[list[Contact], int]:
    """Paginated read for `GET /api/segments/{id}/contacts`."""
    sort_columns = {
        "name": Contact.first_name,
        "email": Contact.email,
        "created_at": Contact.created_at,
        "updated_at": Contact.updated_at,
        "lead_score": Contact.lead_score,
    }
    sort_column = sort_columns.get(sort_by, Contact.created_at)
    order = sort_column.desc() if sort_dir.lower() == "desc" else sort_column.asc()

    if not segment.is_dynamic:
        ids = decode_static_ids(segment)
        if not ids:
            return [], 0
        statement = (
            select(Contact)
            .options(
                selectinload(Contact.tag_assignments).selectinload(ContactTag.tag)
            )
            .where(Contact.id.in_(ids))
            .order_by(order)
            .offset(skip)
            .limit(limit)
        )
        rows = list(session.scalars(statement))
        total = len(ids)
        return rows, total

    rules = decode_rules(segment)
    try:
        condition = build_filter(rules)
    except SegmentRuleError:
        return [], 0
    statement = (
        select(Contact)
        .options(
            selectinload(Contact.tag_assignments).selectinload(ContactTag.tag)
        )
        .where(condition)
        .order_by(order)
        .offset(skip)
        .limit(limit)
    )
    rows = list(session.scalars(statement))
    total = int(
        session.scalar(
            select(func.count()).select_from(Contact).where(condition)
        )
        or 0
    )
    return rows, total


def preview_rules(
    session: Session, rules: dict[str, Any], *, sample_size: int = 10
) -> tuple[int, list[Contact]]:
    """Used by the live preview in the builder: count + first N rows
    without persisting a segment. Same anti-injection guarantees —
    build_filter runs the whitelist before any SQL is generated."""
    try:
        condition = build_filter(rules)
    except SegmentRuleError:
        return 0, []
    count = int(
        session.scalar(
            select(func.count()).select_from(Contact).where(condition)
        )
        or 0
    )
    sample = list(
        session.scalars(
            select(Contact)
            .options(
                selectinload(Contact.tag_assignments).selectinload(ContactTag.tag)
            )
            .where(condition)
            .order_by(Contact.updated_at.desc())
            .limit(sample_size)
        )
    )
    return count, sample


def _encode(payload: Any) -> str | None:
    if payload is None:
        return None
    if isinstance(payload, (dict, list)) and not payload:
        return None
    return json.dumps(payload, ensure_ascii=False)


def _decode_any(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return None


def _decode_dict(value: str | None) -> dict[str, Any]:
    raw = _decode_any(value)
    return raw if isinstance(raw, dict) else {}
