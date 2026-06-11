import type { SavedViewFilters } from "./api";

type BackendNode = Record<string, unknown>;

/** Convert a legacy `SavedViewFilters` (flat dropdown values stored in
 * `contact_views.filters_json` pre-Sprint-UX) into the segments-engine
 * `{operator: AND, children: [...]}` rule tree. The new query builder
 * consumes the engine shape directly; this helper keeps old saved
 * views functional without a backend migration.
 *
 * Returns an empty object `{}` when no useful filter survives — the
 * engine treats that as "match every contact", same as the bare
 * `GET /api/contacts` did.
 */
export function legacyFiltersToRulesTree(
  filters: SavedViewFilters | null | undefined,
): BackendNode {
  if (!filters) return {};
  // If the view already carries a `rules_json` tree (Sprint UX views),
  // it wins over every legacy field.
  if (
    filters.rules_json &&
    typeof filters.rules_json === "object" &&
    !Array.isArray(filters.rules_json) &&
    Object.keys(filters.rules_json as Record<string, unknown>).length > 0
  ) {
    return filters.rules_json as BackendNode;
  }
  const rules: BackendNode[] = [];

  if (filters.q && typeof filters.q === "string" && filters.q.trim()) {
    // Mirror the backend's `q` semantics: OR across first_name /
    // last_name / email / phone with `contains`. The new query builder
    // shows this as a nested group the user can edit further.
    rules.push({
      operator: "OR",
      children: ["first_name", "last_name", "email", "phone"].map((field) => ({
        type: "rule",
        field,
        comparator: "contains",
        value: filters.q,
      })),
    });
  }
  if (filters.tag_ids && filters.tag_ids.length > 0) {
    const matchMode = filters.tag_match_mode === "all" ? "contains_all" : "contains_any";
    rules.push({
      type: "rule",
      field: "tags",
      comparator: matchMode,
      value: filters.tag_ids,
    });
  }
  if (filters.origin_account_keys && filters.origin_account_keys.length > 0) {
    // Engine doesn't have a multi-system+account field; fall back to
    // the simpler `origin_system` enum. Operators with mixed-system
    // legacy views see a slightly broader match here but never wider
    // than before (origin_system still scopes the rest).
    const systems = Array.from(
      new Set(
        filters.origin_account_keys
          .map((key) => key.split(":")[0])
          .filter((s) => Boolean(s)),
      ),
    );
    if (systems.length > 0) {
      rules.push({
        type: "rule",
        field: "origin_system",
        comparator: systems.length === 1 ? "eq" : "in",
        value: systems.length === 1 ? systems[0] : systems,
      });
    }
  } else if (filters.origin_system) {
    rules.push({
      type: "rule",
      field: "origin_system",
      comparator: "eq",
      value: filters.origin_system,
    });
  }
  if (filters.commercial_status) {
    rules.push({
      type: "rule",
      field: "commercial_status",
      comparator: "eq",
      value: filters.commercial_status,
    });
  }
  if (filters.marketing_consent) {
    rules.push({
      type: "rule",
      field: "marketing_consent",
      comparator: "eq",
      value: filters.marketing_consent,
    });
  }
  if (filters.is_active === false) {
    rules.push({
      type: "rule",
      field: "is_active",
      comparator: "eq",
      value: false,
    });
  }
  if (filters.lead_score_min != null && filters.lead_score_max != null) {
    rules.push({
      type: "rule",
      field: "lead_score",
      comparator: "between",
      value: [filters.lead_score_min, filters.lead_score_max],
    });
  } else if (filters.lead_score_min != null) {
    rules.push({
      type: "rule",
      field: "lead_score",
      comparator: "gte",
      value: filters.lead_score_min,
    });
  } else if (filters.lead_score_max != null) {
    rules.push({
      type: "rule",
      field: "lead_score",
      comparator: "lte",
      value: filters.lead_score_max,
    });
  }

  if (rules.length === 0) return {};
  if (rules.length === 1) return rules[0];
  return { operator: "AND", children: rules };
}
