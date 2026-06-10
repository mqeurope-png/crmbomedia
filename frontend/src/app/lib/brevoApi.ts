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
  sender: { senderName?: string; senderEmail?: string } = {},
): Promise<{ message: string }> {
  return apiFetch<{ message: string }>(
    `/api/brevo/templates/${id}/send-test`,
    {
      method: "POST",
      body: JSON.stringify({
        emails,
        // Brevo's sendTest uses the sender stored on the template;
        // sending the editor's selection lets the backend persist it
        // first so the test really goes out from the picked address.
        sender_name: sender.senderName ?? null,
        sender_email: sender.senderEmail ?? null,
      }),
    },
  );
}

// ----- Campaigns -----

export type BrevoCampaignStats = {
  sent?: number;
  delivered?: number;
  uniqueViews?: number;
  viewed?: number;
  uniqueClicks?: number;
  clickers?: number;
  hardBounces?: number;
  softBounces?: number;
  unsubscriptions?: number;
  complaints?: number;
  [key: string]: number | undefined;
};

export type BrevoCampaign = {
  id: string;
  brevo_account_id: string;
  brevo_campaign_id: number;
  name: string;
  subject?: string | null;
  status:
    | "draft"
    | "sent"
    | "archive"
    | "queued"
    | "suspended"
    | "in_process";
  type: string;
  sender_name?: string | null;
  sender_email?: string | null;
  reply_to?: string | null;
  created_at_brevo?: string | null;
  modified_at_brevo?: string | null;
  scheduled_at?: string | null;
  sent_at?: string | null;
  stats?: BrevoCampaignStats | null;
  recipient_list_ids?: number[] | null;
  template_id_used?: number | null;
  cached_at: string;
  /** Lazy-loaded by the detail endpoint. The list endpoint never
   * carries it. Used to render the iframe preview on the detail
   * page. */
  html_content?: string | null;
};

export type BrevoCampaignTimeline = {
  timeline: Array<{ day: string; opened: number; clicked: number }>;
  top_clicks: Array<{ url: string; count: number }>;
};

export type BrevoCampaignRecipients = {
  items: Array<{
    contact_id: string;
    first_name: string;
    last_name?: string | null;
    email?: string | null;
    event_type: string;
    occurred_at: string;
    detail?: string | null;
  }>;
  limit: number;
  offset: number;
};

export async function listBrevoCampaigns(
  accountId: string,
  options: { status?: string; refresh?: boolean } = {},
): Promise<BrevoCampaign[]> {
  const params = new URLSearchParams({ account_id: accountId });
  if (options.status) params.set("status", options.status);
  if (options.refresh) params.set("refresh", "true");
  return apiFetch<BrevoCampaign[]>(`/api/brevo/campaigns?${params.toString()}`);
}

export async function getBrevoCampaign(id: string): Promise<BrevoCampaign> {
  return apiFetch<BrevoCampaign>(`/api/brevo/campaigns/${id}`);
}

export async function createBrevoCampaign(payload: {
  brevo_account_id: string;
  name: string;
  subject: string;
  sender_name: string;
  sender_email: string;
  reply_to?: string | null;
  html_content?: string | null;
  template_id?: number | null;
  list_ids?: number[] | null;
  segment_id?: string | null;
  scheduled_at?: string | null;
}): Promise<BrevoCampaign> {
  return apiFetch<BrevoCampaign>("/api/brevo/campaigns", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updateBrevoCampaign(
  id: string,
  payload: Partial<{
    name: string;
    subject: string;
    sender_name: string;
    sender_email: string;
    reply_to: string | null;
    html_content: string;
  }>,
): Promise<BrevoCampaign> {
  return apiFetch<BrevoCampaign>(`/api/brevo/campaigns/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function deleteBrevoCampaign(
  id: string,
): Promise<{ message: string }> {
  return apiFetch<{ message: string }>(`/api/brevo/campaigns/${id}`, {
    method: "DELETE",
  });
}

export async function sendBrevoCampaignNow(
  id: string,
): Promise<{ message: string }> {
  return apiFetch<{ message: string }>(
    `/api/brevo/campaigns/${id}/send-now`,
    { method: "POST" },
  );
}

export async function scheduleBrevoCampaign(
  id: string,
  scheduledAt: string,
): Promise<{ message: string }> {
  return apiFetch<{ message: string }>(
    `/api/brevo/campaigns/${id}/schedule`,
    { method: "POST", body: JSON.stringify({ scheduled_at: scheduledAt }) },
  );
}

export async function cancelBrevoCampaignSchedule(
  id: string,
): Promise<{ message: string }> {
  return apiFetch<{ message: string }>(
    `/api/brevo/campaigns/${id}/cancel-schedule`,
    { method: "POST" },
  );
}

export async function sendBrevoCampaignTest(
  id: string,
  emails: string[],
): Promise<{ message: string }> {
  return apiFetch<{ message: string }>(
    `/api/brevo/campaigns/${id}/send-test`,
    { method: "POST", body: JSON.stringify({ emails }) },
  );
}

export async function getBrevoCampaignTimeline(
  id: string,
): Promise<BrevoCampaignTimeline> {
  return apiFetch<BrevoCampaignTimeline>(
    `/api/brevo/campaigns/${id}/timeline`,
  );
}

export async function getBrevoCampaignRecipients(
  id: string,
  eventType: string,
  options: { limit?: number; offset?: number } = {},
): Promise<BrevoCampaignRecipients> {
  const params = new URLSearchParams();
  if (options.limit !== undefined) params.set("limit", String(options.limit));
  if (options.offset !== undefined)
    params.set("offset", String(options.offset));
  const query = params.toString();
  return apiFetch<BrevoCampaignRecipients>(
    `/api/brevo/campaigns/${id}/recipients/${eventType}${query ? `?${query}` : ""}`,
  );
}

export const CAMPAIGN_STATUS_LABEL: Record<string, string> = {
  draft: "Borrador",
  queued: "Programada",
  in_process: "Enviando…",
  sent: "Enviada",
  suspended: "Suspendida",
  archive: "Archivada",
};

export function campaignStatusClass(status: string): string {
  if (status === "sent") return "is-on";
  if (status === "queued" || status === "in_process") return "is-pending";
  return "is-off";
}

/** Open rate / click rate helpers shared by list + detail + widget. */
export function campaignRates(stats?: BrevoCampaignStats | null): {
  openRate: number | null;
  clickRate: number | null;
} {
  if (!stats) return { openRate: null, clickRate: null };
  const delivered = stats.delivered ?? 0;
  if (!delivered) return { openRate: null, clickRate: null };
  const opened = stats.uniqueViews ?? stats.viewed ?? 0;
  const clicked = stats.uniqueClicks ?? stats.clickers ?? 0;
  return {
    openRate: Math.round((opened / delivered) * 1000) / 10,
    clickRate: Math.round((clicked / delivered) * 1000) / 10,
  };
}

// ----- Segments mirror -----

export async function refreshBrevoSegment(
  segmentId: string,
): Promise<{ sync_log_id: string; job_id: string }> {
  return apiFetch(`/api/brevo/segments/${segmentId}/refresh`, {
    method: "POST",
  });
}

export async function refreshAllBrevoSegments(
  accountId: string,
): Promise<{ sync_log_id: string; job_id: string }> {
  return apiFetch(
    `/api/brevo/segments/refresh-all?account_id=${encodeURIComponent(accountId)}`,
    { method: "POST" },
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
