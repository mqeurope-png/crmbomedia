import { apiFetch } from "./api";
import type { Task } from "./tasksApi";

export type GoogleCalendarEvent = {
  id: string | null;
  summary: string | null;
  start: string | null;
  end: string | null;
  html_link: string | null;
  all_day: boolean;
};

export type GoogleCalendarEventsResponse = {
  connected: boolean;
  events: GoogleCalendarEvent[];
  calendar_summary: string | null;
};

export type PipelineSummaryStage = {
  id: string;
  name: string;
  color: string | null;
  count: number;
};

export type PipelineSummary = {
  pipeline_id: string;
  pipeline_name: string;
  pipeline_color: string | null;
  stages: PipelineSummaryStage[];
};

export type UnattendedLead = {
  id: string;
  first_name: string;
  last_name: string | null;
  email: string | null;
  phone: string | null;
  owner_user_id: string | null;
  created_at: string;
};

export type LeadsStatsRange = "7d" | "30d" | "90d";
export type LeadsStatsBucket = "day" | "week" | "month";

export type LeadsStats = {
  range: LeadsStatsRange;
  bucket: LeadsStatsBucket;
  series: Array<{ bucket: string; count: number }>;
  totals: {
    leads_current: number;
    leads_previous: number;
    delta_pct: number | null;
    qualified_pct: number;
    closed_won_pct: number;
  };
};

export type RecentEmailEvent = {
  id: string;
  event_type: string;
  subject: string | null;
  occurred_at: string;
  contact_id: string;
  contact_name: string;
  contact_email: string | null;
};

export async function getDashboardTasksPending(): Promise<Task[]> {
  return apiFetch<Task[]>("/api/dashboard/tasks-pending?limit=8");
}

export async function getDashboardGoogleEvents(): Promise<GoogleCalendarEventsResponse> {
  return apiFetch<GoogleCalendarEventsResponse>(
    "/api/dashboard/google-calendar-events?limit=5",
  );
}

export async function getDashboardPipelineSummary(): Promise<PipelineSummary[]> {
  return apiFetch<PipelineSummary[]>("/api/dashboard/pipeline-summary");
}

export async function getDashboardUnattendedLeads(): Promise<UnattendedLead[]> {
  return apiFetch<UnattendedLead[]>("/api/dashboard/unattended-leads?limit=10");
}

export async function getDashboardLeadsStats(
  range: LeadsStatsRange,
  bucket: LeadsStatsBucket,
): Promise<LeadsStats> {
  return apiFetch<LeadsStats>(
    `/api/dashboard/leads-stats?range=${range}&bucket=${bucket}`,
  );
}

export async function getDashboardRecentEmailActivity(
  scope: "mine" | "all",
): Promise<RecentEmailEvent[]> {
  return apiFetch<RecentEmailEvent[]>(
    `/api/dashboard/recent-email-activity?limit=15&scope=${scope}`,
  );
}

// PR-E2: nuevos endpoints del dashboard.
export type PriorityLead = {
  id: string;
  first_name: string;
  last_name: string | null;
  email: string;
  phone: string | null;
  signal_at: string;
  reason: "recent" | "assigned" | "active" | string;
};

export type UserCampaignStat = {
  user_id: string;
  full_name: string;
  email: string;
  received: number;
  opened: number;
  clicked: number;
  open_rate: number;
  click_rate: number;
};

export type RecentInteraction = {
  id: string;
  event_type: string;
  subject: string | null;
  body: string | null;
  occurred_at: string;
  contact_id: string;
  contact_name: string;
  contact_email: string | null;
  campaign_brevo_id: number | null;
};

// PR-E3: la escala de períodos crece a 3d/7d/15d/30d + custom. "14d"
// se conserva por compat con llamadas previas (el backend lo acepta).
export type DashboardPeriod = "3d" | "7d" | "14d" | "15d" | "30d" | "custom";

export type DashboardWindow = {
  period: DashboardPeriod;
  /** ISO datetimes — solo cuando period === "custom". */
  start?: string | null;
  end?: string | null;
};

function windowParams(w: DashboardWindow): string {
  const p = new URLSearchParams({ period: w.period });
  if (w.period === "custom") {
    if (w.start) p.set("start", w.start);
    if (w.end) p.set("end", w.end);
  }
  return p.toString();
}

export async function getDashboardUpcomingTasks(limit = 8): Promise<Task[]> {
  return apiFetch<Task[]>(`/api/dashboard/upcoming-tasks?limit=${limit}`);
}

export async function getDashboardPriorityLeads(
  window: DashboardWindow = { period: "7d" },
  limit = 10,
): Promise<PriorityLead[]> {
  return apiFetch<PriorityLead[]>(
    `/api/dashboard/priority-leads?${windowParams(window)}&limit=${limit}`,
  );
}

export type MyCampaignStats = {
  received: number;
  opened: number;
  clicked: number;
  open_rate: number;
  click_rate: number;
};

export async function getDashboardMyCampaignStats(
  window: DashboardWindow = { period: "30d" },
): Promise<MyCampaignStats> {
  return apiFetch<MyCampaignStats>(
    `/api/dashboard/my-campaign-stats?${windowParams(window)}`,
  );
}

export async function getDashboardUserCampaignStats(
  window: DashboardWindow = { period: "30d" },
  limit = 5,
): Promise<UserCampaignStat[]> {
  return apiFetch<UserCampaignStat[]>(
    `/api/dashboard/user-campaign-stats?${windowParams(window)}&limit=${limit}`,
  );
}

export async function getDashboardRecentInteractions(
  scope: "mine" | "team" = "mine",
  window: DashboardWindow = { period: "7d" },
  limit = 20,
): Promise<RecentInteraction[]> {
  return apiFetch<RecentInteraction[]>(
    `/api/dashboard/recent-interactions?scope=${scope}&${windowParams(window)}&limit=${limit}`,
  );
}
