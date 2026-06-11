"""Whitelist of fields + comparators the segment rule engine accepts.

This is the **anti-injection boundary**. Any field name or
comparator that doesn't appear here is rejected before the engine
attempts to build SQL — the operator's `rules_json` is never
trusted to name a column directly.

Each `FieldSpec` carries the human label shown in the UI, the
contact-table column reference (or relationship hint for joins) and
the list of comparators valid for the field's value type.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.models.crm import (
    Contact,
    ContactPipelineStage,
    ContactTag,
)


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    type: str  # string | int | bool | date | enum | tag-multi | uuid-multi
    comparators: tuple[str, ...]
    column: Any | None = None
    enum_values: tuple[str, ...] = ()
    # When the field requires a join (tags / pipelines), the engine
    # follows this hint to build an `EXISTS (subquery)` predicate so
    # the outer Contact query stays distinct-free.
    relation: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)


_COMMON_STRING = ("contains", "not_contains", "starts_with", "eq", "neq")
_COMMON_NULLABLE = ("is_null", "is_not_null")
_NUMERIC = ("eq", "neq", "gt", "gte", "lt", "lte", "between", "is_null")
_DATE = (
    "before",
    "after",
    "between",
    "in_last_n_days",
    "not_in_last_n_days",
    "is_null",
    "is_not_null",
)

FIELD_SPECS: dict[str, FieldSpec] = {
    "name": FieldSpec(
        key="name",
        label="Nombre completo",
        type="string",
        comparators=_COMMON_STRING,
        # "name" maps to first_name + last_name concatenation. The
        # engine handles it specially.
        extras={"concat": ("first_name", "last_name")},
    ),
    "email": FieldSpec(
        key="email",
        label="Email",
        type="string",
        comparators=("contains", "eq", "neq", *_COMMON_NULLABLE),
        column=Contact.email,
    ),
    "phone": FieldSpec(
        key="phone",
        label="Teléfono",
        type="string",
        comparators=("contains", "eq", "is_null"),
        column=Contact.phone,
    ),
    "tags": FieldSpec(
        key="tags",
        label="Tags",
        type="tag-multi",
        comparators=("contains_any", "contains_all", "contains_none"),
        relation="tags",
    ),
    "origin_system": FieldSpec(
        key="origin_system",
        label="Sistema de origen",
        type="enum",
        comparators=("eq", "neq", "in", "not_in"),
        enum_values=("agilecrm", "brevo", "freshdesk", "factusol", "manual"),
        relation="external_refs.system",
    ),
    "origin_account_id": FieldSpec(
        key="origin_account_id",
        label="Cuenta de origen",
        type="string",
        comparators=("eq", "neq", "in"),
        relation="external_refs.account_id",
    ),
    "commercial_status": FieldSpec(
        key="commercial_status",
        label="Estado comercial",
        type="enum",
        comparators=("eq", "neq", "in"),
        enum_values=("new", "qualified", "won", "lost"),
        column=Contact.commercial_status,
    ),
    "marketing_consent": FieldSpec(
        key="marketing_consent",
        label="Consentimiento marketing",
        type="enum",
        comparators=("eq", "neq", "in"),
        enum_values=("granted", "denied", "unknown", "unsubscribed"),
        column=Contact.marketing_consent,
    ),
    "is_active": FieldSpec(
        key="is_active",
        label="Activo",
        type="bool",
        comparators=("eq",),
        column=Contact.is_active,
    ),
    "lead_score": FieldSpec(
        key="lead_score",
        label="Lead score",
        type="int",
        comparators=_NUMERIC,
        column=Contact.lead_score,
    ),
    "address_country": FieldSpec(
        key="address_country",
        label="País (dirección)",
        type="string",
        comparators=("eq", "neq", "in", "is_null"),
        column=Contact.address_country,
    ),
    "created_at": FieldSpec(
        key="created_at",
        label="Fecha creación",
        type="date",
        comparators=_DATE,
        column=Contact.created_at,
    ),
    "updated_at": FieldSpec(
        key="updated_at",
        label="Última modificación",
        type="date",
        comparators=_DATE,
        column=Contact.updated_at,
    ),
    "external_data_refreshed_at": FieldSpec(
        key="external_data_refreshed_at",
        label="Última actualización externa",
        type="date",
        comparators=_DATE,
        column=Contact.external_data_refreshed_at,
    ),
    "created_at_external": FieldSpec(
        key="created_at_external",
        label="Creado en origen",
        type="date",
        comparators=_DATE,
        column=Contact.created_at_external,
    ),
    "updated_at_external": FieldSpec(
        key="updated_at_external",
        label="Última modificación en origen",
        type="date",
        comparators=_DATE,
        column=Contact.updated_at_external,
    ),
    "in_segment": FieldSpec(
        key="in_segment",
        label="En segmento",
        type="uuid-multi",
        comparators=("in", "not_in"),
        relation="segment_membership",
    ),
    "in_brevo_list": FieldSpec(
        key="in_brevo_list",
        label="En lista Brevo",
        type="uuid-multi",
        comparators=("in", "not_in"),
        relation="brevo_list_membership",
    ),
    "pipeline_id": FieldSpec(
        key="pipeline_id",
        label="En pipeline",
        type="uuid-multi",
        comparators=("in", "not_in"),
        relation="pipeline_id",
    ),
    "pipeline_stage_id": FieldSpec(
        key="pipeline_stage_id",
        label="En etapa de pipeline",
        type="uuid-multi",
        comparators=("in", "not_in"),
        relation="pipeline_stage_id",
    ),
}


def get_field_spec(field_key: str) -> FieldSpec | None:
    return FIELD_SPECS.get(field_key)


def list_fields_for_ui() -> list[dict[str, Any]]:
    """Shape consumed by `GET /api/segments/available-fields`. The
    builder UI uses it to render the dropdowns; the AI prompt
    serialises it into the system prompt so Claude only sees fields
    it can actually use."""
    return [
        {
            "key": spec.key,
            "label": spec.label,
            "type": spec.type,
            "comparators": list(spec.comparators),
            "enum_values": list(spec.enum_values),
        }
        for spec in FIELD_SPECS.values()
    ]


def validate_value(spec: FieldSpec, comparator: str, value: Any) -> Any:
    """Coerce / validate the raw `value` from the rules tree. Raises
    `ValueError` on anything that doesn't match the field type — the
    engine maps that to a 400 before any SQL is generated.

    Lists are allowed for the `*in*` / multi-value comparators. The
    booleans / ints are normalised so the comparator doesn't receive
    strings from a JSON payload.
    """
    if comparator in {"is_null", "is_not_null"}:
        return None
    if comparator in {"in", "not_in", "contains_any", "contains_all", "contains_none"}:
        if not isinstance(value, list) or not value:
            raise ValueError(f"Comparator {comparator!r} requires a non-empty list")
        return [_coerce_scalar(spec, item) for item in value]
    if comparator == "between":
        if not isinstance(value, list) or len(value) != 2:
            raise ValueError("between requires a 2-element list")
        return [_coerce_scalar(spec, item) for item in value]
    return _coerce_scalar(spec, value)


def _coerce_scalar(spec: FieldSpec, value: Any) -> Any:
    if spec.type == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in {"true", "1", "yes"}
        return bool(value)
    if spec.type == "int":
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Expected int for {spec.key}") from exc
    if spec.type == "enum":
        sval = str(value)
        if spec.enum_values and sval not in spec.enum_values:
            raise ValueError(
                f"Unknown enum value {sval!r} for {spec.key}"
            )
        return sval
    if spec.type == "date":
        from datetime import datetime as _dt

        if isinstance(value, (int, float)):
            return int(value)
        if isinstance(value, str):
            try:
                return _dt.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError(
                    f"Expected ISO date for {spec.key}"
                ) from exc
        return value
    return value if value is not None else ""


# Re-exported so the engine can import the join helper classes from
# one place. Keeps the import graph in the engine module narrow.
JOIN_MODELS = {
    "ContactTag": ContactTag,
    "ContactPipelineStage": ContactPipelineStage,
}
