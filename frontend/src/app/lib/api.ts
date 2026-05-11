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
};

export type Task = {
  id: string;
  title: string;
  status: "open" | "done" | "cancelled";
  due_at?: string | null;
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
  notes?: Note[];
  tasks?: Task[];
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
  return apiFetch<Contact[]>("/api/contacts?limit=20");
}

export async function getContact(id: string): Promise<Contact> {
  return apiFetch<Contact>(`/api/contacts/${id}`);
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
  action: string;
  entity_type: string;
  entity_id?: string | null;
  message?: string | null;
  created_at: string;
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

export async function getAuditLogs(): Promise<AuditLog[]> {
  return apiFetch<AuditLog[]>("/api/audit-logs?limit=100");
}

export async function exportAuditLogs(format: "csv" | "json"): Promise<Blob> {
  const token = getStoredToken();
  const response = await fetch(`${API_BASE_URL}/api/audit-logs/export?format=${format}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!response.ok) {
    throw new Error(`Audit export failed with ${response.status}`);
  }
  return response.blob();
}
