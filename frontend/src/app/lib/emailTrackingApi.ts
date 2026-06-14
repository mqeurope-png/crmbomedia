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

export async function getEmailStats(days = 30): Promise<EmailStats> {
  return apiFetch<EmailStats>(`/api/emails/stats?days=${days}`);
}
