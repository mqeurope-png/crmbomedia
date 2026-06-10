"""Boolean-rule engine: JSON tree → SQLAlchemy filter expression.

The route layer calls `build_filter(rules_tree)` to get a single
`ColumnElement[bool]` it can apply to `select(Contact).where(...)`.

Three goals:
  1. Anti-injection: every `field` / `comparator` is matched against
     the whitelist in `fields.py` before any SQL is generated.
  2. Determinism: the same tree always produces the same plan; the
     engine never inspects the database.
  3. Composability: AND / OR / NOT nest arbitrarily; the tree's leaves
     (`type: "rule"`) speak the per-field whitelist.

`evaluate_contact_against_rules(contact, tree)` is the in-memory
mirror used for hooks (Sprint E "trigger when a contact enters
segment X").
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import (
    Boolean,
    ColumnElement,
    String,
    and_,
    exists,
    func,
    not_,
    or_,
    select,
)
from sqlalchemy.orm import Mapped

from app.models.crm import (
    Contact,
    ContactPipelineStage,
    ContactTag,
    ExternalReference,
)
from app.services.segments.fields import (
    FIELD_SPECS,
    FieldSpec,
    get_field_spec,
    validate_value,
)

MAX_DEPTH = 10
LOGICAL_OPERATORS = {"AND", "OR", "NOT"}


class SegmentRuleError(ValueError):
    """Operator-supplied tree failed validation. The route maps it to
    400 so the UI can highlight the offending node."""


def build_filter(tree: dict[str, Any]) -> ColumnElement[bool]:
    """Compile the rule tree into a SQLAlchemy boolean expression.

    Empty / falsy input compiles to `True` so a brand-new segment
    matches every contact instead of crashing the preview.
    """
    if not tree:
        return _true()
    return _compile(tree, depth=0)


def _compile(node: dict[str, Any], *, depth: int) -> ColumnElement[bool]:
    if depth > MAX_DEPTH:
        raise SegmentRuleError(f"Rule tree exceeds max depth {MAX_DEPTH}")
    if not isinstance(node, dict):
        raise SegmentRuleError("Rule node must be a JSON object")

    operator = node.get("operator")
    if operator:
        op_upper = str(operator).upper()
        if op_upper not in LOGICAL_OPERATORS:
            raise SegmentRuleError(f"Unknown logical operator {operator!r}")
        children = node.get("children") or []
        if not isinstance(children, list) or not children:
            # An empty logical block matches everything (AND) / nothing
            # (OR / NOT). We pick a friendly default: empty means no
            # filter so the operator sees their contact universe.
            return _true()
        compiled = [_compile(child, depth=depth + 1) for child in children]
        if op_upper == "AND":
            return and_(*compiled)
        if op_upper == "OR":
            return or_(*compiled)
        # NOT
        if len(compiled) != 1:
            raise SegmentRuleError("NOT requires exactly one child")
        return not_(compiled[0])

    # Leaf
    if node.get("type") != "rule":
        raise SegmentRuleError(
            "Leaf nodes must declare `type: 'rule'` and a field"
        )
    field_key = str(node.get("field") or "")
    comparator = str(node.get("comparator") or "")
    raw_value = node.get("value")

    spec = get_field_spec(field_key)
    if spec is None:
        raise SegmentRuleError(f"Unknown field {field_key!r}")
    if comparator not in spec.comparators:
        raise SegmentRuleError(
            f"Comparator {comparator!r} not allowed for field {field_key!r}"
        )
    value = validate_value(spec, comparator, raw_value)
    return _compile_leaf(spec, comparator, value)


def _compile_leaf(
    spec: FieldSpec, comparator: str, value: Any
) -> ColumnElement[bool]:
    if spec.relation == "tags":
        return _compile_tag_leaf(comparator, value)
    if spec.relation in {"external_refs.system", "external_refs.account_id"}:
        return _compile_external_ref_leaf(spec, comparator, value)
    if spec.relation in {"pipeline_id", "pipeline_stage_id"}:
        return _compile_pipeline_leaf(spec, comparator, value)

    column = spec.column
    if column is None and "concat" in spec.extras:
        first, last = spec.extras["concat"]
        column = func.coalesce(getattr(Contact, first), "") + " " + func.coalesce(
            getattr(Contact, last), ""
        )
    if column is None:
        raise SegmentRuleError(f"Field {spec.key!r} has no resolved column")

    return _compile_column_leaf(column, spec, comparator, value)


def _compile_column_leaf(
    column: ColumnElement,
    spec: FieldSpec,
    comparator: str,
    value: Any,
) -> ColumnElement[bool]:
    if comparator == "is_null":
        return column.is_(None)
    if comparator == "is_not_null":
        return column.is_not(None)
    if comparator == "eq":
        return column == value
    if comparator == "neq":
        return column != value
    if comparator == "contains":
        return column.ilike(f"%{value}%")
    if comparator == "not_contains":
        return ~column.ilike(f"%{value}%")
    if comparator == "starts_with":
        return column.ilike(f"{value}%")
    if comparator == "in":
        return column.in_(value)
    if comparator == "not_in":
        return ~column.in_(value)
    if comparator == "gt":
        return column > value
    if comparator == "gte":
        return column >= value
    if comparator == "lt":
        return column < value
    if comparator == "lte":
        return column <= value
    if comparator == "between":
        low, high = value
        return column.between(low, high)
    if comparator == "before":
        return column < _to_datetime(value)
    if comparator == "after":
        return column > _to_datetime(value)
    if comparator == "in_last_n_days":
        boundary = _now() - timedelta(days=int(value))
        return column >= boundary
    if comparator == "not_in_last_n_days":
        boundary = _now() - timedelta(days=int(value))
        return or_(column.is_(None), column < boundary)
    raise SegmentRuleError(f"Unsupported comparator {comparator!r}")


def _compile_tag_leaf(comparator: str, value: list[str]) -> ColumnElement[bool]:
    """Build an EXISTS subquery against `contact_tags`.

    - contains_any: ≥1 of the listed tag ids is attached.
    - contains_all: every listed tag id is attached.
    - contains_none: NONE of the listed tag ids is attached.
    """
    if comparator == "contains_any":
        return Contact.id.in_(
            select(ContactTag.contact_id).where(
                ContactTag.tag_id.in_(value)
            )
        )
    if comparator == "contains_all":
        return Contact.id.in_(
            select(ContactTag.contact_id)
            .where(ContactTag.tag_id.in_(value))
            .group_by(ContactTag.contact_id)
            .having(func.count(func.distinct(ContactTag.tag_id)) == len(value))
        )
    if comparator == "contains_none":
        return ~Contact.id.in_(
            select(ContactTag.contact_id).where(
                ContactTag.tag_id.in_(value)
            )
        )
    raise SegmentRuleError(f"Unsupported tag comparator {comparator!r}")


def _compile_external_ref_leaf(
    spec: FieldSpec, comparator: str, value: Any
) -> ColumnElement[bool]:
    column = (
        ExternalReference.system
        if spec.relation == "external_refs.system"
        else ExternalReference.account_id
    )
    subq = select(ExternalReference.contact_id)
    if comparator == "eq":
        subq = subq.where(column == value)
        return Contact.id.in_(subq)
    if comparator == "neq":
        subq = subq.where(column == value)
        return ~Contact.id.in_(subq)
    if comparator == "in":
        subq = subq.where(column.in_(value))
        return Contact.id.in_(subq)
    if comparator == "not_in":
        subq = subq.where(column.in_(value))
        return ~Contact.id.in_(subq)
    raise SegmentRuleError(f"Unsupported origin comparator {comparator!r}")


def _compile_pipeline_leaf(
    spec: FieldSpec, comparator: str, value: list[str]
) -> ColumnElement[bool]:
    column = (
        ContactPipelineStage.pipeline_id
        if spec.relation == "pipeline_id"
        else ContactPipelineStage.stage_id
    )
    subq = select(ContactPipelineStage.contact_id).where(
        ContactPipelineStage.is_archived.is_(False),
        column.in_(value),
    )
    if comparator == "in":
        return Contact.id.in_(subq)
    if comparator == "not_in":
        return ~Contact.id.in_(subq)
    raise SegmentRuleError(
        f"Unsupported pipeline comparator {comparator!r}"
    )


def _true() -> ColumnElement[bool]:
    """Portable 1=1 for SQLite + MySQL. SQLAlchemy's `true()` literal
    would be ideal but it falls back to `1` on MySQL strict modes."""
    return Contact.id == Contact.id


def _now() -> datetime:
    return datetime.now(UTC)


def _to_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise SegmentRuleError(
                f"Expected ISO date, got {value!r}"
            ) from exc
    raise SegmentRuleError(f"Expected date, got {type(value).__name__}")


# ---------------------------------------------------------------------------
# In-memory evaluator (for future Sprint E hooks).
# ---------------------------------------------------------------------------


def evaluate_contact_against_rules(
    contact: Contact, tree: dict[str, Any]
) -> bool:
    """Return True iff the in-memory contact would match the tree.
    Reads only what's already on the ORM instance — no DB queries —
    so the future event hook can decide in O(1) whether a contact
    just entered / left a segment."""
    if not tree:
        return True
    return _evaluate(contact, tree, depth=0)


def _evaluate(contact: Contact, node: dict[str, Any], *, depth: int) -> bool:
    if depth > MAX_DEPTH:
        raise SegmentRuleError("Rule tree exceeds max depth")
    operator = node.get("operator")
    if operator:
        op_upper = str(operator).upper()
        children = node.get("children") or []
        results = [_evaluate(contact, child, depth=depth + 1) for child in children]
        if op_upper == "AND":
            return all(results) if results else True
        if op_upper == "OR":
            return any(results)
        if op_upper == "NOT":
            return not results[0] if results else False
        raise SegmentRuleError(f"Unknown logical operator {operator!r}")

    spec = get_field_spec(str(node.get("field") or ""))
    if spec is None:
        return False
    comparator = str(node.get("comparator") or "")
    if comparator not in spec.comparators:
        return False
    value = validate_value(spec, comparator, node.get("value"))
    return _evaluate_leaf(contact, spec, comparator, value)


def _evaluate_leaf(
    contact: Contact, spec: FieldSpec, comparator: str, value: Any
) -> bool:
    actual = _resolve_attr(contact, spec)
    if comparator == "is_null":
        return actual is None
    if comparator == "is_not_null":
        return actual is not None
    if actual is None and comparator not in {"contains_none"}:
        return False
    if comparator == "eq":
        return actual == value
    if comparator == "neq":
        return actual != value
    if comparator == "contains":
        return value.lower() in str(actual).lower()
    if comparator == "not_contains":
        return value.lower() not in str(actual).lower()
    if comparator == "starts_with":
        return str(actual).lower().startswith(value.lower())
    if comparator == "in":
        return actual in value
    if comparator == "not_in":
        return actual not in value
    if comparator == "gt":
        return actual > value
    if comparator == "gte":
        return actual >= value
    if comparator == "lt":
        return actual < value
    if comparator == "lte":
        return actual <= value
    if comparator == "between":
        low, high = value
        return low <= actual <= high
    if comparator == "before":
        return actual < _to_datetime(value)
    if comparator == "after":
        return actual > _to_datetime(value)
    if comparator == "in_last_n_days":
        boundary = _now() - timedelta(days=int(value))
        return actual >= boundary
    if comparator == "not_in_last_n_days":
        boundary = _now() - timedelta(days=int(value))
        return actual < boundary
    if comparator in {"contains_any", "contains_all", "contains_none"}:
        # Tags-on-contact in memory: contact.tag_objects gives us the
        # current Tag list.
        ids = {tag.id for tag in getattr(contact, "tag_objects", [])}
        if comparator == "contains_any":
            return bool(ids & set(value))
        if comparator == "contains_all":
            return set(value).issubset(ids)
        return not (ids & set(value))
    return False


def _resolve_attr(contact: Contact, spec: FieldSpec) -> Any:
    if "concat" in spec.extras:
        first, last = spec.extras["concat"]
        return " ".join(
            part
            for part in (getattr(contact, first), getattr(contact, last))
            if part
        ).strip() or None
    if spec.relation == "external_refs.system":
        return next(
            (ref.system.value for ref in contact.external_refs),
            None,
        )
    if spec.relation == "external_refs.account_id":
        return next(
            (ref.account_id for ref in contact.external_refs),
            None,
        )
    if spec.column is not None:
        # InstrumentedAttribute → attribute name.
        attr_name = spec.column.key  # type: ignore[union-attr]
        return getattr(contact, attr_name)
    return None


# Helpers kept here so the import surface is one module.
__all__ = [
    "SegmentRuleError",
    "build_filter",
    "evaluate_contact_against_rules",
    "FIELD_SPECS",
    "Mapped",  # re-export so type-hint imports don't break the engine consumer
    "Boolean",
    "String",
    "exists",
]
