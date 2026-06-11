/**
 * Translate between react-querybuilder's `RuleGroupType` shape and
 * our backend's `{operator, children}` / `{type: 'rule', field,
 * comparator, value}` tree.
 *
 * The two formats describe the same boolean algebra; this file is
 * the only place where the impedance mismatch lives.
 */
import type { RuleGroupType, RuleType } from "react-querybuilder";

type BackendNode = Record<string, unknown>;

/**
 * Backend comparator → react-querybuilder operator NAME.
 *
 * We deliberately use the backend comparator string verbatim instead
 * of the math symbol (`>`, `<`, etc.). The previous map sent BOTH
 * `gt → ">"` and `after → ">"` (same for `lt`/`before`), and reversing
 * collapsed `">" → "after"`, so a numeric `lead_score > 5` was sent
 * to the backend as `comparator: "after"` and 400'd with
 * "Comparator 'after' not allowed for field 'lead_score'".
 *
 * Each field config in `ContactQueryBuilder` declares only the
 * comparators valid for its type, so the dropdown never shows
 * date-only operators on a numeric column.
 */
const OPERATOR_MAP_TO_QB: Record<string, string> = {
  eq: "=",
  neq: "!=",
  contains: "contains",
  not_contains: "doesNotContain",
  starts_with: "beginsWith",
  is_null: "null",
  is_not_null: "notNull",
  in: "in",
  not_in: "notIn",
  // Numeric comparators keep distinct QB names so the reverse map
  // round-trips cleanly.
  gt: "gt",
  gte: "gte",
  lt: "lt",
  lte: "lte",
  between: "between",
  // Date comparators do the same; they only ever appear on date
  // fields where `gt`/`lt` are not in the field's operator list.
  before: "before",
  after: "after",
  in_last_n_days: "in_last_n_days",
  not_in_last_n_days: "not_in_last_n_days",
  contains_any: "contains_any",
  contains_all: "contains_all",
  contains_none: "contains_none",
};

const OPERATOR_MAP_TO_BACKEND: Record<string, string> = Object.fromEntries(
  Object.entries(OPERATOR_MAP_TO_QB).map(([backend, qb]) => [qb, backend]),
);

export function backendOpToQB(comparator: string): string {
  return OPERATOR_MAP_TO_QB[comparator] ?? comparator;
}

export function qbOpToBackend(operator: string): string {
  return OPERATOR_MAP_TO_BACKEND[operator] ?? operator;
}

/** Empty group used by the builder when the operator opens a fresh
 * segment with no saved rules yet. */
export const EMPTY_QB_GROUP: RuleGroupType = {
  combinator: "and",
  rules: [],
};

export function backendToQB(tree: BackendNode): RuleGroupType {
  if (!tree || Object.keys(tree).length === 0) return EMPTY_QB_GROUP;
  const operator = (tree.operator as string | undefined)?.toLowerCase();
  if (operator) {
    if (operator === "not") {
      // react-querybuilder represents NOT via a `not: true` flag on a
      // group of one child.
      const children = (tree.children as BackendNode[]) ?? [];
      const inner = children[0]
        ? backendToQB(children[0])
        : EMPTY_QB_GROUP;
      return { combinator: "and", rules: [inner], not: true };
    }
    return {
      combinator: operator === "or" ? "or" : "and",
      rules: ((tree.children as BackendNode[]) ?? []).map((child) => {
        if (child.operator) return backendToQB(child);
        return backendRuleToQB(child);
      }),
    };
  }
  if (tree.type === "rule") {
    return {
      combinator: "and",
      rules: [backendRuleToQB(tree)],
    };
  }
  return EMPTY_QB_GROUP;
}

function backendRuleToQB(rule: BackendNode): RuleType {
  return {
    field: String(rule.field ?? ""),
    operator: backendOpToQB(String(rule.comparator ?? "eq")),
    value: rule.value as RuleType["value"],
  };
}

export function qbToBackend(group: RuleGroupType): BackendNode {
  if (group.not) {
    return {
      operator: "NOT",
      children: [
        group.rules[0] && "combinator" in group.rules[0]
          ? qbToBackend(group.rules[0] as RuleGroupType)
          : group.rules[0]
            ? qbRuleToBackend(group.rules[0] as RuleType)
            : {},
      ],
    };
  }
  return {
    operator: group.combinator.toUpperCase(),
    children: group.rules.map((rule) =>
      "combinator" in rule
        ? qbToBackend(rule as RuleGroupType)
        : qbRuleToBackend(rule as RuleType),
    ),
  };
}

function qbRuleToBackend(rule: RuleType): BackendNode {
  return {
    type: "rule",
    field: rule.field,
    comparator: qbOpToBackend(rule.operator),
    value: rule.value,
  };
}

// ---------------------------------------------------------------------------
// Prune + coerce — keep half-typed rules away from the backend
// ---------------------------------------------------------------------------

type FieldSpecLite = {
  key: string;
  type: string; // string | int | bool | date | enum | tag-multi | uuid-multi
};

const NO_VALUE_COMPARATORS = new Set(["is_null", "is_not_null"]);
const LIST_COMPARATORS = new Set([
  "in",
  "not_in",
  "contains_any",
  "contains_all",
  "contains_none",
]);

/** Drop unfinished rules and coerce values to the engine's expected
 * types before a tree leaves the browser.
 *
 * Production bugs this guards against:
 * - tags / in_brevo_list rules with an empty list 400'd with
 *   "requires a non-empty list" the moment they were added.
 * - lead_score values travelled as strings → "Expected int".
 * - date values travelled empty or non-ISO → "Expected ISO date".
 *
 * The UI keeps the half-typed rule on screen (the QB tree is separate
 * state); only the emitted tree is pruned, so the operator perceives
 * "the filter doesn't apply until I give it a value".
 *
 * Returns `{}` when nothing survives — the engine treats that as
 * "match everything".
 */
export function pruneRulesTree(
  tree: BackendNode,
  specs: FieldSpecLite[],
): BackendNode {
  const typeByField: Record<string, string> = {};
  for (const spec of specs) typeByField[spec.key] = spec.type;
  const pruned = pruneNode(tree, typeByField);
  return pruned ?? {};
}

function pruneNode(
  node: BackendNode,
  typeByField: Record<string, string>,
): BackendNode | null {
  if (!node || Object.keys(node).length === 0) return null;
  const operator = (node.operator as string | undefined)?.toUpperCase();
  if (operator) {
    const children = ((node.children as BackendNode[]) ?? [])
      .map((child) => pruneNode(child, typeByField))
      .filter((child): child is BackendNode => child !== null);
    if (children.length === 0) return null;
    if (operator === "NOT") {
      // NOT of a pruned-away child means "no filter", not "everything
      // except nothing".
      return { operator: "NOT", children: [children[0]] };
    }
    if (children.length === 1) return children[0];
    return { operator, children };
  }
  if (node.type === "rule") {
    return pruneRule(node, typeByField);
  }
  return null;
}

function pruneRule(
  rule: BackendNode,
  typeByField: Record<string, string>,
): BackendNode | null {
  const comparator = String(rule.comparator ?? "");
  if (NO_VALUE_COMPARATORS.has(comparator)) return rule;

  const fieldType = typeByField[String(rule.field ?? "")] ?? "string";
  const value = rule.value;

  if (LIST_COMPARATORS.has(comparator)) {
    if (!Array.isArray(value) || value.length === 0) return null;
    const items = value
      .map((item) => coerceScalar(item, fieldType))
      .filter((item) => item !== null && item !== "");
    if (items.length === 0) return null;
    return { ...rule, value: items };
  }

  if (comparator === "between") {
    if (!Array.isArray(value) || value.length !== 2) return null;
    const low = coerceScalar(value[0], fieldType);
    const high = coerceScalar(value[1], fieldType);
    if (low === null || low === "" || high === null || high === "") return null;
    return { ...rule, value: [low, high] };
  }

  if (comparator === "in_last_n_days" || comparator === "not_in_last_n_days") {
    const days = coerceScalar(value, "int");
    if (days === null) return null;
    return { ...rule, value: days };
  }

  const coerced = coerceScalar(value, fieldType);
  if (coerced === null || coerced === "") return null;
  return { ...rule, value: coerced };
}

/** Per-type scalar coercion. Returns null for values that can't be
 * coerced (the rule gets dropped rather than 400ing server-side). */
function coerceScalar(value: unknown, fieldType: string): unknown {
  if (value === null || value === undefined) return null;
  if (fieldType === "int") {
    if (typeof value === "number") return Number.isNaN(value) ? null : value;
    const parsed = Number.parseInt(String(value).trim(), 10);
    return Number.isNaN(parsed) ? null : parsed;
  }
  if (fieldType === "bool") {
    if (typeof value === "boolean") return value;
    if (value === "true") return true;
    if (value === "false") return false;
    return null;
  }
  if (fieldType === "date") {
    const raw = String(value).trim();
    if (!raw) return null;
    // <input type="date"> emits YYYY-MM-DD which the backend's
    // fromisoformat accepts as-is; anything else gets normalised
    // through Date → ISO, dropped when unparseable.
    if (/^\d{4}-\d{2}-\d{2}$/.test(raw)) return raw;
    const parsed = new Date(raw);
    if (Number.isNaN(parsed.getTime())) return null;
    return parsed.toISOString();
  }
  if (typeof value === "string") {
    const trimmed = value.trim();
    return trimmed === "" ? null : trimmed;
  }
  return value;
}
