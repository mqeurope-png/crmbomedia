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

from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field as _dc_field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.services.entities.registry import EntityDescriptor

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
    ContactAssignment,
    ContactPipelineStage,
    ContactTag,
    ExternalReference,
    Tag,
)
from app.services.segments.fields import (
    FIELD_SPECS,
    FieldSpec,
    get_field_spec,
    validate_value,
)

MAX_DEPTH = 10
LOGICAL_OPERATORS = {"AND", "OR", "NOT"}


@dataclass(frozen=True)
class _EntityCtx:
    """Per-entity compilation context. Sprint Filtros & Listas (PR-A)
    lifted the engine off the hardcoded `Contact` so the same tree
    grammar compiles against companies / email threads / Brevo caches.

    `field_specs` is the entity's whitelist (the anti-injection
    boundary); `id_column` anchors the `_true`/`_false` tautologies and
    the relation `EXISTS` subqueries; `base_model` resolves the `name`
    concat. The relation join handlers (tags, pipelines, external_refs,
    segment/brevo membership) remain Contact-specific and are only
    reached when a Contact field declares that relation — other
    entities simply don't register those fields.
    """

    field_specs: Mapping[str, FieldSpec]
    id_column: Any
    base_model: Any
    resolver: SegmentResolver | None = None
    visited: set[str] = _dc_field(default_factory=set)


def _contact_ctx(resolver: SegmentResolver | None) -> _EntityCtx:
    return _EntityCtx(
        field_specs=FIELD_SPECS,
        id_column=Contact.id,
        base_model=Contact,
        resolver=resolver,
    )

#: Callable that returns the `rules_json` tree for a segment id, or
#: None when the id doesn't resolve. The route layer passes a
#: closure backed by the SQLAlchemy session; tests pass an in-memory
#: dict. Without a resolver the `in_segment` field can't be compiled
#: and the engine raises a clear error.
SegmentResolver = Any  # Callable[[str, set[str]], dict | None]


class SegmentRuleError(ValueError):
    """Operator-supplied tree failed validation. The route maps it to
    400 so the UI can highlight the offending node."""


def build_filter(
    tree: dict[str, Any],
    *,
    segment_resolver: SegmentResolver | None = None,
) -> ColumnElement[bool]:
    """Compile the rule tree into a SQLAlchemy boolean expression for
    **contacts** (back-compat entry point — segments, `/contacts/search`,
    Brevo target sync all call this).

    Empty / falsy input compiles to `True` so a brand-new segment
    matches every contact instead of crashing the preview.

    `segment_resolver` is consulted when the tree references
    `in_segment` — the route layer passes a session-backed lookup; in
    its absence the engine raises `SegmentRuleError` instead of
    silently ignoring the rule.
    """
    ctx = _contact_ctx(segment_resolver)
    if not tree:
        return _true(ctx)
    return _compile(tree, ctx=ctx, depth=0)


def build_entity_filter(
    entity: EntityDescriptor,
    tree: dict[str, Any],
    *,
    segment_resolver: SegmentResolver | None = None,
) -> ColumnElement[bool]:
    """Sprint Filtros & Listas (PR-A): compile a rule tree against an
    arbitrary registered entity. The Contact path stays on `build_filter`
    for byte-for-byte back-compat; this is the generic sibling the
    unified `/api/{entity}/search` will use in PR-B.
    """
    ctx = _EntityCtx(
        field_specs=entity.field_specs,
        id_column=entity.id_column,
        base_model=entity.base_model,
        resolver=segment_resolver,
    )
    if not tree:
        return _true(ctx)
    return _compile(tree, ctx=ctx, depth=0)


def _compile(
    node: dict[str, Any],
    *,
    ctx: _EntityCtx,
    depth: int,
) -> ColumnElement[bool]:
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
            # filter so the operator sees their full universe.
            return _true(ctx)
        compiled = [
            _compile(child, ctx=ctx, depth=depth + 1)
            for child in children
        ]
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

    spec = ctx.field_specs.get(field_key)
    if spec is None:
        raise SegmentRuleError(f"Unknown field {field_key!r}")
    if comparator not in spec.comparators:
        raise SegmentRuleError(
            f"Comparator {comparator!r} not allowed for field {field_key!r}"
        )
    try:
        value = validate_value(spec, comparator, raw_value)
    except ValueError as exc:
        # `validate_value` raises plain ValueError on type mismatches
        # (e.g. tags expects a list of UUIDs but the operator sent a
        # string). Without this wrap the route layer would see an
        # unhandled exception and return 500 instead of a 400 that the
        # UI can show next to the offending row.
        raise SegmentRuleError(
            f"Campo {spec.key!r}: {exc}"
        ) from exc
    return _compile_leaf(spec, comparator, value, ctx=ctx, depth=depth)


def _compile_leaf(
    spec: FieldSpec,
    comparator: str,
    value: Any,
    *,
    ctx: _EntityCtx,
    depth: int = 0,
) -> ColumnElement[bool]:
    # Relation leaves are Contact-specific joins; only Contact's
    # registry declares these relations, so the generic entities never
    # reach this branch.
    if spec.relation == "segment_membership":
        return _compile_segment_membership(
            comparator, value, ctx=ctx, depth=depth
        )
    if spec.relation == "tags":
        return _compile_tag_leaf(comparator, value)
    if spec.relation in {"external_refs.system", "external_refs.account_id"}:
        return _compile_external_ref_leaf(spec, comparator, value)
    if spec.relation in {"pipeline_id", "pipeline_stage_id"}:
        return _compile_pipeline_leaf(spec, comparator, value)
    if spec.relation == "brevo_list_membership":
        return _compile_brevo_list_leaf(comparator, value)
    # Sprint Reglas-Assign PR-B: assigned_users (M:N a contact_assignments,
    # primary + secondaries) y primary_user (filtrado por la fila primary).
    if spec.relation == "assignments":
        return _compile_assignment_leaf(comparator, value)
    if spec.relation == "primary_assignment":
        return _compile_primary_assignment_leaf(comparator, value)

    column = spec.column
    if column is None and "concat" in spec.extras:
        first, last = spec.extras["concat"]
        column = func.coalesce(getattr(ctx.base_model, first), "") + " " + func.coalesce(
            getattr(ctx.base_model, last), ""
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
    if comparator == "ends_with":
        return column.ilike(f"%{value}")
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
    if comparator == "older_than_n_days":
        # "hace más de N días" — matches rows whose date is at least
        # N days old. Symmetric to in_last_n_days for "stale lead"
        # filters.
        boundary = _now() - timedelta(days=int(value))
        return column < boundary
    raise SegmentRuleError(f"Unsupported comparator {comparator!r}")


def _compile_tag_leaf(comparator: str, value: Any) -> ColumnElement[bool]:
    """Build an EXISTS subquery against `contact_tags`.

    - contains_any: ≥1 of the listed tag ids is attached.
    - contains_all: every listed tag id is attached.
    - contains_none: NONE of the listed tag ids is attached.
    - tag_name_contains: ≥1 attached tag's name matches the substring
      (case-insensitive LIKE). Useful for the "mbo" → "mbo-cliente" /
      "brevo-list:mbo-x" pattern where the operator doesn't want to
      cherry-pick from the tag picker. PR-Cc.
    """
    if comparator == "tag_name_contains":
        if not isinstance(value, str):
            raise SegmentRuleError(
                "tag_name_contains requires a string value"
            )
        needle = value.strip().lower()
        if not needle:
            raise SegmentRuleError(
                "tag_name_contains requires a non-empty string"
            )
        return Contact.id.in_(
            select(ContactTag.contact_id)
            .join(Tag, Tag.id == ContactTag.tag_id)
            .where(func.lower(Tag.name).like(f"%{needle}%"))
        )
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


def _compile_assignment_leaf(
    comparator: str, value: Any
) -> ColumnElement[bool]:
    """`assigned_users`: EXISTS sobre `contact_assignments` cubriendo
    primary + secundarios. El operador "asignado a X" matchea tanto si
    X es el primary como un watcher.

    - contains_any: ≥1 de los user_ids tiene una assignment al contacto.
    - contains_all: TODOS los user_ids tienen assignment al contacto.
    - is_empty: el contacto no tiene NINGUNA assignment (== "Sin asignar"
      multi-comercial). El operador antiguo "owner_user_id is_null" sigue
      válido vía la FieldSpec heredada.
    - is_not_empty: el contacto tiene ≥1 assignment.
    """
    if comparator == "contains_any":
        return Contact.id.in_(
            select(ContactAssignment.contact_id).where(
                ContactAssignment.user_id.in_(value)
            )
        )
    if comparator == "contains_all":
        return Contact.id.in_(
            select(ContactAssignment.contact_id)
            .where(ContactAssignment.user_id.in_(value))
            .group_by(ContactAssignment.contact_id)
            .having(
                func.count(func.distinct(ContactAssignment.user_id))
                == len(value)
            )
        )
    if comparator == "is_empty":
        return ~Contact.id.in_(select(ContactAssignment.contact_id))
    if comparator == "is_not_empty":
        return Contact.id.in_(select(ContactAssignment.contact_id))
    raise SegmentRuleError(
        f"Unsupported assigned_users comparator {comparator!r}"
    )


def _compile_primary_assignment_leaf(
    comparator: str, value: Any
) -> ColumnElement[bool]:
    """`primary_user`: filtro contra el comercial primary (responsable).

    Compila contra `contact_assignments WHERE is_primary` en vez de
    `Contact.owner_user_id` para no depender del estado del caché — el
    caché puede quedar momentáneamente desfasado durante un set_primary
    transaccional. Equivalente funcional con la fuente de verdad.
    """
    primary = select(ContactAssignment.contact_id).where(
        ContactAssignment.is_primary.is_(True)
    )
    if comparator == "eq":
        return Contact.id.in_(
            primary.where(ContactAssignment.user_id == value)
        )
    if comparator == "neq":
        return ~Contact.id.in_(
            primary.where(ContactAssignment.user_id == value)
        )
    if comparator == "is_null":
        return ~Contact.id.in_(primary)
    if comparator == "is_not_null":
        return Contact.id.in_(primary)
    raise SegmentRuleError(
        f"Unsupported primary_user comparator {comparator!r}"
    )


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


def _compile_brevo_list_leaf(
    comparator: str, value: list[Any]
) -> ColumnElement[bool]:
    """Match contacts whose Brevo `external_references.metadata` has any
    of the given list ids in its `list_ids` array.

    The brevo mapper writes `metadata.list_ids` as a JSON array of
    ints. We don't have a per-(contact, list) row, so we LIKE-scan the
    JSON text with anchored patterns that survive the mapper's
    `json.dumps` default formatting (`[4, 7]`, separator is `, `).
    Anchoring both sides with `[` / `, ` / `]` rules out the
    `12 matches 1` false positive."""
    # ExternalSystem.BREVO lives in app.models.crm — imported lazily
    # so the engine module stays decoupled from the enum value.
    from app.models.crm import ExternalSystem  # noqa: PLC0415

    # PR-Ce: el editor de `in_brevo_list` cae a CsvEditor si no hay
    # picker → el operador puede teclear "fespa" (intentando el nombre
    # de la lista) y `int("fespa")` 500-eaba el endpoint. Capturamos el
    # parse y devolvemos 400 con un mensaje accionable. El picker nuevo
    # en el frontend (BrevoListPicker) ya emite los ids numéricos
    # correctos, pero esto blinda el motor frente a clientes legacy o
    # peticiones manuales.
    list_ids: list[str] = []
    for item in value:
        try:
            list_ids.append(str(int(item)))
        except (TypeError, ValueError) as exc:
            raise SegmentRuleError(
                f"`in_brevo_list` espera ids numéricos de lista Brevo "
                f"(recibido {item!r}). Usa el selector para elegir la "
                f"lista en vez de teclear el nombre."
            ) from exc
    patterns: list[Any] = []
    for lid in list_ids:
        patterns.append(
            ExternalReference.metadata_json.like(f'%"list_ids": [{lid}]%')
        )
        patterns.append(
            ExternalReference.metadata_json.like(f'%"list_ids": [{lid}, %')
        )
        patterns.append(
            ExternalReference.metadata_json.like(f'%, {lid}]%')
        )
        patterns.append(
            ExternalReference.metadata_json.like(f'%, {lid}, %')
        )
    subq = (
        select(ExternalReference.contact_id)
        .where(ExternalReference.system == ExternalSystem.BREVO)
        .where(ExternalReference.metadata_json.is_not(None))
        .where(or_(*patterns))
    )
    if comparator == "in":
        return Contact.id.in_(subq)
    if comparator == "not_in":
        return ~Contact.id.in_(subq)
    raise SegmentRuleError(
        f"Unsupported brevo list comparator {comparator!r}"
    )


def _compile_segment_membership(
    comparator: str,
    value: list[str],
    *,
    ctx: _EntityCtx,
    depth: int,
) -> ColumnElement[bool]:
    """`in_segment` references other segments by id; each referenced
    segment's rules tree is loaded via the ctx resolver and compiled in
    place (OR'd across the listed ids). Cycles are detected via the
    ctx `visited` set so a segment that references itself can't loop.

    Contact-only: segments are a Contact concept, so the recursive
    `_compile` runs with the same Contact ctx."""
    if ctx.resolver is None:
        raise SegmentRuleError(
            "in_segment requires a segment_resolver; pass one via build_filter"
        )
    sub_filters: list[ColumnElement[bool]] = []
    for segment_id in value:
        sid = str(segment_id)
        if sid in ctx.visited:
            raise SegmentRuleError(
                f"in_segment cycle detected at segment {sid!r}"
            )
        sub_tree = ctx.resolver(sid, ctx.visited)
        if sub_tree is None:
            # Unknown segment id → contributes no matches. Skipping
            # silently would be too kind (it'd match every contact via
            # the AND-empty rule), so we add a dead clause that filters
            # nothing out.
            sub_filters.append(_false(ctx))
            continue
        sub_ctx = _EntityCtx(
            field_specs=ctx.field_specs,
            id_column=ctx.id_column,
            base_model=ctx.base_model,
            resolver=ctx.resolver,
            visited=ctx.visited | {sid},
        )
        sub_filters.append(_compile(sub_tree, ctx=sub_ctx, depth=depth + 1))
    if not sub_filters:
        return _false(ctx) if comparator == "in" else _true(ctx)
    combined = or_(*sub_filters) if len(sub_filters) > 1 else sub_filters[0]
    return combined if comparator == "in" else not_(combined)


def _false(ctx: _EntityCtx) -> ColumnElement[bool]:
    return ctx.id_column != ctx.id_column


def _true(ctx: _EntityCtx) -> ColumnElement[bool]:
    """Portable 1=1 for SQLite + MySQL. SQLAlchemy's `true()` literal
    would be ideal but it falls back to `1` on MySQL strict modes."""
    return ctx.id_column == ctx.id_column


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
    try:
        value = validate_value(spec, comparator, node.get("value"))
    except ValueError:
        return False
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
    if comparator == "ends_with":
        return str(actual).lower().endswith(value.lower())
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
    if comparator == "older_than_n_days":
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
    "build_entity_filter",
    "evaluate_contact_against_rules",
    "FIELD_SPECS",
    "Mapped",  # re-export so type-hint imports don't break the engine consumer
    "Boolean",
    "String",
    "exists",
]
