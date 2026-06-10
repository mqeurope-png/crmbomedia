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
  gt: ">",
  gte: ">=",
  lt: "<",
  lte: "<=",
  between: "between",
  before: "<",
  after: ">",
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
