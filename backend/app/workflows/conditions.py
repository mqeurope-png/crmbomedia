"""Evaluador del árbol de condición tipado.

Estructura JSON sin texto libre — cero superficie de inyección. Un
nodo es:

    {"op": "AND" | "OR", "children": [<nodo>, ...]}

o una hoja:

    {"field": "contact.lead_score", "op": ">", "value": 50}

Operadores soportados por tipo de campo:

- Cualquier tipo: `eq`, `ne`, `empty`, `not_empty`.
- Número / fecha: `gt`, `lt`, `gte`, `lte`, `between`.
- String / texto: `contains`, `not_contains`, `starts_with`,
  `ends_with`.
- Lista (tags, segmentos): `contains`, `not_contains`, `in`, `not_in`,
  `contains_any`, `contains_all`, `contains_none`.
- Fecha: `before`, `after`, `in_last_n_days`, `in_next_n_days`.

Los campos accesibles vienen del whitelist de `_FIELD_RESOLVERS` —
declarado en código, no en config. Un workflow nunca puede leer
columnas no expuestas (passwords, tokens, etc.).
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.models.crm import Contact

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------
# Whitelist de campos accesibles. Cada entrada es un resolver que
# devuelve el valor en runtime dado el `EvalContext`.
# ---------------------------------------------------------------------


def _contact_field(
    name: str,
) -> Callable[[EvalContext], Any]:
    """Lee `name` directo del Contact ORM."""

    def resolver(ctx: EvalContext) -> Any:
        return getattr(ctx.contact, name, None)

    return resolver


def _contact_tags(ctx: EvalContext) -> list[str]:
    """Tags como lista. `Contact.tags` viene CSV; lo split en runtime."""
    raw = (ctx.contact.tags or "").strip()
    return [t.strip() for t in raw.split(",") if t.strip()] if raw else []


def _contact_full_name(ctx: EvalContext) -> str:
    """`first_name + " " + last_name` (con strip + sin dobles espacios)."""
    first = (ctx.contact.first_name or "").strip()
    last = (ctx.contact.last_name or "").strip()
    return " ".join(part for part in (first, last) if part)


def _trigger_field(name: str) -> Callable[[EvalContext], Any]:
    def resolver(ctx: EvalContext) -> Any:
        return (ctx.trigger_payload or {}).get(name)

    return resolver


# PR-Fix-Evaluator-Campos-Nativos. La whitelist del evaluador refleja
# las columnas del Contact que el FilterBuilder de `/contactos` ya
# expone (ver `app.services.segments.fields.FIELD_SPECS`). El editor
# de workflows usa el MISMO `EntityFilterBuilder`, así que las claves
# del árbol guardado vienen sin prefijo `contact.` — para evitar romper
# workflows existentes mantenemos AMBAS formas (`first_name` y
# `contact.first_name`) apuntando al mismo resolver.
_NATIVE_CONTACT_RESOLVERS: dict[str, Callable[[EvalContext], Any]] = {
    "first_name": _contact_field("first_name"),
    "last_name": _contact_field("last_name"),
    "name": _contact_full_name,
    "full_name": _contact_full_name,
    "email": _contact_field("email"),
    "phone": _contact_field("phone"),
    "origin": _contact_field("origin"),
    "origin_system": _contact_field("origin"),
    "origin_account_id": _contact_field("origin_account_id"),
    "lifecycle_status": _contact_field("commercial_status"),
    "commercial_status": _contact_field("commercial_status"),
    "lead_score": _contact_field("lead_score"),
    "owner_user_id": _contact_field("owner_user_id"),
    "is_active": _contact_field("is_active"),
    "is_email_valid": _contact_field("is_email_valid"),
    "marketing_consent": _contact_field("marketing_consent"),
    "tags": _contact_tags,
    "company_id": _contact_field("company_id"),
    "job_title": _contact_field("job_title"),
    "linkedin_url": _contact_field("linkedin_url"),
    "personal_website": _contact_field("personal_website"),
    "website_url": _contact_field("personal_website"),
    "address_line": _contact_field("address_line"),
    "address_street": _contact_field("address_line"),
    "address_city": _contact_field("address_city"),
    "address_state": _contact_field("address_state"),
    "address_region": _contact_field("address_region"),
    "address_postal_code": _contact_field("address_postal_code"),
    "address_zip": _contact_field("address_postal_code"),
    "address_country": _contact_field("address_country"),
    "address_country_name": _contact_field("address_country_name"),
    "created_at": _contact_field("created_at"),
    "updated_at": _contact_field("updated_at"),
}


def _build_field_resolvers() -> dict[str, Callable[[EvalContext], Any]]:
    """Compone la whitelist final: cada key nativa accesible como bare
    (`first_name`) y con prefijo legacy (`contact.first_name`)."""
    out: dict[str, Callable[[EvalContext], Any]] = {}
    for key, resolver in _NATIVE_CONTACT_RESOLVERS.items():
        out[key] = resolver
        out[f"contact.{key}"] = resolver
    out.update(
        {
            "trigger.field": _trigger_field("field"),
            "trigger.value": _trigger_field("value"),
            "trigger.old_value": _trigger_field("old_value"),
            "trigger.new_value": _trigger_field("new_value"),
            "trigger.event_type": _trigger_field("event_type"),
        }
    )
    return out


_FIELD_RESOLVERS: dict[str, Callable[[EvalContext], Any]] = (
    _build_field_resolvers()
)


def _resolve_custom_field(ctx: EvalContext, key: str) -> Any:
    """PR-Fix-Evaluator-Campos-Nativos. Lee una clave del JSON
    `contact.custom_fields` por nombre. Acepta el slug crudo (e.g.
    `INTERES`, `sector_empresa`) o la forma `custom_fields.X` que
    también podría emitir el FilterBuilder."""
    raw = ctx.contact.custom_fields
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed.get(key)


def _resolve_field(field: str, ctx: EvalContext) -> tuple[bool, Any]:
    """Resuelve el valor de un campo. Devuelve `(resolved, value)`.
    `resolved=False` significa "campo desconocido, log warning".

    Soporta:
      - Whitelist nativa (`first_name`, `email`, `tags`, …) en ambas
        formas: bare y con prefijo `contact.`.
      - Custom fields dinámicos: `custom_fields.X` o `cf.X` → lee
        del JSON de `contact.custom_fields`.
    """
    resolver = _FIELD_RESOLVERS.get(field)
    if resolver is not None:
        return (True, resolver(ctx))
    for prefix in ("custom_fields.", "cf.", "contact.custom_fields."):
        if field.startswith(prefix):
            key = field[len(prefix):]
            if key:
                return (True, _resolve_custom_field(ctx, key))
    return (False, None)


class EvalContext:
    """Bundle pasado al evaluador. Solo expone el Contact y el payload
    del trigger; el resto del state ORM queda fuera del scope."""

    def __init__(
        self,
        *,
        session: Session,
        contact: Contact,
        trigger_payload: dict[str, Any] | None = None,
    ) -> None:
        self.session = session
        self.contact = contact
        self.trigger_payload = trigger_payload or {}


# ---------------------------------------------------------------------
# Evaluador
# ---------------------------------------------------------------------


_LOGICAL = frozenset({"AND", "OR", "NOT"})

# PR-Fixes-Pase-2 Bug B + PR-Fix-Evaluator-Campos-Nativos. Mapeo del
# vocabulario segments → workflow. Ambos comparten muchos operadores
# tal cual (gt/lt/contains/etc.); solo mapeamos los que difieren.
_SEGMENT_OP_MAP = {
    "neq": "ne",
    "is_null": "empty",
    "is_not_null": "not_empty",
    "is_empty": "empty",
    "is_not_empty": "not_empty",
    "doesNotContain": "not_contains",
    "beginsWith": "starts_with",
    "endsWith": "ends_with",
}


def _normalize_logical(value: Any) -> str | None:
    """`{operator: "and"}` (segments) → `"AND"` (workflow)."""
    if isinstance(value, str):
        up = value.upper()
        if up in _LOGICAL:
            return up
    return None


def _normalize_leaf_op(value: Any) -> str:
    """Mapea operadores del vocabulario segments al workflow. Para
    operadores ya válidos en workflow, los devuelve tal cual."""
    if not isinstance(value, str):
        return ""
    return _SEGMENT_OP_MAP.get(value, value)


MAX_DEPTH = 10


_NUMERIC_FIELDS = frozenset({"lead_score", "contact.lead_score"})


def _coerce_value(field: str, value: Any) -> Any:
    """`{{ lead_score > "50" }}` se acepta — cuando viene de UI los
    valores son strings. Coerce a int si el campo lo es."""
    if value is None or not isinstance(value, str):
        return value
    if field in _NUMERIC_FIELDS:
        try:
            return int(value)
        except (TypeError, ValueError):
            return value
    return value


def _compare(actual: Any, op: str, expected: Any) -> bool:
    """Centraliza los operadores. Cada uno tolera tipos incompatibles
    devolviendo False (semántica "el campo no cumple") en vez de
    levantar — un workflow no debe crashear por un campo NULL."""
    if op == "empty":
        return actual is None or actual == "" or actual == []
    if op == "not_empty":
        return not (actual is None or actual == "" or actual == [])

    if op == "eq":
        return actual == expected
    if op == "ne":
        return actual != expected

    if op == "gt":
        try:
            return actual is not None and actual > expected
        except TypeError:
            return False
    if op == "gte":
        try:
            return actual is not None and actual >= expected
        except TypeError:
            return False
    if op == "lt":
        try:
            return actual is not None and actual < expected
        except TypeError:
            return False
    if op == "lte":
        try:
            return actual is not None and actual <= expected
        except TypeError:
            return False
    if op == "between":
        if not isinstance(expected, (list, tuple)) or len(expected) != 2:
            return False
        try:
            return (
                actual is not None
                and expected[0] <= actual <= expected[1]
            )
        except TypeError:
            return False

    if op == "contains":
        if actual is None:
            return False
        if isinstance(actual, (list, tuple, set)):
            return expected in actual
        return str(expected).lower() in str(actual).lower()
    if op == "not_contains":
        return not _compare(actual, "contains", expected)

    if op == "starts_with":
        return (
            actual is not None
            and str(actual).lower().startswith(str(expected).lower())
        )
    if op == "ends_with":
        return (
            actual is not None
            and str(actual).lower().endswith(str(expected).lower())
        )

    if op == "in":
        if not isinstance(expected, (list, tuple, set)):
            return False
        return actual in expected
    if op == "not_in":
        return not _compare(actual, "in", expected)
    if op == "in_list":
        return _compare(actual, "in", expected)

    # PR-Fix-Evaluator-Campos-Nativos. Operadores que el FilterBuilder
    # de `/contactos` emite sobre tag-multi y que el evaluador todavía
    # no soportaba.
    if op in {"contains_any", "contains_all", "contains_none"}:
        if not isinstance(expected, (list, tuple, set)):
            expected = [expected]
        if not isinstance(actual, (list, tuple, set)):
            actual = [actual] if actual is not None else []
        actual_set = {str(a).lower() for a in actual}
        expected_set = {str(e).lower() for e in expected if e}
        if op == "contains_any":
            return bool(actual_set & expected_set)
        if op == "contains_all":
            return expected_set.issubset(actual_set)
        return not (actual_set & expected_set)  # contains_none

    # Operadores de fecha.
    if op in {"before", "after"}:
        actual_dt = _coerce_datetime(actual)
        expected_dt = _coerce_datetime(expected)
        if actual_dt is None or expected_dt is None:
            return False
        return (
            actual_dt < expected_dt if op == "before" else actual_dt > expected_dt
        )
    if op in {"in_last_n_days", "in_next_n_days"}:
        actual_dt = _coerce_datetime(actual)
        try:
            n = int(expected)
        except (TypeError, ValueError):
            return False
        if actual_dt is None or n < 0:
            return False
        now = datetime.now(UTC)
        if op == "in_last_n_days":
            window_start = now - timedelta(days=n)
            return window_start <= actual_dt <= now
        window_end = now + timedelta(days=n)
        return now <= actual_dt <= window_end

    log.warning("workflows.condition unknown op: %s", op)
    return False


def _coerce_datetime(value: Any) -> datetime | None:
    """Acepta datetime ORM directo, ISO 8601 string, o None. Devuelve
    siempre TZ-aware en UTC para que las comparaciones sean válidas."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            # `fromisoformat` 3.11+ acepta el sufijo "Z".
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            # Date-only string `2026-06-19`.
            try:
                parsed = datetime.strptime(text, "%Y-%m-%d")
            except ValueError:
                return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def evaluate(
    tree: dict[str, Any] | None,
    ctx: EvalContext,
    *,
    depth: int = 0,
) -> bool:
    """Evalúa el árbol contra el contexto. Árbol vacío / None → True
    (sin restricciones).

    PR-Fixes-Pase-2 Bug B. Acepta DOS formatos para compatibilidad
    con la integración del filtro de Contactos (`EntityFilterBuilder`):

    1. Formato workflow legacy: `{op: AND|OR|NOT, children}` o
       `{field, op, value}` para hojas.
    2. Formato segments: `{operator: and|or|not, children}` o
       `{type: "rule", field, comparator, value}` para hojas.

    Las hojas del formato segments se mapean al vocabulario de
    operadores del evaluador (`is_null` → `empty`, `neq` → `ne`, etc.).
    """
    if tree is None or not tree:
        return True
    if depth > MAX_DEPTH:
        log.warning("workflows.condition max depth exceeded")
        return False

    # Normaliza el operador lógico (acepta op y operator).
    op = tree.get("op") or _normalize_logical(tree.get("operator"))
    if op in _LOGICAL:
        children = tree.get("children") or []
        if not children:
            return True
        if op == "AND":
            return all(evaluate(c, ctx, depth=depth + 1) for c in children)
        if op == "OR":
            return any(evaluate(c, ctx, depth=depth + 1) for c in children)
        if op == "NOT":
            return not any(
                evaluate(c, ctx, depth=depth + 1) for c in children
            )

    # Hoja: leaf comparison. Aceptamos también el shape segments
    # `{type: "rule", field, comparator, value}`.
    field = tree.get("field")
    if not field:
        return False
    resolved, actual = _resolve_field(field, ctx)
    if not resolved:
        log.warning("workflows.condition unknown field: %s", field)
        return False
    raw_value = tree.get("value")
    expected = _coerce_value(field, raw_value)
    # `op` puede ser workflow legacy o segments. Para hojas viene en
    # `tree.get("op")` (workflow) o `tree.get("comparator")` (segments).
    leaf_op = tree.get("op") or tree.get("comparator")
    return _compare(actual, _normalize_leaf_op(leaf_op), expected)


def parse_tree(raw: str | None) -> dict[str, Any]:
    """Lee el JSON guardado en `WorkflowStep.config_json["condition"]` —
    devuelve `{}` (que evalúa a True) si el JSON está vacío o corrupto."""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except (TypeError, ValueError):
        pass
    return {}


def validate_tree(
    tree: dict[str, Any], *, depth: int = 0
) -> list[str]:
    """Recorre el árbol y devuelve la lista de errores estructurales.
    Llamado al `activate` del workflow para rechazar workflows con
    condiciones rotas."""
    errors: list[str] = []
    if depth > MAX_DEPTH:
        errors.append("max depth exceeded")
        return errors
    if not tree:
        return errors
    op = tree.get("op")
    if op in _LOGICAL:
        children = tree.get("children") or []
        for child in children:
            errors.extend(validate_tree(child, depth=depth + 1))
        return errors
    field = tree.get("field")
    if field and not _is_known_field(field):
        errors.append(f"unknown field: {field}")
    if not op:
        errors.append(f"leaf without op (field={field})")
    return errors


def _is_known_field(field: str) -> bool:
    """Espejo de la lógica del evaluador para `validate_tree`."""
    if field in _FIELD_RESOLVERS:
        return True
    for prefix in ("custom_fields.", "cf.", "contact.custom_fields."):
        if field.startswith(prefix) and len(field) > len(prefix):
            return True
    return False


def available_fields() -> list[str]:
    """Para el dropdown del builder: devuelve la lista whitelisteada."""
    return sorted(_FIELD_RESOLVERS.keys())
