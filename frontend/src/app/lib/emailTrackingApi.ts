import { apiFetch } from "./api";

export type UserPreferences = {
  email_include_unsubscribe_default: boolean;
};

export async function getMyPreferences(): Promise<UserPreferences> {
  return apiFetch<UserPreferences>("/api/users/me/preferences");
}

export async function updateMyPreferences(
  payload: UserPreferences,
): Promise<UserPreferences> {
  return apiFetch<UserPreferences>("/api/users/me/preferences", {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export type EmailEvent = {
  id: string;
  message_id: string;
  event_type:
    | "sent"
    | "delivered"
    | "open"
    | "click"
    | "bounce"
    | "complaint"
    | "unsubscribe";
  occurred_at: string;
  ip: string | null;
  user_agent: string | null;
  metadata_json: string | null;
};

export type MessageEventsResponse = {
  message_id: string;
  events: EmailEvent[];
};

export async function getMessageEvents(
  messageId: string,
): Promise<MessageEventsResponse> {
  return apiFetch<MessageEventsResponse>(
    `/api/emails/messages/${messageId}/events`,
  );
}

export type EmailStats = {
  sent: number;
  opened: number;
  clicked: number;
  bounced: number;
  unsubscribed: number;
  days: number;
};

export async function getEmailStats(
  days = 30,
  options: { scope?: "mine" | "team"; teamUserId?: string } = {},
): Promise<EmailStats> {
  // QoL hotfix — el widget en /emails ahora respeta el toggle Mías/
  // Equipo igual que la lista de threads. Default mine.
  const params = new URLSearchParams({ days: String(days) });
  if (options.scope && options.scope !== "mine") {
    params.set("scope", options.scope);
  }
  if (options.teamUserId) params.set("team_user_id", options.teamUserId);
  return apiFetch<EmailStats>(`/api/emails/stats?${params.toString()}`);
}
