"""Unified entity filter/column schema + generic list/search endpoints.

PR-A shipped the schema surface. PR-B adds the generic list endpoint
`POST /api/entities/{entity}/search` (returns `{items, total, limit,
offset}`) and the matching id-enumeration sibling
`POST /api/entities/{entity}/search/ids` for "select all filtered"
flows. Both reuse `build_entity_filter` so the engine's anti-injection
boundary, NOT-with-arbitrary-nesting, and 24 comparators carry over
for free.

Sort keys are resolved through `EntityDescriptor.sort_column`, which
extends the whitelist to ordering (a caller can't sort on an unknown
column or one a spec marked non-sortable).

Contact stays on the legacy `POST /api/contacts/search` for now —
PR-E retires that one when the contacts UI migrates. The generic
endpoint works for every entity (including contact) but doesn't
implement the contact-specific extras (`assigned_to_me`, full
`ContactRead` projection with `tag_objects`); those happen in PR-E.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.auth import require_viewer
from app.core.errors import not_found
from app.db.session import get_session
from app.models.crm import User
from app.services.entities import (
    get_entity,
    list_entities,
    list_fields_for_entity,
)
from app.services.segments.engine import SegmentRuleError, build_entity_filter

router = APIRouter(prefix="/api/entities", tags=["entities"])

# Sprint Filtros & Listas — keep the "select all filtered" id-list cap
# in lock-step with the legacy `/api/contacts/search/ids` constant.
MAX_IDS = 10_000


# --- schemas -----------------------------------------------------


class EntitySearchRequest(BaseModel):
    rules_json: dict[str, Any] | None = None
    sort_by: str | None = None
    sort_dir: str = "desc"
    limit: int = Field(default=25, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


class EntitySearchPage(BaseModel):
    items: list[dict[str, Any]]
    total: int
    limit: int
    offset: int


class EntitySearchIdsResult(BaseModel):
    ids: list[str]
    count: int
    truncated: bool
    max_ids: int


# --- schema endpoints --------------------------------------------


@router.get("")
def list_registered_entities(
    current_user: User = Depends(require_viewer),
) -> list[dict[str, str]]:
    """List the entities that expose a unified filter/column schema."""
    _ = current_user
    out: list[dict[str, str]] = []
    for key in list_entities():
        descriptor = get_entity(key)
        if descriptor is None:  # pragma: no cover - registry is static
            continue
        out.append({"key": descriptor.key, "label": descriptor.label})
    return out


@router.get("/{entity}/filter-schema")
def entity_filter_schema(
    entity: str,
    current_user: User = Depends(require_viewer),
) -> dict[str, Any]:
    """Declarative field schema for one entity: drives the filter
    builder (filterable fields + comparators + enum/reference hints) and
    the column configurator (displayable + default_visible + grouped_under).
    """
    _ = current_user
    fields = list_fields_for_entity(entity)
    if fields is None:
        raise not_found("Entity")
    descriptor = get_entity(entity)
    assert descriptor is not None  # list_fields_for_entity already checked
    return {
        "entity": descriptor.key,
        "label": descriptor.label,
        "default_sort": descriptor.default_sort,
        "default_sort_dir": descriptor.default_sort_dir,
        "fields": fields,
    }


# --- search endpoints --------------------------------------------


def _resolve_sort(descriptor: Any, payload: EntitySearchRequest):
    """Resolve `(sort_by, sort_dir)` against the entity whitelist.
    Returns (column, dir) or raises 400 on an unknown/non-sortable key —
    the same anti-injection rule that the engine applies to filter
    fields, lifted to ordering."""
    sort_by = payload.sort_by or descriptor.default_sort
    sort_dir = (payload.sort_dir or descriptor.default_sort_dir).lower()
    if sort_dir not in {"asc", "desc"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid sort_dir {payload.sort_dir!r}",
        )
    column = descriptor.sort_column(sort_by)
    if column is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown or non-sortable field {sort_by!r}",
        )
    return column, sort_dir


def _segment_resolver_for(session: Session):
    """Closure factory for `in_segment` field. Sprint Filtros & Listas
    (PR-Cf): el motor levanta `SegmentRuleError("in_segment requires a
    segment_resolver")` si una rule usa `in_segment` y nadie pasa el
    resolver. El endpoint legacy `/api/contacts/search` ya lo hace —
    el genérico `/api/entities/{entity}/search` también debe.

    El resolver es Contact-only en la práctica (los segmentos son un
    concepto de contactos), pero pasarlo a cualquier entidad es
    inocuo porque sólo se invoca cuando una rule lo necesita.
    """
    import json as _json  # noqa: PLC0415

    from app.models.crm import Segment  # noqa: PLC0415

    def _resolver(segment_id: str, _visited: set[str]) -> dict[str, Any] | None:
        seg = session.get(Segment, segment_id)
        if seg is None or not seg.rules_json:
            return None
        try:
            return _json.loads(seg.rules_json)
        except (TypeError, ValueError):
            return None

    return _resolver


@router.post("/{entity}/search")
def entity_search(
    entity: str,
    payload: EntitySearchRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> EntitySearchPage:
    """Generic paginated list. The body's `rules_json` is the engine's IR
    tree (same shape as `POST /api/contacts/search`); empty body returns
    the entity universe."""
    _ = current_user
    descriptor = get_entity(entity)
    if descriptor is None:
        raise not_found("Entity")

    try:
        clause = build_entity_filter(
            descriptor,
            payload.rules_json or {},
            segment_resolver=_segment_resolver_for(session),
        )
    except SegmentRuleError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    column, sort_dir = _resolve_sort(descriptor, payload)
    order_by = column.asc() if sort_dir == "asc" else column.desc()

    total = session.scalar(
        select(func.count()).select_from(descriptor.base_model).where(clause)
    ) or 0

    stmt = (
        select(descriptor.base_model)
        .where(clause)
        .order_by(order_by)
        .offset(payload.offset)
        .limit(payload.limit)
    )
    # PR-Cd: apply per-entity loader paths so serialize_row doesn't N+1
    # on every list expansion (Contact's tag_objects is the obvious
    # one; other entities have an empty `eager_load_paths`). Each path
    # walks `(rel_name, rel_name, ...)` and resolves each step against
    # the previous step's target mapper so multi-hop chains
    # (`tag_assignments → tag`) become
    # `selectinload(Contact.tag_assignments).selectinload(ContactTag.tag)`.
    for path in descriptor.eager_load_paths:
        loader = None
        current_model = descriptor.base_model
        for attr_name in path:
            attr = getattr(current_model, attr_name)
            loader = (
                selectinload(attr)
                if loader is None
                else loader.selectinload(attr)
            )
            rel = current_model.__mapper__.relationships.get(attr_name)
            if rel is not None:
                current_model = rel.mapper.class_
        if loader is not None:
            stmt = stmt.options(loader)
    rows = list(session.scalars(stmt).unique())
    items = [descriptor.serialize_row(row) for row in rows]
    return EntitySearchPage(
        items=items, total=int(total), limit=payload.limit, offset=payload.offset
    )


@router.post("/{entity}/search/ids")
def entity_search_ids(
    entity: str,
    payload: EntitySearchRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> EntitySearchIdsResult:
    """Return matching ids only — feeds the "select all filtered" banner.
    Capped at MAX_IDS; truncation is reported back so the UI can warn
    the operator. (PR-C lands the proper set-based bulk that doesn't
    enumerate ids at all.)"""
    _ = current_user
    descriptor = get_entity(entity)
    if descriptor is None:
        raise not_found("Entity")

    try:
        clause = build_entity_filter(
            descriptor,
            payload.rules_json or {},
            segment_resolver=_segment_resolver_for(session),
        )
    except SegmentRuleError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    rows = list(
        session.scalars(
            select(descriptor.id_column).where(clause).limit(MAX_IDS + 1)
        )
    )
    truncated = len(rows) > MAX_IDS
    ids = [str(r) for r in rows[:MAX_IDS]]
    return EntitySearchIdsResult(
        ids=ids,
        count=len(ids),
        truncated=truncated,
        max_ids=MAX_IDS,
    )
