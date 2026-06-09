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
  account_id: string;
  external_id: string;
  account_label?: string | null;
  contact_id: string;
  external_created_at?: string | null;
  external_updated_at?: string | null;
  origin_detail?: string | null;
  metadata?: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
};

export type Contact = {
  id: string;
  first_name: string;
  last_name?: string | null;
  email: string;
  phone?: string | null;
  origin?: string | null;
  tags: string;
  commercial_status: string;
  marketing_consent: "unknown" | "granted" | "denied" | "unsubscribed";
  company_id?: string | null;
  is_active: boolean;
  updated_at?: string;
  created_at?: string;
  address_country?: string | null;
  address_country_name?: string | null;
  address_state?: string | null;
  address_city?: string | null;
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
  origin_system?: string;
  origin_account_id?: string;
  commercial_status?: string;
  marketing_consent?: string;
  sort_by?: "name" | "email" | "created_at" | "updated_at" | "lead_score";
  sort_dir?: "asc" | "desc";
  skip?: number;
  limit?: number;
  include_inactive?: boolean;
};

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

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
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
  if (filters.origin_system) params.set("origin_system", filters.origin_system);
  if (filters.origin_account_id) params.set("origin_account_id", filters.origin_account_id);
  if (filters.commercial_status) params.set("commercial_status", filters.commercial_status);
  if (filters.marketing_consent) params.set("marketing_consent", filters.marketing_consent);
  if (filters.sort_by) params.set("sort_by", filters.sort_by);
  if (filters.sort_dir) params.set("sort_dir", filters.sort_dir);
  if (filters.skip !== undefined) params.set("skip", String(filters.skip));
  if (filters.limit !== undefined) params.set("limit", String(filters.limit));
  if (filters.include_inactive) params.set("include_inactive", "true");
  const query = params.toString();
  return apiFetch<ContactListPage>(`/api/contacts${query ? `?${query}` : ""}`);
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

export async function deactivateContact(id: string): Promise<Contact> {
  return apiFetch<Contact>(`/api/contacts/${id}/deactivate`, { method: "PATCH" });
}

export async function getCompanies(): Promise<Company[]> {
  return apiFetch<Company[]>("/api/companies?limit=20");
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

export async function getUsers(): Promise<User[]> {
  return apiFetch<User[]>("/api/users?limit=100");
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
