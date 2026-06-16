"""Pydantic schemas para `/api/assignment-rules` (Sprint Reglas-Assign).

`conditions` se acepta como dict (árbol IR del motor de filtros) y se
serializa a TEXT al persistir. Misma convención que `contact_views.
filters_json` / `segments.rules_json`.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class AssignmentRuleWrite(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    is_active: bool = True
    priority: int = Field(default=100, ge=0)
    conditions: dict[str, Any] = Field(default_factory=dict)
    primary_user_id: str | None = None
    secondary_user_ids: list[str] = Field(default_factory=list)
    apply_to: Literal["unassigned_only", "all"] = "unassigned_only"
    override_existing: bool = False
    stop_on_match: bool = True


class AssignmentRuleRead(BaseModel):
    id: str
    name: str
    description: str | None
    is_active: bool
    priority: int
    conditions: dict[str, Any]
    primary_user_id: str | None
    secondary_user_ids: list[str]
    apply_to: str
    override_existing: bool
    stop_on_match: bool
    created_by_user_id: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AssignmentRuleDryRun(BaseModel):
    """Salida de `/api/assignment-rules/{id}/dry-run` y `/run`."""

    rule_id: str
    matched: int
    applied: int
    dry_run: bool = False
    auto_disabled: bool = False
    reason: str | None = None
    error: str | None = None
