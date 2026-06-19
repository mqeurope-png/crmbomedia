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
- Lista (tags, segmentos): `contains`, `not_contains`, `in`, `not_in`.

Los campos accesibles vienen del whitelist de `_FIELD_RESOLVERS` —
declarado en código, no en config. Un workflow nunca puede leer
columnas no expuestas (passwords, tokens, etc.).
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
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


def _trigger_field(name: str) -> Callable[[EvalContext], Any]:
    def resolver(ctx: EvalContext) -> Any:
        return (ctx.trigger_payload or {}).get(name)

    return resolver


_FIELD_RESOLVERS: dict[str, Callable[[EvalContext], Any]] = {
    # Datos básicos del contacto
    "contact.first_name": _contact_field("first_name"),
    "contact.last_name": _contact_field("last_name"),
    "contact.email": _contact_field("email"),
    "contact.phone": _contact_field("phone"),
    "contact.origin": _contact_field("origin"),
    "contact.lifecycle_status": _contact_field("commercial_status"),
    "contact.lead_score": _contact_field("lead_score"),
    "contact.owner_user_id": _contact_field("owner_user_id"),
    "contact.is_active": _contact_field("is_active"),
    "contact.tags": _contact_tags,
    "contact.marketing_consent": _contact_field("marketing_consent"),
    "contact.address_country": _contact_field("address_country"),
    "contact.job_title": _contact_field("job_title"),
    "contact.created_at": _contact_field("created_at"),
    "contact.updated_at": _contact_field("updated_at"),
    # Payload del trigger
    "trigger.field": _trigger_field("field"),
    "trigger.value": _trigger_field("value"),
    "trigger.old_value": _trigger_field("old_value"),
    "trigger.new_value": _trigger_field("new_value"),
    "trigger.event_type": _trigger_field("event_type"),
}


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

# PR-Fixes-Pase-2 Bug B. Mapeo del vocabulario segments → workflow.
_SEGMENT_OP_MAP = {
    "neq": "ne",
    "is_null": "empty",
    "is_not_null": "not_empty",
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


def _coerce_value(field: str, value: Any) -> Any:
    """`{{ contact.lead_score > "50" }}` se acepta — cuando viene de UI
    los valores son strings. Coerce a int/float si el campo lo es."""
    if value is None or not isinstance(value, str):
        return value
    if field in {"contact.lead_score"}:
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

    log.warning("workflows.condition unknown op: %s", op)
    return False


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
    resolver = _FIELD_RESOLVERS.get(field)
    if resolver is None:
        log.warning("workflows.condition unknown field: %s", field)
        return False
    actual = resolver(ctx)
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
    if field and field not in _FIELD_RESOLVERS:
        errors.append(f"unknown field: {field}")
    if not op:
        errors.append(f"leaf without op (field={field})")
    return errors


def available_fields() -> list[str]:
    """Para el dropdown del builder: devuelve la lista whitelisteada."""
    return sorted(_FIELD_RESOLVERS.keys())
