export type ExternalSystem = "agilecrm" | "brevo" | "freshdesk" | "factusol";
export type IntegrationMode = "sandbox" | "live";
export type IntegrationStatus = "not_configured" | "configured" | "paused";

export type IntegrationSetting = {
  id: string;
  system: ExternalSystem;
  display_name: string;
  enabled: boolean;
  mode: IntegrationMode;
  status: IntegrationStatus;
  api_base_url?: string | null;
  account_label?: string | null;
  credential_status: string;
  notes?: string | null;
  has_api_key: boolean;
  api_key_set_at?: string | null;
  api_key_last_used_at?: string | null;
  created_at: string;
  updated_at: string;
};

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const TOKEN_STORAGE_KEY = "crmbomedia_access_token";

function getStoredToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_STORAGE_KEY);
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getStoredToken();
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...init?.headers,
    },
    cache: "no-store",
  });

  if (!response.ok) {
    let message = `API request failed with ${response.status}`;
    try {
      const body = await response.json();
      message = body.detail ?? message;
    } catch {
      message = await response.text();
    }
    throw new Error(message);
  }

  return response.json() as Promise<T>;
}

export async function getIntegrationSettings(): Promise<IntegrationSetting[]> {
  return apiFetch<IntegrationSetting[]>("/api/integration-settings");
}

export async function updateIntegrationSetting(
  system: ExternalSystem,
  payload: Record<string, unknown>,
): Promise<IntegrationSetting> {
  return apiFetch<IntegrationSetting>(`/api/integration-settings/${system}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function setIntegrationApiKey(
  system: ExternalSystem,
  apiKey: string,
): Promise<IntegrationSetting> {
  return apiFetch<IntegrationSetting>(`/api/integration-settings/${system}/api-key`, {
    method: "PUT",
    body: JSON.stringify({ api_key: apiKey }),
  });
}

export async function deleteIntegrationApiKey(
  system: ExternalSystem,
): Promise<IntegrationSetting> {
  return apiFetch<IntegrationSetting>(`/api/integration-settings/${system}/api-key`, {
    method: "DELETE",
  });
}
