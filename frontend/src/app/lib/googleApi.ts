import { apiFetch } from "./api";

/** Backend tells the UI three things: whether the integration is wired
 *  up at all (admin config), whether the current user is connected,
 *  and (if connected) which calendar they picked. The CTA on
 *  /account is decided from these three flags. */
export type GoogleCalendarSelection = {
  id: string;
  summary: string | null;
};

export type GoogleStatus = {
  configured: boolean;
  connected: boolean;
  google_email: string | null;
  selected_calendar: GoogleCalendarSelection | null;
  requires_calendar_selection: boolean;
  connected_at: string | null;
  last_sync_at: string | null;
};

export type GoogleCalendarItem = {
  id: string;
  summary: string;
  primary: boolean;
  access_role: string | null;
  background_color: string | null;
};

export async function getGoogleStatus(): Promise<GoogleStatus> {
  return apiFetch<GoogleStatus>("/api/integrations/google/status");
}

export type GoogleScopesStatus = {
  calendar_events: boolean;
  calendar_readonly: boolean;
  gmail_send: boolean;
  gmail_modify: boolean;
  gmail_settings: boolean;
};

export async function getGoogleScopesStatus(): Promise<GoogleScopesStatus> {
  return apiFetch<GoogleScopesStatus>(
    "/api/integrations/google/scopes-status",
  );
}

export async function listGoogleCalendars(): Promise<GoogleCalendarItem[]> {
  return apiFetch<GoogleCalendarItem[]>("/api/integrations/google/calendars");
}

export async function selectGoogleCalendar(
  calendarId: string,
): Promise<GoogleStatus> {
  return apiFetch<GoogleStatus>("/api/integrations/google/calendar", {
    method: "PATCH",
    body: JSON.stringify({ calendar_id: calendarId }),
  });
}

export async function disconnectGoogle(): Promise<void> {
  await apiFetch("/api/integrations/google/disconnect", { method: "DELETE" });
}

/** Fetch the consent URL from the backend and navigate the browser
 *  to it. Indirect because the Bearer token lives in localStorage
 *  and wouldn't accompany a top-level navigation. */
export async function startGoogleConnect(): Promise<void> {
  const response = await apiFetch<{ url: string }>(
    "/api/integrations/google/authorize",
  );
  window.location.href = response.url;
}
