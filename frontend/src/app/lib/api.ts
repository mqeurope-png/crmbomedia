import { extractErrorMessage, formatFastApiDetail } from "./errors";

export type Role = "admin" | "manager" | "user" | "viewer";

export type User = {
  id: string;
  email: string;
  full_name: string;
  role: Role;
  is_active: boolean;
  totp_enabled?: boolean;
  /** Only set on GET /api/auth/me. True when the user is an admin who has
   * not enabled 2FA yet; the UI renders a persistent banner and the
   * backend issues a `limited` JWT that blocks sensitive admin endpoints
   * until 2FA setup is complete. */
  requires_2fa_setup?: boolean;
  /** Sprint Email v2.3b — operator's stored default for the
   *  "incluir opción de baja" toggle. The send modal uses it as the
   *  toggle's initial value. */
  email_include_unsubscribe_default?: boolean;
};

export type LoginResult = {
  access_token: string;
  token_type?: string;
  /** When true, `access_token` is a short-lived pre-2FA temp token. The
   * client must call POST /api/auth/2fa/verify with that token + a TOTP
   * (or backup) code to obtain the final JWT. */
  requires_2fa: boolean;
  /** Set on the FINAL JWT issued to an admin who logged in without 2FA;
   * the token still works for most endpoints but admin-sensitive routes
   * (/api/users, /api/audit-logs, /api/integration-settings) will refuse
   * it until 2FA is enabled. */
  limited: boolean;
};

export type TotpSetupResponse = {
  secret: string;
  otpauth_uri: string;
};

export type TotpConfirmResponse = {
  backup_codes: string[];
  enabled: boolean;
};

export type Company = {
  id: string;
  name: string;
  tax_id?: string | null;
  website?: string | null;
  is_active: boolean;
};

export type Note = {
  id: string;
  body: string;
  created_at: string;
  external_system?: string | null;
  external_account_id?: string | null;
  external_id?: string | null;
  external_author_email?: string | null;
  external_author_name?: string | null;
  external_created_at?: string | null;
};

export type Task = {
  id: string;
  title: string;
  status: "open" | "done" | "cancelled";
  due_at?: string | null;
  external_system?: string | null;
  external_account_id?: string | null;
  external_id?: string | null;
  external_created_at?: string | null;
  external_updated_at?: string | null;
};

export type ActivityEvent = {
  id: string;
  contact_id: string;
  system: string;
  account_id: string;
  external_id?: string | null;
  event_type: string;
  subject?: string | null;
  body?: string | null;
  metadata?: Record<string, unknown> | null;
  occurred_at: string;
  synced_at: string;
  created_at: string;
  updated_at: string;
};

export type ActivityEventListPage = {
  items: ActivityEvent[];
  total: number;
  limit: number;
  offset: number;
};

export type ExternalReference = {
  id: string;
  system: string;
  /** Display label for the system, e.g. "AgileCRM" / "Brevo". */
  system_label?: string | null;
  account_id: string;
  account_label?: string | null;
  external_id: string;
  contact_id: string;
  external_created_at?: string | null;
  external_updated_at?: string | null;
  origin_detail?: string | null;
  /** Deep link into the source system's UI, when one can be built. */
  external_url?: string | null;
  metadata?: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
};

export type ExternalReferenceSummary = {
  system: string;
  account_id: string;
};

export type Tag = {
  id: string;
  name: string;
  color?: string | null;
};

export type TagDetail = Tag & {
  description?: string | null;
  contact_count: number;
  created_by_user_id?: string | null;
  created_at: string;
  updated_at: string;
};

export type TagListPage = {
  items: TagDetail[];
  total: number;
  limit: number;
  offset: number;
};

export type Contact = {
  id: string;
  first_name: string;
  last_name?: string | null;
  email: string;
  phone?: string | null;
  origin?: string | null;
  /** All origins as compact (system, account) pairs — drives the
   * origin chips on the list page. */
  external_references_summary?: ExternalReferenceSummary[];
  /** Deprecated CSV; new code should consume `tag_objects`. */
  tags: string;
  tag_objects?: Tag[];
  commercial_status: string;
  marketing_consent: "unknown" | "granted" | "denied" | "unsubscribed";
  company_id?: string | null;
  is_active: boolean;
  updated_at?: string;
  created_at?: string;
  /** Real creation/modification dates in the source system(s). */
  created_at_external?: string | null;
  updated_at_external?: string | null;
  address_country?: string | null;
  address_country_name?: string | null;
  address_state?: string | null;
  address_city?: string | null;
  /** Sprint Empresas — sub-PR 2/4 added these as first-class
   *  columns. The mapper writes them off Brevo / Agile; older
   *  rows are filled via the backfill script. */
  job_title?: string | null;
  linkedin_url?: string | null;
  personal_website?: string | null;
  address_line?: string | null;
  address_postal_code?: string | null;
  address_region?: string | null;
  lead_score?: number | null;
  custom_fields?: Record<string, unknown> | null;
  notes?: Note[];
  tasks?: Task[];
  external_refs?: ExternalReference[];
  activity_events?: ActivityEvent[];
  last_external_refresh_at?: string | null;
  external_data_freshness?: "fresh" | "stale" | "outdated";
};

export type ExternalRefreshResult = {
  refreshed_at: string;
  sources_refreshed: string[];
  notes_count: number;
  tasks_count: number;
  events_count: number;
  warnings: string[];
  status: "ok" | "partial";
};

export async function refreshContactExternalData(
  contactId: string,
): Promise<ExternalRefreshResult> {
  return apiFetch<ExternalRefreshResult>(
    `/api/contacts/${contactId}/refresh-external-data`,
    { method: "POST" },
  );
}

export type ContactListFilters = {
  q?: string;
  tag?: string;
  tag_ids?: string[];
  tag_match_mode?: "any" | "all";
  origin_system?: string;
  origin_account_id?: string;
  /** Sprint UX: list of `"system:account_id"` keys. Takes
   * precedence over `origin_system` + `origin_account_id` on the
   * backend. */
  origin_account_keys?: string[];
  commercial_status?: string;
  marketing_consent?: string;
  lead_score_min?: number;
  lead_score_max?: number;
  sort_by?: "name" | "email" | "created_at" | "updated_at" | "lead_score";
  sort_dir?: "asc" | "desc";
  skip?: number;
  limit?: number;
  include_inactive?: boolean;
  view_id?: string;
};

export type SavedViewFilters = {
  q?: string | null;
  tag_ids?: string[] | null;
  tag_match_mode?: "any" | "all" | null;
  origin_system?: string | null;
  origin_account_id?: string | null;
  origin_account_keys?: string[] | null;
  commercial_status?: string | null;
  marketing_consent?: string | null;
  is_active?: boolean | null;
  lead_score_min?: number | null;
  lead_score_max?: number | null;
  created_after?: string | null;
  created_before?: string | null;
  /** Segments-engine rules tree (Sprint UX). When present the query
   * builder reads it directly; the legacy flat fields above stay for
   * backwards compatibility. */
  rules_json?: Record<string, unknown> | null;
};

export type SavedViewColumns = {
  visible: string[];
  order: string[];
  widths: Record<string, number>;
};

export type SavedViewSort = {
  sort_by: string;
  sort_dir: "asc" | "desc";
};

// PR-H: las funciones `listSavedViews / createSavedView / …` que
// servían a `/api/contact-views` se quitaron al cerrar el Sprint
// Filtros & Listas. La pantalla nueva de `/contacts` usa
// `lib/entityViewsApi.ts` contra `/api/entity-views/contact`. Los
// types `SavedView*` legacy también se retiraron.
//
// Excepción: `saveViewAsSegment` y `pushViewToBrevoList` (más abajo)
// siguen vivas porque la pantalla nueva usa esos dos bridges
// `/api/contact-views/{id}/{save-as-segment|push-to-brevo-list}` —
// comparten tabla con entity_views y operan sobre cualquier view_id
// con `entity_type='contact'`.

export type ContactListPage = {
  items: Contact[];
  total: number;
  limit: number;
  offset: number;
};

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const TOKEN_STORAGE_KEY = "crmbomedia_access_token";

export function getStoredToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_STORAGE_KEY);
}

export function setStoredToken(token: string) {
  window.localStorage.setItem(TOKEN_STORAGE_KEY, token);
}

export function clearStoredToken() {
  window.localStorage.removeItem(TOKEN_STORAGE_KEY);
}

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getStoredToken();
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...init?.headers,
      },
      cache: "no-store",
    });
  } catch (networkError) {
    throw new Error(extractErrorMessage(networkError));
  }

  if (!response.ok) {
    const fallback = `Error de la API (${response.status})`;
    let body: unknown = null;
    try {
      body = await response.json();
    } catch {
      // body was empty or non-JSON; fall back to status-only message
    }
    const message =
      body && typeof body === "object" && "detail" in body
        ? formatFastApiDetail((body as { detail?: unknown }).detail, fallback)
        : fallback;
    throw new Error(message);
  }

  // 204 No Content (DELETE endpoints, the brevo lists delete in
  // particular) and zero-length 200s have no body — `response.json()`
  // would throw "Unexpected end of JSON input" and surface as a
  // toast to the operator even though the call succeeded. Skip the
  // parse and return null cast through T so the call sites that
  // expect `void` / `null` keep working without per-site `try/catch`
  // around every DELETE.
  if (response.status === 204) {
    return null as T;
  }
  const contentLength = response.headers.get("content-length");
  if (contentLength === "0") {
    return null as T;
  }
  return response.json() as Promise<T>;
}

export async function login(email: string, password: string): Promise<LoginResult> {
  const result = await apiFetch<LoginResult>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
  // Only persist the FINAL JWT. A pre-2FA temp token stays in component
  // state and is used once for /auth/2fa/verify.
  if (!result.requires_2fa) {
    setStoredToken(result.access_token);
  }
  return result;
}

export async function verifyTotp(tempToken: string, code: string): Promise<LoginResult> {
  const result = await apiFetch<LoginResult>("/api/auth/2fa/verify", {
    method: "POST",
    body: JSON.stringify({ temp_token: tempToken, code }),
  });
  setStoredToken(result.access_token);
  return result;
}

export async function setupTotp(): Promise<TotpSetupResponse> {
  return apiFetch<TotpSetupResponse>("/api/auth/2fa/setup", { method: "POST" });
}

export async function confirmTotp(code: string): Promise<TotpConfirmResponse> {
  return apiFetch<TotpConfirmResponse>("/api/auth/2fa/confirm", {
    method: "POST",
    body: JSON.stringify({ code }),
  });
}

export async function disableTotp(password: string): Promise<{ message: string }> {
  return apiFetch<{ message: string }>("/api/auth/2fa/disable", {
    method: "POST",
    body: JSON.stringify({ password }),
  });
}

export async function getCurrentUser(): Promise<User> {
  return apiFetch<User>("/api/auth/me");
}

export async function getContacts(): Promise<Contact[]> {
  // Dashboard shortcut: the list endpoint now wraps the response, but
  // callers that only want the first page items continue to receive a
  // plain array. The pagination wrapper is exposed via `listContacts`.
  const page = await apiFetch<ContactListPage>("/api/contacts?limit=20");
  return page.items;
}

export async function listContacts(
  filters: ContactListFilters = {},
): Promise<ContactListPage> {
  const params = new URLSearchParams();
  if (filters.q) params.set("q", filters.q);
  if (filters.tag) params.set("tag", filters.tag);
  if (filters.tag_ids?.length) {
    for (const id of filters.tag_ids) params.append("tag_ids", id);
  }
  if (filters.tag_match_mode) params.set("tag_match_mode", filters.tag_match_mode);
  if (filters.origin_system) params.set("origin_system", filters.origin_system);
  if (filters.origin_account_id) params.set("origin_account_id", filters.origin_account_id);
  if (filters.origin_account_keys?.length) {
    for (const key of filters.origin_account_keys) {
      params.append("origin_account_keys", key);
    }
  }
  if (filters.commercial_status) params.set("commercial_status", filters.commercial_status);
  if (filters.marketing_consent) params.set("marketing_consent", filters.marketing_consent);
  if (filters.lead_score_min !== undefined)
    params.set("lead_score_min", String(filters.lead_score_min));
  if (filters.lead_score_max !== undefined)
    params.set("lead_score_max", String(filters.lead_score_max));
  if (filters.sort_by) params.set("sort_by", filters.sort_by);
  if (filters.sort_dir) params.set("sort_dir", filters.sort_dir);
  if (filters.skip !== undefined) params.set("skip", String(filters.skip));
  if (filters.limit !== undefined) params.set("limit", String(filters.limit));
  if (filters.include_inactive) params.set("include_inactive", "true");
  if (filters.view_id) params.set("view_id", filters.view_id);
  const query = params.toString();
  return apiFetch<ContactListPage>(`/api/contacts${query ? `?${query}` : ""}`);
}

// PR-H: `searchContacts` y `searchContactIds` (legacy
// `/api/contacts/search` y `/search/ids`) se retiraron junto con
// los types `ContactSearchPayload` / `ContactSearchIdsResult` —
// la pantalla nueva usa `searchEntity('contact', …)` y
// `searchEntityIds('contact', …)` de `lib/entitySchema.ts`. Los
// endpoints backend siguen vivos por compatibilidad con tests.


export async function saveViewAsSegment(
  viewId: string,
  payload: { name: string; description?: string | null; is_shared?: boolean },
): Promise<{ id: string; name: string }> {
  return apiFetch<{ id: string; name: string }>(
    `/api/contact-views/${viewId}/save-as-segment`,
    { method: "POST", body: JSON.stringify(payload) },
  );
}

export type PushViewToBrevoResult = {
  sync_log_id: string;
  job_id: string | null;
  target_id: string;
  segment_id: string;
  contacts_to_push: number;
  brevo_list_id: number;
};

export async function pushViewToBrevoList(
  viewId: string,
  payload: {
    brevo_account_id: string;
    brevo_list_id?: number | null;
    new_list_name?: string | null;
  },
): Promise<PushViewToBrevoResult> {
  return apiFetch<PushViewToBrevoResult>(
    `/api/contact-views/${viewId}/push-to-brevo-list`,
    { method: "POST", body: JSON.stringify(payload) },
  );
}

export async function listTags(query?: string): Promise<TagListPage> {
  const params = new URLSearchParams();
  if (query) params.set("q", query);
  params.set("limit", "200");
  return apiFetch<TagListPage>(`/api/tags?${params.toString()}`);
}

export async function createTag(payload: {
  name: string;
  color?: string | null;
  description?: string | null;
}): Promise<TagDetail> {
  return apiFetch<TagDetail>("/api/tags", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updateTag(
  id: string,
  payload: { name?: string; color?: string | null; description?: string | null },
): Promise<TagDetail> {
  return apiFetch<TagDetail>(`/api/tags/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function deleteTag(id: string): Promise<{ message: string }> {
  return apiFetch<{ message: string }>(`/api/tags/${id}`, { method: "DELETE" });
}

export async function addTagToContact(
  contactId: string,
  payload: { tag_id?: string; tag_name?: string; color?: string | null },
): Promise<Tag> {
  return apiFetch<Tag>(`/api/contacts/${contactId}/tags`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function removeTagFromContact(
  contactId: string,
  tagId: string,
): Promise<{ message: string }> {
  return apiFetch<{ message: string }>(
    `/api/contacts/${contactId}/tags/${tagId}`,
    { method: "DELETE" },
  );
}

export async function bulkContactTag(payload: {
  action: "add" | "remove";
  tag_id: string;
  contact_ids: string[];
}): Promise<{ action: string; tag_id: string; affected: number; skipped: number }> {
  return apiFetch("/api/contacts/bulk-tag", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function getContactsCount(): Promise<number> {
  const body = await apiFetch<{ total: number }>("/api/contacts/count");
  return body.total;
}

export async function getContact(id: string): Promise<Contact> {
  return apiFetch<Contact>(`/api/contacts/${id}`);
}

export async function getContactActivityEvents(
  id: string,
  { skip = 0, limit = 50 }: { skip?: number; limit?: number } = {},
): Promise<ActivityEventListPage> {
  const params = new URLSearchParams({ skip: String(skip), limit: String(limit) });
  return apiFetch<ActivityEventListPage>(
    `/api/contacts/${id}/activity-events?${params.toString()}`,
  );
}

export async function updateContact(id: string, payload: Record<string, unknown>): Promise<Contact> {
  return apiFetch<Contact>(`/api/contacts/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

// Sprint Reglas-Assign PR-Ca hotfix. Antes el botón "Asignarme" del
// dashboard hacía PATCH /api/contacts/{id} con owner_user_id — eso (1)
// requería require_manager y rompía para users normales, y (2) tocaba
// solo el caché sin crear la fila en contact_assignments, así que el
// widget seguía mostrando el lead como sin asignar. La ruta correcta
// es el endpoint multi-comercial (require_user, crea fila + recalcula
// caché + audit).
export async function assignContactToUser(
  contactId: string,
  userId: string,
  options: { isPrimary?: boolean; notes?: string | null } = {},
): Promise<ContactAssignment> {
  return apiFetch<ContactAssignment>(`/api/contacts/${contactId}/assignments`, {
    method: "POST",
    body: JSON.stringify({
      user_id: userId,
      is_primary: options.isPrimary ?? true,
      notes: options.notes ?? null,
    }),
  });
}

// Sprint Reglas-Assign PR-D — helpers de la sección "Comerciales
// asignados" de la ficha.

export interface AssignmentUserRef {
  id: string;
  email: string;
  full_name?: string | null;
  is_active: boolean;
}

export interface ContactAssignment {
  id: string;
  contact_id: string;
  user_id: string;
  user: AssignmentUserRef;
  is_primary: boolean;
  source: string;
  rule_id: string | null;
  notes: string | null;
  assigned_by_user_id: string | null;
  assigned_at: string;
  created_at: string;
  updated_at: string;
}

export async function listContactAssignments(
  contactId: string,
): Promise<ContactAssignment[]> {
  return apiFetch<ContactAssignment[]>(
    `/api/contacts/${contactId}/assignments`,
  );
}

export async function promoteAssignment(
  contactId: string,
  assignmentId: string,
): Promise<ContactAssignment> {
  return apiFetch<ContactAssignment>(
    `/api/contacts/${contactId}/assignments/${assignmentId}/promote`,
    { method: "POST" },
  );
}

export async function deleteAssignment(
  contactId: string,
  assignmentId: string,
): Promise<void> {
  await apiFetch(`/api/contacts/${contactId}/assignments/${assignmentId}`, {
    method: "DELETE",
  });
}

export async function deactivateContact(id: string): Promise<Contact> {
  return apiFetch<Contact>(`/api/contacts/${id}/deactivate`, { method: "PATCH" });
}

export async function getCompanies(): Promise<Company[]> {
  // Sprint Empresas — `/api/companies` now returns a paginated
  // envelope; the legacy callers (contact-create dropdown) only
  // need the items.
  const page = await apiFetch<{ items: Company[]; total: number }>(
    "/api/companies?limit=20",
  );
  return page.items;
}

export async function getCompaniesCount(): Promise<number> {
  const body = await apiFetch<{ total: number }>("/api/companies/count");
  return body.total;
}

export async function updateCompany(id: string, payload: Record<string, unknown>): Promise<Company> {
  return apiFetch<Company>(`/api/companies/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function createContact(payload: Record<string, FormDataEntryValue | null>) {
  return apiFetch<Contact>("/api/contacts", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export type AuditLog = {
  id: string;
  actor_user_id?: string | null;
  actor_email?: string | null;
  action: string;
  target_type: string;
  target_id?: string | null;
  metadata?: Record<string, unknown> | null;
  message?: string | null;
  ip_address?: string | null;
  user_agent?: string | null;
  created_at: string;
};

export type AuditLogFilters = {
  action?: string;
  action_prefix?: string;
  actor_user_id?: string;
  target_type?: string;
  from?: string;
  to?: string;
  skip?: number;
  limit?: number;
};

export type AuditLogPage = {
  items: AuditLog[];
  total: number;
  skip: number;
  limit: number;
};

export async function getUsers(
  options: { q?: string; limit?: number; skip?: number } = {},
): Promise<User[]> {
  // PR-Cg: el UserPicker autocompleta server-side, así que el cliente
  // manda `q`. Sin args, mantiene el shape original que usa el módulo
  // admin (limit=100 por defecto).
  const params = new URLSearchParams();
  if (options.q) params.set("q", options.q);
  params.set("limit", String(options.limit ?? 100));
  if (options.skip !== undefined) params.set("skip", String(options.skip));
  return apiFetch<User[]>(`/api/users?${params.toString()}`);
}

export async function createUser(payload: Record<string, unknown>): Promise<User> {
  return apiFetch<User>("/api/users", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updateUser(id: string, payload: Record<string, unknown>): Promise<User> {
  return apiFetch<User>(`/api/users/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function deactivateUser(id: string): Promise<User> {
  return apiFetch<User>(`/api/users/${id}/deactivate`, { method: "PATCH" });
}

export async function reactivateUser(id: string): Promise<User> {
  return apiFetch<User>(`/api/users/${id}/reactivate`, { method: "PATCH" });
}

export async function adminUpdateUserPassword(id: string, newPassword: string) {
  return apiFetch<{ message: string }>(`/api/users/${id}/password`, {
    method: "PATCH",
    body: JSON.stringify({ new_password: newPassword }),
  });
}

export async function changePassword(currentPassword: string, newPassword: string) {
  return apiFetch<{ message: string }>("/api/auth/change-password", {
    method: "POST",
    body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
  });
}

export async function requestPasswordReset(email: string): Promise<{ message: string; reset_token?: string }> {
  return apiFetch<{ message: string; reset_token?: string }>("/api/auth/password-reset/request", {
    method: "POST",
    body: JSON.stringify({ email }),
  });
}

export async function confirmPasswordReset(token: string, newPassword: string) {
  return apiFetch<{ message: string }>("/api/auth/password-reset/confirm", {
    method: "POST",
    body: JSON.stringify({ token, new_password: newPassword }),
  });
}

function buildAuditQuery(filters: AuditLogFilters): string {
  const params = new URLSearchParams();
  if (filters.action) params.set("action", filters.action);
  if (filters.action_prefix) params.set("action_prefix", filters.action_prefix);
  if (filters.actor_user_id) params.set("actor_user_id", filters.actor_user_id);
  if (filters.target_type) params.set("target_type", filters.target_type);
  if (filters.from) params.set("from", filters.from);
  if (filters.to) params.set("to", filters.to);
  if (filters.skip !== undefined) params.set("skip", String(filters.skip));
  if (filters.limit !== undefined) params.set("limit", String(filters.limit));
  return params.toString();
}

export async function getAuditLogs(filters: AuditLogFilters = {}): Promise<AuditLogPage> {
  const token = getStoredToken();
  const query = buildAuditQuery({ limit: 50, ...filters });
  const url = `${API_BASE_URL}/api/audit-logs${query ? `?${query}` : ""}`;
  const response = await fetch(url, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    cache: "no-store",
  });
  if (!response.ok) {
    let detail = `API request failed with ${response.status}`;
    try {
      const body = await response.json();
      detail = body.detail ?? detail;
    } catch {
      detail = await response.text();
    }
    throw new Error(detail);
  }
  const items = (await response.json()) as AuditLog[];
  const total = Number.parseInt(response.headers.get("x-total-count") ?? "0", 10);
  return {
    items,
    total: Number.isFinite(total) ? total : items.length,
    skip: filters.skip ?? 0,
    limit: filters.limit ?? 50,
  };
}

export type GdprRequestType =
  | "access"
  | "rectification"
  | "erasure"
  | "portability"
  | "objection";

export type GdprRequestStatus =
  | "pending"
  | "in_progress"
  | "completed"
  | "rejected";

export type GdprRequest = {
  id: string;
  subject_email: string;
  subject_contact_id: string | null;
  request_type: GdprRequestType;
  status: GdprRequestStatus;
  requested_at: string;
  completed_at: string | null;
  requester_user_id: string | null;
  notes: string | null;
  evidence_path: string | null;
  created_at: string;
  updated_at: string;
};

export type GdprProcessResult = {
  request_id: string;
  request_type: GdprRequestType;
  status: GdprRequestStatus;
  evidence_path: string | null;
  payload: Record<string, unknown>;
};

export type GdprRequestFilters = {
  status?: GdprRequestStatus;
  request_type?: GdprRequestType;
  subject_email?: string;
};

export async function listGdprRequests(
  filters: GdprRequestFilters = {},
): Promise<GdprRequest[]> {
  const params = new URLSearchParams();
  if (filters.status) params.set("status", filters.status);
  if (filters.request_type) params.set("request_type", filters.request_type);
  if (filters.subject_email) params.set("subject_email", filters.subject_email);
  const query = params.toString();
  return apiFetch<GdprRequest[]>(`/api/gdpr/requests${query ? `?${query}` : ""}`);
}

export async function createGdprRequest(payload: {
  subject_email: string;
  request_type: GdprRequestType;
  notes?: string | null;
}): Promise<GdprRequest> {
  return apiFetch<GdprRequest>("/api/gdpr/requests", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updateGdprRequest(
  id: string,
  payload: { status?: GdprRequestStatus; notes?: string | null },
): Promise<GdprRequest> {
  return apiFetch<GdprRequest>(`/api/gdpr/requests/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function processGdprRequest(id: string): Promise<GdprProcessResult> {
  return apiFetch<GdprProcessResult>(`/api/gdpr/requests/${id}/process`, {
    method: "POST",
  });
}

export async function exportAuditLogs(
  format: "csv" | "json",
  filters: AuditLogFilters = {},
): Promise<Blob> {
  const token = getStoredToken();
  const query = buildAuditQuery({ ...filters });
  const url = `${API_BASE_URL}/api/audit-logs/export?format=${format}${
    query ? `&${query}` : ""
  }`;
  const response = await fetch(url, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!response.ok) {
    let detail = `Audit export failed with ${response.status}`;
    try {
      const body = await response.json();
      detail = body.detail ?? detail;
    } catch {
      // body was empty / non-JSON; keep status-only message
    }
    throw new Error(detail);
  }
  return response.blob();
}

// ----- Pipelines (Sprint P.2) -----

export type PipelineStage = {
  id: string;
  pipeline_id: string;
  name: string;
  description?: string | null;
  position: number;
  color?: string | null;
  is_won: boolean;
  is_lost: boolean;
  target_days?: number | null;
  created_at: string;
  updated_at: string;
};

export type Pipeline = {
  id: string;
  name: string;
  description?: string | null;
  color?: string | null;
  is_active: boolean;
  is_shared: boolean;
  owner_user_id: string;
  stages: PipelineStage[];
  contact_count: number;
  created_at: string;
  updated_at: string;
};

export type PipelineContactCard = {
  id: string;
  contact_id: string;
  first_name: string;
  last_name?: string | null;
  email: string;
  phone?: string | null;
  lead_score?: number | null;
  tags: Tag[];
  entered_stage_at: string;
  added_to_pipeline_at: string;
  days_in_stage: number;
};

export type PipelineStageGroup = {
  stage_id: string;
  stage_name: string;
  stage_color?: string | null;
  position: number;
  is_won: boolean;
  is_lost: boolean;
  target_days?: number | null;
  total: number;
  contacts: PipelineContactCard[];
};

export type PipelineContactsResponse = {
  pipeline: Pipeline;
  stages: PipelineStageGroup[];
};

export type PipelineStageMetric = {
  stage_id: string;
  stage_name: string;
  position: number;
  contact_count: number;
  avg_seconds_in_stage?: number | null;
  conversion_to_next?: number | null;
  stalled_count: number;
};

export type PipelineReport = {
  pipeline_id: string;
  pipeline_name: string;
  total_contacts: number;
  won_count: number;
  lost_count: number;
  metrics: PipelineStageMetric[];
};

export async function listPipelines(includeInactive = false): Promise<Pipeline[]> {
  const query = includeInactive ? "?include_inactive=true" : "";
  return apiFetch<Pipeline[]>(`/api/pipelines${query}`);
}

export async function getPipeline(id: string): Promise<Pipeline> {
  return apiFetch<Pipeline>(`/api/pipelines/${id}`);
}

export async function createPipeline(payload: {
  name: string;
  description?: string | null;
  color?: string | null;
  is_shared?: boolean;
  stages?: Array<{
    name: string;
    description?: string | null;
    color?: string | null;
    is_won?: boolean;
    is_lost?: boolean;
    target_days?: number | null;
    position?: number;
  }>;
}): Promise<Pipeline> {
  return apiFetch<Pipeline>("/api/pipelines", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updatePipeline(
  id: string,
  payload: Partial<{
    name: string;
    description: string | null;
    color: string | null;
    is_shared: boolean;
    is_active: boolean;
  }>,
): Promise<Pipeline> {
  return apiFetch<Pipeline>(`/api/pipelines/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function deletePipeline(id: string): Promise<{ message: string }> {
  return apiFetch<{ message: string }>(`/api/pipelines/${id}`, {
    method: "DELETE",
  });
}

export async function duplicatePipeline(
  id: string,
  payload: { name?: string; include_contacts?: boolean } = {},
): Promise<Pipeline> {
  return apiFetch<Pipeline>(`/api/pipelines/${id}/duplicate`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function addPipelineStage(
  pipelineId: string,
  payload: {
    name: string;
    description?: string | null;
    color?: string | null;
    is_won?: boolean;
    is_lost?: boolean;
    target_days?: number | null;
    position?: number;
  },
): Promise<PipelineStage> {
  return apiFetch<PipelineStage>(`/api/pipelines/${pipelineId}/stages`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updatePipelineStage(
  stageId: string,
  payload: Partial<{
    name: string;
    description: string | null;
    color: string | null;
    is_won: boolean;
    is_lost: boolean;
    target_days: number | null;
  }>,
): Promise<PipelineStage> {
  return apiFetch<PipelineStage>(`/api/pipeline-stages/${stageId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function deletePipelineStage(
  stageId: string,
  moveToStageId?: string,
): Promise<{ message: string }> {
  const query = moveToStageId ? `?move_to_stage_id=${moveToStageId}` : "";
  return apiFetch<{ message: string }>(
    `/api/pipeline-stages/${stageId}${query}`,
    { method: "DELETE" },
  );
}

export async function reorderPipelineStages(
  pipelineId: string,
  stageIds: string[],
): Promise<PipelineStage[]> {
  return apiFetch<PipelineStage[]>(
    `/api/pipelines/${pipelineId}/stages/reorder`,
    { method: "POST", body: JSON.stringify({ stage_ids: stageIds }) },
  );
}

export async function listPipelineContacts(
  pipelineId: string,
): Promise<PipelineContactsResponse> {
  return apiFetch<PipelineContactsResponse>(
    `/api/pipelines/${pipelineId}/contacts`,
  );
}

export async function pipelineReport(
  pipelineId: string,
): Promise<PipelineReport> {
  return apiFetch<PipelineReport>(`/api/pipelines/${pipelineId}/report`);
}

export async function addContactToPipeline(
  contactId: string,
  payload: { pipeline_id: string; stage_id?: string; note?: string | null },
): Promise<{ id: string; stage_id: string; pipeline_id: string }> {
  return apiFetch(`/api/contacts/${contactId}/pipelines`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function moveContactToStage(
  assignmentId: string,
  payload: { stage_id: string; note?: string | null },
): Promise<{ id: string; stage_id: string }> {
  return apiFetch(`/api/contact-pipeline-stages/${assignmentId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function archivePipelineAssignment(
  assignmentId: string,
): Promise<{ message: string }> {
  return apiFetch<{ message: string }>(
    `/api/contact-pipeline-stages/${assignmentId}`,
    { method: "DELETE" },
  );
}

export type ContactPipelineSummary = {
  assignment_id: string;
  pipeline_id: string;
  pipeline_name: string;
  pipeline_color?: string | null;
  stage_id: string;
  stage_name: string;
  stage_color?: string | null;
  stage_position: number;
  is_won: boolean;
  is_lost: boolean;
  days_in_stage: number;
  entered_stage_at: string;
  added_to_pipeline_at: string;
};

export async function listContactPipelines(
  contactId: string,
  includeArchived = false,
): Promise<ContactPipelineSummary[]> {
  const query = includeArchived ? "?include_archived=true" : "";
  return apiFetch<ContactPipelineSummary[]>(
    `/api/contacts/${contactId}/pipelines${query}`,
  );
}

export type StalledContactRow = {
  assignment_id: string;
  contact_id: string;
  first_name: string;
  last_name?: string | null;
  email: string;
  stage_id: string;
  stage_name: string;
  target_days: number;
  days_in_stage: number;
  overdue_days: number;
  entered_stage_at: string;
};

export async function pipelineStalledContacts(
  pipelineId: string,
): Promise<StalledContactRow[]> {
  return apiFetch<StalledContactRow[]>(
    `/api/pipelines/${pipelineId}/stalled-contacts`,
  );
}

// ----- Pipeline templates + AI assist (Sprint P.2.5) -----

export type PipelineTemplate = {
  id: string;
  name: string;
  description: string;
  category: string;
  color?: string | null;
  stages: Array<{
    name: string;
    description?: string | null;
    color?: string | null;
    is_won?: boolean;
    is_lost?: boolean;
    target_days?: number | null;
  }>;
};

export type PipelineProposal = {
  name: string;
  description?: string | null;
  color?: string | null;
  stages: Array<{
    name: string;
    description?: string | null;
    color?: string | null;
    is_won: boolean;
    is_lost: boolean;
    target_days?: number | null;
    position: number;
  }>;
};

export async function listPipelineTemplates(): Promise<PipelineTemplate[]> {
  return apiFetch<PipelineTemplate[]>("/api/pipeline-templates");
}

export async function createPipelineFromTemplate(payload: {
  template_id: string;
  name?: string;
}): Promise<Pipeline> {
  return apiFetch<Pipeline>("/api/pipelines/from-template", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function generatePipelineWithAI(
  description: string,
): Promise<PipelineProposal> {
  return apiFetch<PipelineProposal>("/api/pipelines/generate-ai", {
    method: "POST",
    body: JSON.stringify({ description }),
  });
}

export type HealthResponse = {
  status: string;
  app_name: string;
  environment: string;
  ai_features_enabled: boolean;
};

export async function getHealth(): Promise<HealthResponse> {
  return apiFetch<HealthResponse>("/api/health");
}

// ----- Segments (Sprint P.3) -----

export type SegmentFieldDescriptor = {
  key: string;
  label: string;
  type: string;
  comparators: string[];
  enum_values: string[];
};

export type Segment = {
  id: string;
  name: string;
  description?: string | null;
  color?: string | null;
  owner_user_id: string;
  is_owner: boolean;
  is_shared: boolean;
  is_dynamic: boolean;
  rules: Record<string, unknown>;
  static_contact_ids: string[];
  cached_count?: number | null;
  last_evaluated_at?: string | null;
  /** Brevo-managed mirror identifier. `<system>:<account>:<id>` when
   * the segment is externally managed; null on CRM-native segments.
   * The UI uses this to hide the rule editor and show
   * "Espejo Brevo" + refresh/open buttons. */
  external_source?: string | null;
  external_last_refreshed_at?: string | null;
  external_refresh_interval_minutes?: number | null;
  created_at: string;
  updated_at: string;
};

export function isBrevoMirror(segment: Segment): boolean {
  return Boolean(segment.external_source?.startsWith("brevo:"));
}

export function brevoMirrorParts(segment: Segment):
  | { account: string; brevoSegmentId: string }
  | null {
  if (!segment.external_source) return null;
  const parts = segment.external_source.split(":");
  if (parts.length !== 3 || parts[0] !== "brevo") return null;
  return { account: parts[1], brevoSegmentId: parts[2] };
}

export type SegmentPreviewContactCard = {
  id: string;
  first_name: string;
  last_name?: string | null;
  email: string;
  lead_score?: number | null;
};

export type SegmentPreviewResponse = {
  count: number;
  sample: SegmentPreviewContactCard[];
};

export type SegmentTemplate = {
  id: string;
  name: string;
  description: string;
  category: string;
  color?: string | null;
  rules: Record<string, unknown>;
};

export type SegmentAIGenerateResponse = {
  rules: Record<string, unknown> | null;
  error: string | null;
  count: number;
  sample: SegmentPreviewContactCard[];
};

export type SegmentAIExplainResponse = { explanation: string };

export async function listSegmentFields(): Promise<SegmentFieldDescriptor[]> {
  return apiFetch<SegmentFieldDescriptor[]>("/api/segments/available-fields");
}

export type SegmentCountryOption = {
  code: string;
  contact_count: number;
};

export type SegmentOriginAccountOption = {
  value: string;
  label: string;
  system: string;
};

export async function listSegmentAvailableCountries(): Promise<
  SegmentCountryOption[]
> {
  return apiFetch<SegmentCountryOption[]>(
    "/api/segments/available-countries",
  );
}

export async function listSegmentAvailableOriginAccounts(): Promise<
  SegmentOriginAccountOption[]
> {
  return apiFetch<SegmentOriginAccountOption[]>(
    "/api/segments/available-origin-accounts",
  );
}

export type IntegrationAccountSummary = {
  account_id: string;
  label: string;
  contacts_count: number;
  enabled: boolean;
};

export type IntegrationSystemGroup = {
  system: string;
  system_label: string;
  accounts: IntegrationAccountSummary[];
};

export async function listIntegrationAccountGroups(): Promise<
  IntegrationSystemGroup[]
> {
  return apiFetch<IntegrationSystemGroup[]>("/api/integrations/accounts");
}

export async function listSegments(
  options: { q?: string; limit?: number } = {},
): Promise<Segment[]> {
  // PR-Cg: `q` + `limit` para autocomplete del SegmentPicker
  // server-side. Sin args, el endpoint devuelve la lista completa
  // como antes (la pantalla `/segments` no aplica filtros).
  const params = new URLSearchParams();
  if (options.q) params.set("q", options.q);
  if (options.limit !== undefined) params.set("limit", String(options.limit));
  const qs = params.toString();
  return apiFetch<Segment[]>(`/api/segments${qs ? `?${qs}` : ""}`);
}

export async function getSegment(id: string): Promise<Segment> {
  return apiFetch<Segment>(`/api/segments/${id}`);
}

export async function createSegment(payload: {
  name: string;
  description?: string | null;
  color?: string | null;
  is_shared?: boolean;
  is_dynamic?: boolean;
  rules: Record<string, unknown>;
}): Promise<Segment> {
  return apiFetch<Segment>("/api/segments", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updateSegment(
  id: string,
  payload: Partial<{
    name: string;
    description: string | null;
    color: string | null;
    is_shared: boolean;
    is_dynamic: boolean;
    rules: Record<string, unknown>;
  }>,
): Promise<Segment> {
  return apiFetch<Segment>(`/api/segments/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function deleteSegment(id: string): Promise<{ message: string }> {
  return apiFetch<{ message: string }>(`/api/segments/${id}`, {
    method: "DELETE",
  });
}

export async function duplicateSegment(
  id: string,
  payload: { name?: string } = {},
): Promise<Segment> {
  return apiFetch<Segment>(`/api/segments/${id}/duplicate`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function segmentContacts(
  id: string,
  params: { skip?: number; limit?: number; sort_by?: string; sort_dir?: "asc" | "desc" } = {},
): Promise<ContactListPage> {
  const search = new URLSearchParams();
  if (params.skip !== undefined) search.set("skip", String(params.skip));
  if (params.limit !== undefined) search.set("limit", String(params.limit));
  if (params.sort_by) search.set("sort_by", params.sort_by);
  if (params.sort_dir) search.set("sort_dir", params.sort_dir);
  const query = search.toString();
  return apiFetch<ContactListPage>(
    `/api/segments/${id}/contacts${query ? `?${query}` : ""}`,
  );
}

export async function segmentCount(
  id: string,
  forceRefresh = false,
): Promise<{ total: number }> {
  const query = forceRefresh ? "?force_refresh=true" : "";
  return apiFetch<{ total: number }>(`/api/segments/${id}/count${query}`);
}

export async function previewSegmentRules(
  rules: Record<string, unknown>,
): Promise<SegmentPreviewResponse> {
  return apiFetch<SegmentPreviewResponse>("/api/segments/preview", {
    method: "POST",
    body: JSON.stringify({ rules }),
  });
}

export async function listSegmentTemplates(): Promise<SegmentTemplate[]> {
  return apiFetch<SegmentTemplate[]>("/api/segments/templates");
}

export async function segmentAIGenerate(
  description: string,
): Promise<SegmentAIGenerateResponse> {
  return apiFetch<SegmentAIGenerateResponse>("/api/segments/ai-generate", {
    method: "POST",
    body: JSON.stringify({ description }),
  });
}

export async function segmentAIExplain(
  payload: { rules?: Record<string, unknown>; segment_id?: string },
): Promise<SegmentAIExplainResponse> {
  return apiFetch<SegmentAIExplainResponse>("/api/segments/ai-explain", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}
