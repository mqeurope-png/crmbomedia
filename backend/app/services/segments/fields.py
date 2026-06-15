"""Whitelist of fields + comparators the rule engine accepts (Contact).

This is the **anti-injection boundary**. Any field name or
comparator that doesn't appear here is rejected before the engine
attempts to build SQL — the operator's `rules_json` is never
trusted to name a column directly.

Each `FieldSpec` carries the human label shown in the UI, the
column reference (or relationship hint for joins) and the list of
comparators valid for the field's value type.

Sprint Filtros & Listas (PR-A): `FieldSpec` grew the UI/column
metadata (`sortable`, `displayable`, `filterable`, `default_visible`,
`grouped_under`, `source`, `reference_table`) so the same descriptor
drives BOTH the filter builder and the TanStack column configurator,
across every entity — not just contacts. The Contact registry below
is the reference implementation; the other entities live under
`app/services/entities/`.
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
    type: str  # string | int | bool | date | enum | reference | tag-multi | uuid-multi | json
    comparators: tuple[str, ...]
    column: Any | None = None
    enum_values: tuple[str, ...] = ()
    # When the field requires a join (tags / pipelines), the engine
    # follows this hint to build an `EXISTS (subquery)` predicate so
    # the outer query stays distinct-free.
    relation: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)
    # --- Sprint Filtros & Listas (PR-A) UI/column metadata -----------
    # Defaults keep older call-sites valid; the registries below set
    # them explicitly. `filterable` defaults False when a field has no
    # comparators (display-only columns); see `__post_init__`.
    sortable: bool = False
    displayable: bool = True
    filterable: bool = True
    default_visible: bool = False
    grouped_under: str = "General"
    source: str = "column"  # column | custom_fields_json | computed | related_table
    reference_table: str | None = None

    def __post_init__(self) -> None:
        # A field with no comparators can't be filtered, regardless of
        # the flag — keep the two consistent so the UI never renders a
        # filter row that the engine would reject.
        if not self.comparators:
            object.__setattr__(self, "filterable", False)


_COMMON_STRING = (
    "contains",
    "not_contains",
    "starts_with",
    "ends_with",
    "eq",
    "neq",
    "is_null",
    "is_not_null",
)
_COMMON_NULLABLE = ("is_null", "is_not_null")
_NUMERIC = ("eq", "neq", "gt", "gte", "lt", "lte", "between", "is_null")
_DATE = (
    "before",
    "after",
    "between",
    "in_last_n_days",
    "not_in_last_n_days",
    "older_than_n_days",
    "is_null",
    "is_not_null",
)
_REFERENCE = ("eq", "neq", "in", "not_in", "is_null", "is_not_null")
_ENUM = ("eq", "neq", "in", "not_in")

FIELD_SPECS: dict[str, FieldSpec] = {
    "name": FieldSpec(
        key="name",
        label="Nombre completo",
        type="string",
        comparators=_COMMON_STRING,
        # "name" maps to first_name + last_name concatenation. The
        # engine handles it specially.
        extras={"concat": ("first_name", "last_name")},
        sortable=True,
        default_visible=True,
        grouped_under="Datos básicos",
        source="computed",
    ),
    "email": FieldSpec(
        key="email",
        label="Email",
        type="string",
        comparators=_COMMON_STRING,
        column=Contact.email,
        sortable=True,
        default_visible=True,
        grouped_under="Datos básicos",
    ),
    "phone": FieldSpec(
        key="phone",
        label="Teléfono",
        type="string",
        comparators=_COMMON_STRING,
        column=Contact.phone,
        sortable=True,
        default_visible=True,
        grouped_under="Datos básicos",
    ),
    "first_name": FieldSpec(
        key="first_name",
        label="Nombre",
        type="string",
        comparators=_COMMON_STRING,
        column=Contact.first_name,
        sortable=True,
        grouped_under="Datos básicos",
    ),
    "last_name": FieldSpec(
        key="last_name",
        label="Apellidos",
        type="string",
        comparators=_COMMON_STRING,
        column=Contact.last_name,
        sortable=True,
        grouped_under="Datos básicos",
    ),
    "tags": FieldSpec(
        key="tags",
        label="Tags",
        type="tag-multi",
        comparators=("contains_any", "contains_all", "contains_none"),
        relation="tags",
        default_visible=True,
        grouped_under="Datos básicos",
        source="related_table",
        reference_table="tags",
    ),
    # Sprint Filtros & Listas: owner exposed as column AND filter.
    # `is_null` == "Sin asignar"; populated en masse by the upcoming
    # Reglas-Assign sprint, NULL on every imported contact today.
    "owner_user_id": FieldSpec(
        key="owner_user_id",
        label="Propietario",
        type="reference",
        comparators=_REFERENCE,
        column=Contact.owner_user_id,
        sortable=True,
        grouped_under="Comercial",
        reference_table="users",
    ),
    "origin_system": FieldSpec(
        key="origin_system",
        label="Sistema de origen",
        type="enum",
        comparators=("eq", "neq", "in", "not_in"),
        enum_values=("agilecrm", "brevo", "freshdesk", "factusol", "manual"),
        relation="external_refs.system",
        default_visible=True,
        grouped_under="Origen",
        source="related_table",
    ),
    "origin_account_id": FieldSpec(
        key="origin_account_id",
        label="Cuenta de origen",
        type="string",
        comparators=("eq", "neq", "in"),
        relation="external_refs.account_id",
        grouped_under="Origen",
        source="related_table",
    ),
    "commercial_status": FieldSpec(
        key="commercial_status",
        label="Estado comercial",
        type="enum",
        comparators=("eq", "neq", "in", "not_in"),
        enum_values=("new", "qualified", "won", "lost"),
        column=Contact.commercial_status,
        sortable=True,
        default_visible=True,
        grouped_under="Comercial",
    ),
    "marketing_consent": FieldSpec(
        key="marketing_consent",
        label="Consentimiento marketing",
        type="enum",
        comparators=("eq", "neq", "in", "not_in"),
        enum_values=("granted", "denied", "unknown", "unsubscribed"),
        column=Contact.marketing_consent,
        sortable=True,
        default_visible=True,
        grouped_under="GDPR",
    ),
    "is_active": FieldSpec(
        key="is_active",
        label="Activo",
        type="bool",
        comparators=("eq",),
        column=Contact.is_active,
        sortable=True,
        grouped_under="Sistema",
    ),
    "lead_score": FieldSpec(
        key="lead_score",
        label="Lead score",
        type="int",
        comparators=_NUMERIC,
        column=Contact.lead_score,
        sortable=True,
        grouped_under="Comercial",
    ),
    "address_country": FieldSpec(
        key="address_country",
        label="País (dirección)",
        type="string",
        comparators=(
            "eq",
            "neq",
            "in",
            "not_in",
            "contains",
            "not_contains",
            "is_null",
            "is_not_null",
        ),
        column=Contact.address_country,
        sortable=True,
        grouped_under="Dirección",
    ),
    "address_city": FieldSpec(
        key="address_city",
        label="Ciudad (dirección)",
        type="string",
        comparators=_COMMON_STRING,
        column=Contact.address_city,
        sortable=True,
        grouped_under="Dirección",
    ),
    "created_at": FieldSpec(
        key="created_at",
        label="Fecha creación",
        type="date",
        comparators=_DATE,
        column=Contact.created_at,
        sortable=True,
        grouped_under="Sistema",
    ),
    "updated_at": FieldSpec(
        key="updated_at",
        label="Última modificación",
        type="date",
        comparators=_DATE,
        column=Contact.updated_at,
        sortable=True,
        grouped_under="Sistema",
    ),
    "external_data_refreshed_at": FieldSpec(
        key="external_data_refreshed_at",
        label="Última actualización externa",
        type="date",
        comparators=_DATE,
        column=Contact.external_data_refreshed_at,
        sortable=True,
        grouped_under="Origen",
    ),
    "created_at_external": FieldSpec(
        key="created_at_external",
        label="Creado en origen",
        type="date",
        comparators=_DATE,
        column=Contact.created_at_external,
        sortable=True,
        default_visible=True,
        grouped_under="Origen",
    ),
    "updated_at_external": FieldSpec(
        key="updated_at_external",
        label="Última modificación en origen",
        type="date",
        comparators=_DATE,
        column=Contact.updated_at_external,
        sortable=True,
        default_visible=True,
        grouped_under="Origen",
    ),
    "in_segment": FieldSpec(
        key="in_segment",
        label="En segmento",
        type="uuid-multi",
        comparators=("in", "not_in"),
        relation="segment_membership",
        displayable=False,
        grouped_under="Segmentos",
        source="related_table",
        reference_table="segments",
    ),
    "in_brevo_list": FieldSpec(
        key="in_brevo_list",
        label="En lista Brevo",
        type="uuid-multi",
        comparators=("in", "not_in"),
        relation="brevo_list_membership",
        displayable=False,
        grouped_under="Marketing",
        source="related_table",
        reference_table="brevo_lists",
    ),
    "pipeline_id": FieldSpec(
        key="pipeline_id",
        label="En pipeline",
        type="uuid-multi",
        comparators=("in", "not_in"),
        relation="pipeline_id",
        displayable=False,
        grouped_under="Comercial",
        source="related_table",
        reference_table="pipelines",
    ),
    "pipeline_stage_id": FieldSpec(
        key="pipeline_stage_id",
        label="En etapa de pipeline",
        type="uuid-multi",
        comparators=("in", "not_in"),
        relation="pipeline_stage_id",
        displayable=False,
        grouped_under="Comercial",
        source="related_table",
        reference_table="pipeline_stages",
    ),
}


def get_field_spec(field_key: str) -> FieldSpec | None:
    return FIELD_SPECS.get(field_key)


def field_spec_to_ui(spec: FieldSpec) -> dict[str, Any]:
    """Serialise one `FieldSpec` to the shape the frontend consumes
    (filter builder dropdowns + TanStack column configurator). Shared
    by the legacy `/api/segments/available-fields` and the new
    `/api/entities/{entity}/filter-schema` so both stay in lock-step."""
    return {
        "key": spec.key,
        "label": spec.label,
        "type": spec.type,
        "comparators": list(spec.comparators),
        "enum_values": list(spec.enum_values),
        "sortable": spec.sortable,
        "displayable": spec.displayable,
        "filterable": spec.filterable,
        "default_visible": spec.default_visible,
        "grouped_under": spec.grouped_under,
        "source": spec.source,
        "reference_table": spec.reference_table,
    }


def list_fields_for_ui() -> list[dict[str, Any]]:
    """Shape consumed by `GET /api/segments/available-fields` (Contact).
    The builder UI uses it to render the dropdowns; the AI prompt
    serialises it into the system prompt so Claude only sees fields it
    can actually use. The legacy callers ignore the extra keys added in
    PR-A, so the additive shape is backward compatible."""
    return [field_spec_to_ui(spec) for spec in FIELD_SPECS.values()]


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
    if spec.type == "reference":
        # Foreign-key id (owner_user_id, company_id, …). Compared as a
        # string id; no enum constraint (the universe of ids is the
        # referenced table, validated by the FK at write time).
        return str(value)
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
