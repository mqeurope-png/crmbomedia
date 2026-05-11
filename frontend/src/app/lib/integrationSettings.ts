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
