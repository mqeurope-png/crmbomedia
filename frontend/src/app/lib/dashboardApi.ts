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
