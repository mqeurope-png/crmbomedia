/** URL state encoding for the contacts list page.
 *
 * The query builder's `rules_json` tree is base64-encoded into a single
 * `rules` param so the URL stays manageable; a saved view loaded by id
 * trumps the inline tree. Sort + columns travel as compact strings so
 * a copy-pasted URL re-loads the exact same screen.
 *
 * Encoding stays UTF-8-safe via `encodeURIComponent` before the base64
 * round-trip — `JSON.stringify` of a typical rule tree produces ASCII,
 * but a Spanish name like "Categoría" landing in a value field would
 * crash the raw `btoa(str)` call.
 */

type RulesTree = Record<string, unknown>;

export type ContactsUrlState = {
  viewId: string | null;
  rules: RulesTree | null;
  q: string;
  sortBy: string;
  sortDir: "asc" | "desc";
  columns: string[] | null;
};

const DEFAULT_STATE: ContactsUrlState = {
  viewId: null,
  rules: null,
  q: "",
  sortBy: "created_at",
  sortDir: "desc",
  columns: null,
};

export function encodeRules(rules: RulesTree | null): string | null {
  if (!rules || Object.keys(rules).length === 0) return null;
  try {
    const json = JSON.stringify(rules);
    const utf8 = encodeURIComponent(json);
    // `btoa` is browser-only; URL helpers run client-side anyway.
    return typeof window !== "undefined" ? window.btoa(utf8) : null;
  } catch {
    return null;
  }
}

export function decodeRules(raw: string | null): RulesTree | null {
  if (!raw) return null;
  try {
    if (typeof window === "undefined") return null;
    const utf8 = window.atob(raw);
    const json = decodeURIComponent(utf8);
    const parsed = JSON.parse(json);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? (parsed as RulesTree)
      : null;
  } catch {
    return null;
  }
}

export function readUrlState(params: URLSearchParams): ContactsUrlState {
  const sortRaw = params.get("sort");
  const [sortBy, sortDir] = sortRaw
    ? (sortRaw.split(":") as [string, "asc" | "desc"])
    : [DEFAULT_STATE.sortBy, DEFAULT_STATE.sortDir];
  return {
    viewId: params.get("view_id"),
    rules: decodeRules(params.get("rules")),
    q: params.get("q") ?? "",
    sortBy: sortBy ?? DEFAULT_STATE.sortBy,
    sortDir: sortDir === "asc" ? "asc" : "desc",
    columns: params.get("cols")?.split(",").filter(Boolean) ?? null,
  };
}

export function serializeUrlState(state: Partial<ContactsUrlState>): string {
  const params = new URLSearchParams();
  if (state.viewId) {
    params.set("view_id", state.viewId);
  } else if (state.rules) {
    const encoded = encodeRules(state.rules);
    if (encoded) params.set("rules", encoded);
  }
  if (state.q) params.set("q", state.q);
  const sortBy = state.sortBy ?? DEFAULT_STATE.sortBy;
  const sortDir = state.sortDir ?? DEFAULT_STATE.sortDir;
  if (sortBy !== DEFAULT_STATE.sortBy || sortDir !== DEFAULT_STATE.sortDir) {
    params.set("sort", `${sortBy}:${sortDir}`);
  }
  if (state.columns && state.columns.length > 0) {
    params.set("cols", state.columns.join(","));
  }
  return params.toString();
}
