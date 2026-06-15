"""Unified entity filter/column schema endpoints (Sprint Filtros & Listas).

Serves the declarative `FieldDescriptor` list per entity so the
frontend `<EntityTable>` + `<EntityFilterBuilder>` build their column
configurator and filter dropdowns from one source of truth.

PR-A ships the schema surface only; the generic `/search` + `/bulk-action`
land in PR-B/PR-C. The Contact-specific `/api/segments/available-fields`
stays as-is for back-compat (it returns the same Contact field shape).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.core.auth import require_viewer
from app.core.errors import not_found
from app.models.crm import User
from app.services.entities import (
    get_entity,
    list_entities,
    list_fields_for_entity,
)

router = APIRouter(prefix="/api/entities", tags=["entities"])


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
