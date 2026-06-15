import { apiFetch } from "./api";

// Sprint Filtros & Listas (PR-A) — shared declarative schema types.
// The backend emits these per entity at
// `GET /api/entities/{entity}/filter-schema`; the unified
// `<EntityTable>` + `<EntityFilterBuilder>` (PR-C/PR-D) consume them to
// build both the column configurator and the filter dropdowns.

export type FieldType =
  | "string"
  | "int"
  | "number"
  | "date"
  | "datetime"
  | "bool"
  | "boolean"
  | "enum"
  | "reference"
  | "tag-multi"
  | "uuid-multi"
  | "json";

// Operator vocabulary = the rule engine's comparators (the persisted
// IR). The filter builder (react-querybuilder, PR-D) translates its own
// operator names to/from these via the RQB⇄IR translator; this is the
// canonical set.
export type Operator =
  | "is_null"
  | "is_not_null"
  | "eq"
  | "neq"
  | "in"
  | "not_in"
  | "contains"
  | "not_contains"
  | "starts_with"
  | "ends_with"
  | "gt"
  | "gte"
  | "lt"
  | "lte"
  | "between"
  | "before"
  | "after"
  | "in_last_n_days"
  | "not_in_last_n_days"
  | "older_than_n_days"
  | "contains_any"
  | "contains_all"
  | "contains_none";

export type FieldSource =
  | "column"
  | "custom_fields_json"
  | "computed"
  | "related_table";

export type ReferenceTable =
  | "users"
  | "companies"
  | "contacts"
  | "tags"
  | "pipelines"
  | "pipeline_stages"
  | "segments"
  | "brevo_lists"
  | "email_folders"
  | "email_labels";

export type FieldDescriptor = {
  key: string;
  label: string;
  type: FieldType;
  comparators: Operator[];
  enum_values: string[];
  sortable: boolean;
  displayable: boolean;
  filterable: boolean;
  default_visible: boolean;
  grouped_under: string;
  source: FieldSource;
  reference_table: ReferenceTable | null;
};

export type EntityKey =
  | "contact"
  | "company"
  | "email_thread"
  | "brevo_template"
  | "brevo_campaign";

export type EntitySummary = { key: EntityKey; label: string };

export type EntityFilterSchema = {
  entity: EntityKey;
  label: string;
  default_sort: string;
  default_sort_dir: "asc" | "desc";
  fields: FieldDescriptor[];
};

export const listEntities = () =>
  apiFetch<EntitySummary[]>(`/api/entities`);

export const getEntityFilterSchema = (entity: EntityKey | string) =>
  apiFetch<EntityFilterSchema>(`/api/entities/${entity}/filter-schema`);

// --- search (PR-B) -----------------------------------------------

// Engine IR — same tree the backend's `build_entity_filter` consumes
// and `segments.rules_json` / `entity_views.filters.rules_json` persist.
export type RuleNode =
  | { type: "rule"; field: string; comparator: Operator; value: unknown }
  | { operator: "AND" | "OR" | "NOT"; children: RuleNode[] };

export type EntitySearchRequest = {
  rules_json?: RuleNode | null;
  sort_by?: string | null;
  sort_dir?: "asc" | "desc";
  limit?: number;
  offset?: number;
};

export type EntitySearchPage<T = Record<string, unknown>> = {
  items: T[];
  total: number;
  limit: number;
  offset: number;
};

export type EntitySearchIdsResult = {
  ids: string[];
  count: number;
  truncated: boolean;
  max_ids: number;
};

export const searchEntity = <T = Record<string, unknown>>(
  entity: EntityKey | string,
  body: EntitySearchRequest = {},
) =>
  apiFetch<EntitySearchPage<T>>(`/api/entities/${entity}/search`, {
    method: "POST",
    body: JSON.stringify(body),
  });

export const searchEntityIds = (
  entity: EntityKey | string,
  body: EntitySearchRequest = {},
) =>
  apiFetch<EntitySearchIdsResult>(`/api/entities/${entity}/search/ids`, {
    method: "POST",
    body: JSON.stringify(body),
  });
