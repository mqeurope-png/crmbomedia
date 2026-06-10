import { extractErrorMessage, formatFastApiDetail } from "./errors";

export type ExternalSystem = "agilecrm" | "brevo" | "freshdesk" | "factusol";
export type IntegrationMode = "sandbox" | "live";
export type IntegrationStatus = "not_configured" | "configured" | "paused";
export type QuotaStrategy = "keep_newest" | "keep_oldest" | "none";

export type IntegrationAccount = {
  id: string;
  system: ExternalSystem;
  account_id: string;
  display_name: string;
  enabled: boolean;
  mode: IntegrationMode;
  status: IntegrationStatus;
  api_base_url?: string | null;
  account_label?: string | null;
  auth_identifier?: string | null;
  credential_status: string;
  notes?: string | null;
  quota_max_contacts?: number | null;
  quota_strategy?: QuotaStrategy | null;
  sync_priority: number;
  has_api_key: boolean;
  api_key_set_at?: string | null;
  api_key_last_used_at?: string | null;
  created_at: string;
  updated_at: string;
};

export type IntegrationAccountCreatePayload = {
  account_id: string;
  display_name: string;
  enabled?: boolean;
  mode?: IntegrationMode;
  api_base_url?: string | null;
  account_label?: string | null;
  auth_identifier?: string | null;
  notes?: string | null;
  quota_max_contacts?: number | null;
  quota_strategy?: QuotaStrategy | null;
  sync_priority?: number;
};

export type IntegrationAccountUpdatePayload = Partial<{
  display_name: string;
  enabled: boolean;
  mode: IntegrationMode;
  status: IntegrationStatus;
  api_base_url: string | null;
  account_label: string | null;
  auth_identifier: string | null;
  credential_status: string;
  notes: string | null;
  quota_max_contacts: number | null;
  quota_strategy: QuotaStrategy | null;
  sync_priority: number;
}>;

// Kept as alias to ease migration of any caller that still says "Setting".
export type IntegrationSetting = IntegrationAccount;

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const TOKEN_STORAGE_KEY = "crmbomedia_access_token";

function getStoredToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_STORAGE_KEY);
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
      // empty / non-JSON body
    }
    const message =
      body && typeof body === "object" && "detail" in body
        ? formatFastApiDetail((body as { detail?: unknown }).detail, fallback)
        : fallback;
    throw new Error(message);
  }

  return response.json() as Promise<T>;
}

export async function listIntegrationAccounts(filters?: {
  system?: ExternalSystem;
  enabled?: boolean;
}): Promise<IntegrationAccount[]> {
  const params = new URLSearchParams();
  if (filters?.system) params.set("system", filters.system);
  if (typeof filters?.enabled === "boolean") {
    params.set("enabled", String(filters.enabled));
  }
  const query = params.toString();
  return apiFetch<IntegrationAccount[]>(
    `/api/integration-accounts${query ? `?${query}` : ""}`,
  );
}

export async function createIntegrationAccount(
  system: ExternalSystem,
  payload: IntegrationAccountCreatePayload,
): Promise<IntegrationAccount> {
  return apiFetch<IntegrationAccount>(`/api/integration-accounts/${system}`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updateIntegrationAccount(
  system: ExternalSystem,
  accountId: string,
  payload: IntegrationAccountUpdatePayload,
): Promise<IntegrationAccount> {
  return apiFetch<IntegrationAccount>(
    `/api/integration-accounts/${system}/${accountId}`,
    {
      method: "PATCH",
      body: JSON.stringify(payload),
    },
  );
}

export async function deleteIntegrationAccount(
  system: ExternalSystem,
  accountId: string,
  options?: { force?: boolean },
): Promise<IntegrationAccount> {
  const params = new URLSearchParams();
  if (options?.force) params.set("force", "true");
  const query = params.toString();
  return apiFetch<IntegrationAccount>(
    `/api/integration-accounts/${system}/${accountId}${query ? `?${query}` : ""}`,
    { method: "DELETE" },
  );
}

export async function setIntegrationAccountApiKey(
  system: ExternalSystem,
  accountId: string,
  apiKey: string,
): Promise<IntegrationAccount> {
  return apiFetch<IntegrationAccount>(
    `/api/integration-accounts/${system}/${accountId}/api-key`,
    {
      method: "PUT",
      body: JSON.stringify({ api_key: apiKey }),
    },
  );
}

export async function deleteIntegrationAccountApiKey(
  system: ExternalSystem,
  accountId: string,
): Promise<IntegrationAccount> {
  return apiFetch<IntegrationAccount>(
    `/api/integration-accounts/${system}/${accountId}/api-key`,
    { method: "DELETE" },
  );
}

// ---------------------------------------------------------------------------
// Sync triggers + sync_logs (Sprint A infra)
// ---------------------------------------------------------------------------

export type SyncStatus =
  | "pending"
  | "running"
  | "success"
  | "partial_success"
  | "failed";

export type SyncLogEntry = {
  id: string;
  system: ExternalSystem;
  account_id: string | null;
  operation: string | null;
  status: SyncStatus | string;
  started_at: string | null;
  finished_at: string | null;
  records_processed: number;
  records_skipped: number;
  records_failed: number;
  error_summary: string | null;
  triggered_by: string | null;
  triggered_by_user_id: string | null;
  job_id: string | null;
  metadata: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
};

export type SyncTriggerResult = {
  sync_log_id: string;
  job_id: string | null;
  operation: string;
  status: SyncStatus;
};

export type SyncLogFilters = {
  status?: SyncStatus;
  operation?: string;
  from?: string;
  to?: string;
  skip?: number;
  limit?: number;
};

function buildSyncLogQuery(filters: SyncLogFilters = {}): string {
  const params = new URLSearchParams();
  if (filters.status) params.set("status", filters.status);
  if (filters.operation) params.set("operation", filters.operation);
  if (filters.from) params.set("from", filters.from);
  if (filters.to) params.set("to", filters.to);
  if (typeof filters.skip === "number") params.set("skip", String(filters.skip));
  if (typeof filters.limit === "number") params.set("limit", String(filters.limit));
  return params.toString();
}

export async function triggerIntegrationSync(
  system: ExternalSystem,
  accountId: string,
  operation: string,
  payload?: Record<string, unknown> | null,
): Promise<SyncTriggerResult> {
  return apiFetch<SyncTriggerResult>(
    `/api/integration-accounts/${system}/${accountId}/sync`,
    {
      method: "POST",
      body: JSON.stringify({ operation, payload: payload ?? null }),
    },
  );
}

export async function listIntegrationSyncLogs(
  system: ExternalSystem,
  accountId: string,
  filters: SyncLogFilters = {},
): Promise<SyncLogEntry[]> {
  const query = buildSyncLogQuery(filters);
  return apiFetch<SyncLogEntry[]>(
    `/api/integration-accounts/${system}/${accountId}/sync-logs${
      query ? `?${query}` : ""
    }`,
  );
}

export async function getIntegrationSyncLog(
  system: ExternalSystem,
  accountId: string,
  logId: string,
): Promise<SyncLogEntry> {
  return apiFetch<SyncLogEntry>(
    `/api/integration-accounts/${system}/${accountId}/sync-logs/${logId}`,
  );
}

// The per-system operations registry mirrors the backend's
// `app.workers.jobs.OPERATIONS`. The SyncPanel uses it to keep the
// "Sincronizar ahora" button disabled while a connector hasn't shipped
// yet. Sprint A PR-2 lands AgileCRM; future PRs add Brevo, Freshdesk
// and FactuSOL.
export const SYSTEM_OPERATIONS: Partial<Record<ExternalSystem, string[]>> = {
  agilecrm: ["sync_contacts", "purge_quota"],
  // Brevo's connector landed in PR #51 (read sync) + PR #52 (write
  // targets, webhooks, segments mirror). All operations are
  // registered server-side under the same `<system>:<operation>`
  // contract; the first entry is the "default" the SyncPanel
  // dispatches when the operator clicks "Sincronizar ahora".
  brevo: ["sync_contacts", "refresh_segments"],
};

export function hasOperationsRegistered(system: ExternalSystem): boolean {
  const list = SYSTEM_OPERATIONS[system];
  return Array.isArray(list) && list.length > 0;
}

export function defaultOperationFor(system: ExternalSystem): string | null {
  const list = SYSTEM_OPERATIONS[system];
  return list && list.length ? list[0] : null;
}

export function hasOperation(system: ExternalSystem, operation: string): boolean {
  const list = SYSTEM_OPERATIONS[system];
  return Array.isArray(list) && list.includes(operation);
}
