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

import re
from dataclasses import dataclass, field
from typing import Any

from app.models.crm import (
    Contact,
    ContactPipelineStage,
    ContactTag,
)

# 36-char canonical UUID. Acepta también el hex-32 sin guiones por si
# algún cliente legacy lo envía así.
_LOOKS_LIKE_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    r"|^[0-9a-f]{32}$",
    re.IGNORECASE,
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
# PR-Ce: numeric whitelist gana `is_not_null` para paridad con la tabla
# normativa (auditoría §1) — el motor ya lo soportaba; sólo era un
# olvido del whitelist.
_NUMERIC = (
    "eq",
    "neq",
    "gt",
    "gte",
    "lt",
    "lte",
    "between",
    "is_null",
    "is_not_null",
)
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
# Sprint Reglas-Assign PR-B: comparadores M:N para `assigned_users`.
# `is_empty` / `is_not_empty` operan sobre el conjunto entero (sin/con
# asignaciones), por eso no usan `is_null`/`is_not_null` que están
# pensados para columnas escalares. Sintaxis distinta, semántica
# análoga.
_ASSIGNMENT_MULTI = (
    "contains_any",
    "contains_all",
    "is_empty",
    "is_not_empty",
)
_PRIMARY_REFERENCE = ("eq", "neq", "is_null", "is_not_null")
# QoL sprint — filtro notes_content. Comparadores de texto sobre el
# join con `notes.body` (post-unification 0049). `is_empty` /
# `is_not_empty` operan sobre el conjunto (contacto SIN notas /
# con ≥1 nota), no sobre el contenido literal vacío.
_NOTES_CONTENT = (
    "contains",
    "starts_with",
    "ends_with",
    "is_empty",
    "is_not_empty",
)
# PR-Ce: los 3 enums (origin_system, commercial_status,
# marketing_consent) usaban (eq, neq, in, not_in) — sin nullable. Hay
# casos en producción con esos campos NULL (importados sin estado, sin
# consent) que el operador necesita encontrar.
_ENUM_NULLABLE = ("eq", "neq", "in", "not_in", "is_null", "is_not_null")
# PR-E3 (Deuda #8) — filtro composite de interacción con campañas Brevo.
# Un único comparator `matches` cuyo `value` es un objeto
# `{campaigns:[int], action, period, start?, end?}`. El motor lo
# compila a EXISTS / NOT EXISTS sobre activity_events.
_BREVO_INTERACTION = ("matches",)
#: Acciones soportadas → event_type(s) de activity_events. `not_opened`
#: y `not_clicked` son negativas (delivered-pero-no-X / opened-pero-no-X)
#: y se compilan distinto.
BREVO_INTERACTION_ACTIONS: dict[str, tuple[str, ...]] = {
    "received": ("email.delivered",),
    "opened": ("email.opened",),
    "clicked": ("email.clicked",),
    "not_opened": ("email.opened",),  # negativa sobre delivered
    "not_clicked": ("email.clicked",),  # negativa sobre opened
    "bounced": ("email.bounced_hard", "email.bounced_soft"),
    "unsubscribed": ("email.unsubscribed",),
}
#: Períodos referidos a `brevo_campaigns_cache.sent_at`. "all" = sin
#: cota temporal. Reusa la misma escala que los widgets del dashboard.
BREVO_INTERACTION_PERIODS: dict[str, int | None] = {
    "all": None,
    "3d": 3,
    "7d": 7,
    "15d": 15,
    "30d": 30,
    "custom": None,  # usa start/end explícitos
}

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
        comparators=(
            "contains_any",
            "contains_all",
            "contains_none",
            # PR-Cc: substring match by tag name. Resuelve "filtra los
            # contactos con cualquier tag tipo 'mbo'" sin tener que
            # seleccionar uno a uno desde el picker.
            "tag_name_contains",
        ),
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
        label="Propietario (legacy)",
        type="reference",
        comparators=_REFERENCE,
        column=Contact.owner_user_id,
        sortable=True,
        # Sprint Reglas-Assign PR-B: el campo histórico se mantiene
        # operativo (apunta al caché del primary) pero se oculta del
        # constructor por defecto a favor de `assigned_users` y
        # `primary_user`, que reflejan el modelo multi-comercial.
        default_visible=False,
        displayable=False,
        grouped_under="Comercial",
        reference_table="users",
    ),
    # Sprint Reglas-Assign PR-B. M:N — un contacto puede estar asignado
    # a varios comerciales (primary + secundarios). El motor expande a
    # EXISTS sobre contact_assignments cubriendo TODOS los roles.
    "assigned_users": FieldSpec(
        key="assigned_users",
        label="Asignado a",
        type="reference-multi",
        comparators=_ASSIGNMENT_MULTI,
        relation="assignments",
        default_visible=True,
        grouped_under="Comercial",
        source="related_table",
        reference_table="users",
    ),
    "primary_user": FieldSpec(
        key="primary_user",
        label="Responsable (primary)",
        type="reference",
        comparators=_PRIMARY_REFERENCE,
        relation="primary_assignment",
        grouped_under="Comercial",
        source="related_table",
        reference_table="users",
    ),
    "origin_system": FieldSpec(
        key="origin_system",
        label="Sistema de origen",
        type="enum",
        # PR-Ce: las otras enums ganan is_null/is_not_null (son columnas
        # NOT NULL hoy pero los matchers compilan a clausa válida —
        # zero rows hasta que alguien las haga nullable). Aquí
        # `origin_system` es relacional (external_refs.system) y el
        # leaf compiler de relaciones no soporta los nullable
        # matchers; mantengo sin ellos hasta que se decida una
        # semántica clara para "contacto sin external_refs".
        comparators=("eq", "neq", "in", "not_in"),
        enum_values=("agilecrm", "brevo", "freshdesk", "factusol", "manual"),
        relation="external_refs.system",
        default_visible=True,
        grouped_under="Origen",
        source="related_table",
    ),
    # QoL sprint. Texto libre en cualquiera de las notas del contacto
    # (timeline Agile importado + Note1..10 + manuales del CRM, todo
    # unificado en `notes` tras migration 0049). Útil para rescatar
    # leads por palabras clave que el operador anotó hace tiempo.
    # Compila a EXISTS sobre `notes` con LIKE sobre `body`.
    "notes_content": FieldSpec(
        key="notes_content",
        label="Notas (texto libre)",
        type="string",
        comparators=_NOTES_CONTENT,
        relation="notes.body",
        grouped_under="Notas",
        source="related_table",
        displayable=False,
    ),
    "origin_account_id": FieldSpec(
        # PR-Fix-Sync-Dispara-Reglas-Workflows.
        #
        # SQL path (`build_filter`): mantenemos `relation =
        # external_refs.account_id` para que el _compile_external_ref_leaf
        # de PR-Da siga parseando compound keys `system:account_id` →
        # tupla AND(system, account_id). Esa lógica ya tiene el filtro
        # cross-system correcto + backward-compat para legacy bare
        # values, y los tests `test_origin_account_filter` la cubren.
        #
        # In-memory path (`_resolve_attr` → `evaluate_contact_against_rules`):
        # antes devolvía `ref.account_id` ("default") y comparaba con
        # `"agilecrm:default"` → False, así que las rules de Bart
        # nunca disparaban. El resolver ahora prefiere
        # `Contact.origin_account_id` (formato `{system}:{account_id}`,
        # poblado por sync + migración 0064) cuando está set.
        key="origin_account_id",
        label="Cuenta de origen",
        type="string",
        comparators=("eq", "neq", "in"),
        relation="external_refs.account_id",
        source="related_table",
        grouped_under="Origen",
    ),
    "commercial_status": FieldSpec(
        key="commercial_status",
        label="Estado comercial",
        type="enum",
        comparators=_ENUM_NULLABLE,
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
        comparators=_ENUM_NULLABLE,
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
    # PR-Consolidado — Star Rating. Campo independiente del
    # `lead_score`: réplica del Star Value nativo de AgileCRM (1-5).
    # Filtrable + sortable + columna toggleable en la lista. El
    # `<EntityTable>` reusa el FieldDescriptor para renderizar tanto
    # el header como las cells; el cell renderer override
    # (`StarRating`) vive en el frontend.
    "star_rating": FieldSpec(
        key="star_rating",
        label="Estrellas",
        type="int",
        comparators=_NUMERIC,
        column=Contact.star_rating,
        sortable=True,
        grouped_under="Comercial",
    ),
    # PR-Cc — Sprint Empresas trajo job_title, linkedin_url,
    # personal_website y la dirección granular (state/postal_code/
    # address_line/address_region). En PR-A solo registré city + country;
    # añado el resto para paridad con las columnas reales de la ficha.
    "job_title": FieldSpec(
        key="job_title",
        label="Cargo",
        type="string",
        comparators=_COMMON_STRING,
        column=Contact.job_title,
        sortable=True,
        grouped_under="Profesional",
    ),
    "linkedin_url": FieldSpec(
        key="linkedin_url",
        label="LinkedIn",
        type="string",
        comparators=_COMMON_STRING,
        column=Contact.linkedin_url,
        grouped_under="Profesional",
    ),
    "personal_website": FieldSpec(
        key="personal_website",
        label="Web personal",
        type="string",
        comparators=_COMMON_STRING,
        column=Contact.personal_website,
        grouped_under="Profesional",
    ),
    "company_id": FieldSpec(
        key="company_id",
        label="Empresa",
        type="reference",
        comparators=("eq", "neq", "in", "not_in", "is_null", "is_not_null"),
        column=Contact.company_id,
        sortable=True,
        grouped_under="Profesional",
        reference_table="companies",
    ),
    "is_email_valid": FieldSpec(
        key="is_email_valid",
        label="Email válido",
        type="bool",
        comparators=("eq",),
        column=Contact.is_email_valid,
        grouped_under="Sistema",
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
    "address_state": FieldSpec(
        key="address_state",
        label="Provincia",
        type="string",
        comparators=_COMMON_STRING,
        column=Contact.address_state,
        grouped_under="Dirección",
    ),
    "address_line": FieldSpec(
        key="address_line",
        label="Calle / dirección",
        type="string",
        comparators=_COMMON_STRING,
        column=Contact.address_line,
        grouped_under="Dirección",
    ),
    "address_postal_code": FieldSpec(
        key="address_postal_code",
        label="Código postal",
        type="string",
        comparators=_COMMON_STRING,
        column=Contact.address_postal_code,
        grouped_under="Dirección",
    ),
    "address_region": FieldSpec(
        key="address_region",
        label="Región",
        type="string",
        comparators=_COMMON_STRING,
        column=Contact.address_region,
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
    # PR-E3 (Deuda #8). Composite — el editor frontend pinta 3
    # sub-pickers (campañas multi-select + acción + período). El value
    # es un objeto, no un escalar; `validate_value` lo valida y el
    # motor compila a EXISTS/NOT EXISTS contra activity_events.
    "brevo_campaign_interaction": FieldSpec(
        key="brevo_campaign_interaction",
        label="Interacción con campañas",
        type="brevo_campaign_interaction",
        comparators=_BREVO_INTERACTION,
        relation="brevo_campaign_interaction",
        displayable=False,
        grouped_under="Marketing",
        source="related_table",
        reference_table="brevo_campaigns",
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
    if comparator in {"is_null", "is_not_null", "is_empty", "is_not_empty"}:
        return None
    if spec.type == "brevo_campaign_interaction":
        return _validate_brevo_interaction(value)
    if comparator in {"in", "not_in", "contains_any", "contains_all", "contains_none"}:
        if not isinstance(value, list) or not value:
            raise ValueError(f"Comparator {comparator!r} requires a non-empty list")
        return [_coerce_scalar(spec, item) for item in value]
    if comparator == "between":
        if not isinstance(value, list) or len(value) != 2:
            raise ValueError("between requires a 2-element list")
        return [_coerce_scalar(spec, item) for item in value]
    return _coerce_scalar(spec, value)


def _validate_brevo_interaction(value: Any) -> dict[str, Any]:
    """Valida el value composite de `brevo_campaign_interaction`:

        {
          "campaigns": [123, 456],        # brevo_campaign_id (int)
          "action": "opened",            # ∈ BREVO_INTERACTION_ACTIONS
          "period": "30d",               # ∈ BREVO_INTERACTION_PERIODS
          "start": "2026-01-01T...",     # solo si period == custom
          "end":   "2026-01-31T..."
        }

    Devuelve el dict normalizado (campaigns como list[int], period
    default "all"). Raises ValueError en cualquier inconsistencia → el
    motor lo mapea a 400.
    """
    from datetime import datetime as _dt

    if not isinstance(value, dict):
        raise ValueError(
            "brevo_campaign_interaction requiere un objeto "
            "{campaigns, action, period}"
        )
    raw_campaigns = value.get("campaigns")
    if not isinstance(raw_campaigns, list) or not raw_campaigns:
        raise ValueError("Selecciona al menos una campaña")
    campaigns: list[int] = []
    for item in raw_campaigns:
        try:
            campaigns.append(int(item))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"brevo_campaign_id no numérico: {item!r}"
            ) from exc

    action = str(value.get("action") or "")
    if action not in BREVO_INTERACTION_ACTIONS:
        raise ValueError(
            f"Acción desconocida {action!r}. Válidas: "
            f"{', '.join(BREVO_INTERACTION_ACTIONS)}"
        )

    period = str(value.get("period") or "all")
    if period not in BREVO_INTERACTION_PERIODS:
        raise ValueError(
            f"Período desconocido {period!r}. Válidos: "
            f"{', '.join(BREVO_INTERACTION_PERIODS)}"
        )

    normalized: dict[str, Any] = {
        "campaigns": campaigns,
        "action": action,
        "period": period,
    }
    if period == "custom":
        start_raw = value.get("start")
        end_raw = value.get("end")
        if not start_raw or not end_raw:
            raise ValueError(
                "El período 'custom' requiere start + end"
            )
        try:
            normalized["start"] = _dt.fromisoformat(
                str(start_raw).replace("Z", "+00:00")
            )
            normalized["end"] = _dt.fromisoformat(
                str(end_raw).replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise ValueError(
                "start/end deben ser ISO datetime"
            ) from exc
    return normalized


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
    if spec.type in {"reference", "reference-multi"}:
        # Foreign-key id (owner_user_id, company_id, …). PR-Ce: el
        # editor por defecto solía ser un text input — si el operador
        # tecleaba algo no-UUID, el motor lo aceptaba y producía 0
        # matches en silencio. Ahora 400 con mensaje claro; los
        # pickers nuevos emiten UUIDs reales así que esto sólo se
        # dispara en peticiones legacy o manuales.
        text = str(value).strip()
        if not _LOOKS_LIKE_UUID.match(text):
            raise ValueError(
                f"Expected UUID for {spec.key} (got {text!r})"
            )
        return text
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
