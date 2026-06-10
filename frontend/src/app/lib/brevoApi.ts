/**
 * Brevo-specific API surface (`/api/brevo/*`): sync targets, lists,
 * senders, templates and campaigns. Kept apart from `api.ts` so the
 * marketing module's types stay contained.
 */
import { apiFetch } from "./api";

// ----- Sync targets -----

export type BrevoSyncTarget = {
  id: string;
  brevo_account_id: string;
  name: string;
  description?: string | null;
  segment_id: string;
  segment_name?: string | null;
  brevo_list_id?: string | null;
  sync_direction: "push_only" | "pull_only" | "bidirectional";
  is_active: boolean;
  last_run_at?: string | null;
  last_run_status: "idle" | "running" | "success" | "partial_error" | "error";
  last_run_stats?: Record<string, unknown> | null;
  auto_sync_enabled: boolean;
  sync_interval_minutes: number;
  created_at: string;
  updated_at: string;
};

export type BrevoTargetRunResponse = {
  sync_log_id?: string | null;
  job_id?: string | null;
  dry_run: boolean;
  stats?: Record<string, unknown> | null;
};

export async function listBrevoSyncTargets(
  accountId: string,
): Promise<BrevoSyncTarget[]> {
  return apiFetch<BrevoSyncTarget[]>(
    `/api/brevo/sync-targets?account_id=${encodeURIComponent(accountId)}`,
  );
}

export async function createBrevoSyncTarget(payload: {
  brevo_account_id: string;
  name: string;
  description?: string | null;
  segment_id: string;
  brevo_list_id?: string | null;
  sync_direction?: string;
  auto_sync_enabled?: boolean;
  sync_interval_minutes?: number;
}): Promise<BrevoSyncTarget> {
  return apiFetch<BrevoSyncTarget>("/api/brevo/sync-targets", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updateBrevoSyncTarget(
  id: string,
  payload: Partial<{
    name: string;
    description: string | null;
    segment_id: string;
    brevo_list_id: string | null;
    sync_direction: string;
    is_active: boolean;
    auto_sync_enabled: boolean;
    sync_interval_minutes: number;
  }>,
): Promise<BrevoSyncTarget> {
  return apiFetch<BrevoSyncTarget>(`/api/brevo/sync-targets/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function deleteBrevoSyncTarget(
  id: string,
): Promise<{ message: string }> {
  return apiFetch<{ message: string }>(`/api/brevo/sync-targets/${id}`, {
    method: "DELETE",
  });
}

export async function runBrevoSyncTarget(
  id: string,
  options: { dryRun?: boolean } = {},
): Promise<BrevoTargetRunResponse> {
  const query = options.dryRun ? "?dry_run=true" : "";
  return apiFetch<BrevoTargetRunResponse>(
    `/api/brevo/sync-targets/${id}/run${query}`,
    { method: "POST" },
  );
}

// ----- Lists / senders -----

export type BrevoList = {
  id: number;
  name: string;
  total_subscribers: number;
  folder_id?: number | null;
};

export type BrevoSender = {
  id: number;
  name: string;
  email: string;
  active: boolean;
};

export async function listBrevoLists(accountId: string): Promise<BrevoList[]> {
  return apiFetch<BrevoList[]>(
    `/api/brevo/lists?account_id=${encodeURIComponent(accountId)}`,
  );
}

export async function listBrevoSenders(
  accountId: string,
): Promise<BrevoSender[]> {
  return apiFetch<BrevoSender[]>(
    `/api/brevo/senders?account_id=${encodeURIComponent(accountId)}`,
  );
}

export type BrevoWebhookStats = {
  total: number;
  by_type: Record<string, number>;
};

export async function getBrevoWebhookStats(
  accountId: string,
): Promise<BrevoWebhookStats> {
  return apiFetch<BrevoWebhookStats>(
    `/api/brevo/webhook-stats?account_id=${encodeURIComponent(accountId)}`,
  );
}

// ----- Templates -----

export type BrevoTemplate = {
  id: string;
  brevo_account_id: string;
  brevo_template_id: number;
  name: string;
  subject?: string | null;
  is_active: boolean;
  tag?: string | null;
  sender_name?: string | null;
  sender_email?: string | null;
  created_at_brevo?: string | null;
  modified_at_brevo?: string | null;
  cached_at: string;
  html_content?: string | null;
};

export async function listBrevoTemplates(
  accountId: string,
  options: { refresh?: boolean } = {},
): Promise<BrevoTemplate[]> {
  const refresh = options.refresh ? "&refresh=true" : "";
  return apiFetch<BrevoTemplate[]>(
    `/api/brevo/templates?account_id=${encodeURIComponent(accountId)}${refresh}`,
  );
}

export async function getBrevoTemplate(id: string): Promise<BrevoTemplate> {
  return apiFetch<BrevoTemplate>(`/api/brevo/templates/${id}`);
}

export async function createBrevoTemplate(payload: {
  brevo_account_id: string;
  name: string;
  subject: string;
  html_content: string;
  sender_name: string;
  sender_email: string;
  tag?: string | null;
  is_active?: boolean;
}): Promise<BrevoTemplate> {
  return apiFetch<BrevoTemplate>("/api/brevo/templates", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updateBrevoTemplate(
  id: string,
  payload: Partial<{
    name: string;
    subject: string;
    html_content: string;
    sender_name: string;
    sender_email: string;
    tag: string | null;
    is_active: boolean;
  }>,
): Promise<BrevoTemplate> {
  return apiFetch<BrevoTemplate>(`/api/brevo/templates/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function deleteBrevoTemplate(
  id: string,
): Promise<{ message: string }> {
  return apiFetch<{ message: string }>(`/api/brevo/templates/${id}`, {
    method: "DELETE",
  });
}

export async function sendBrevoTemplateTest(
  id: string,
  emails: string[],
): Promise<{ message: string }> {
  return apiFetch<{ message: string }>(
    `/api/brevo/templates/${id}/send-test`,
    { method: "POST", body: JSON.stringify({ emails }) },
  );
}

// ----- Primary Brevo account discovery -----

/**
 * The UI assumes one primary Brevo account (multi-account is data-model
 * ready but out of scope). Resolves the first enabled account of the
 * `brevo` group from the shared integrations endpoint.
 */
export async function resolvePrimaryBrevoAccount(): Promise<string | null> {
  type Group = {
    system: string;
    accounts: Array<{ account_id: string; enabled: boolean }>;
  };
  const groups = await apiFetch<Group[]>("/api/integrations/accounts");
  const brevo = groups.find((group) => group.system === "brevo");
  if (!brevo) return null;
  const enabled = brevo.accounts.find((account) => account.enabled);
  return enabled?.account_id ?? brevo.accounts[0]?.account_id ?? null;
}
