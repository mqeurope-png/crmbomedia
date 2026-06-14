import { apiFetch } from "./api";

export type EmailAlias = {
  send_as_email: string;
  display_name: string;
  is_primary: boolean;
  is_default: boolean;
  verification_status: string | null;
  user_pref_allowed: boolean;
  user_pref_default: boolean;
};

export type MyAlias = {
  send_as_email: string;
  display_name: string;
  is_default: boolean;
};

export type AliasPreferenceItem = {
  alias_email: string;
  is_allowed: boolean;
  is_default: boolean;
};

export async function getMyEmailAliases(): Promise<MyAlias[]> {
  return apiFetch<MyAlias[]>("/api/emails/my-aliases");
}

export async function putEmailAliasPreferences(
  preferences: AliasPreferenceItem[],
): Promise<EmailAlias[]> {
  return apiFetch<EmailAlias[]>("/api/emails/aliases/preferences", {
    method: "PUT",
    body: JSON.stringify({ preferences }),
  });
}

export type EmailMessage = {
  id: string;
  thread_id: string;
  gmail_message_id: string;
  direction: "outbound" | "inbound";
  from_email: string;
  from_name: string | null;
  to_emails: string[];
  cc_emails: string[] | null;
  subject: string | null;
  body_html: string | null;
  body_text: string | null;
  snippet: string | null;
  sent_at: string;
  contact_id: string | null;
  created_by_user_id: string | null;
  read_at: string | null;
};

export type EmailThread = {
  id: string;
  contact_id: string | null;
  initiated_by_user_id: string;
  gmail_thread_id: string;
  gmail_account_user_id: string;
  subject: string | null;
  participants: string[];
  first_message_at: string;
  last_message_at: string;
  message_count: number;
  has_unread_replies: boolean;
  is_archived: boolean;
  last_message_direction?: "outbound" | "inbound" | null;
  last_message_from?: string | null;
  last_message_snippet?: string | null;
  /** v2.1.1: contact name resolved server-side from Contact row or
   *  from the last message's `from_name` / email local part. */
  contact_name?: string | null;
  /** v2.3b: per-thread tracking counts (open / click / bounce /
   *  unsubscribe) aggregated across the thread's outbound messages.
   *  `sent` is excluded. Empty object when nothing tracked yet. */
  tracking?: Record<string, number>;
};

export type EmailThreadDetail = EmailThread & {
  messages: EmailMessage[];
  /** Server-computed address the "Responder" button should target —
   *  the last sender that isn't one of the operator's own aliases.
   *  Trustworthy where `direction` isn't (a comercial replying from
   *  Gmail surfaces as inbound). */
  reply_to_suggestion?: string | null;
};

export type EmailThreadList = {
  items: EmailThread[];
  total: number;
};

export type EmailSendPayload = {
  from_alias: string;
  from_name?: string | null;
  to: string[];
  cc?: string[] | null;
  bcc?: string[] | null;
  subject?: string;
  body_html?: string | null;
  body_text?: string | null;
  contact_id?: string | null;
  in_reply_to_message_id?: string | null;
  /** Sprint Email v2.3a/b — when omitted the backend falls back to the
   *  operator's `email_include_unsubscribe_default`. The send modal
   *  always sends an explicit value because the toggle defaults to
   *  the operator's preference at mount time. */
  include_unsubscribe?: boolean | null;
};

export async function getEmailAliases(): Promise<EmailAlias[]> {
  return apiFetch<EmailAlias[]>("/api/emails/aliases");
}

export async function sendEmail(
  payload: EmailSendPayload,
): Promise<EmailMessage> {
  return apiFetch<EmailMessage>("/api/emails/send", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function listEmailThreads(
  contactId?: string,
  q?: string,
): Promise<EmailThreadList> {
  const params = new URLSearchParams();
  if (contactId) params.set("contact_id", contactId);
  if (q && q.trim()) params.set("q", q.trim());
  const qs = params.toString();
  return apiFetch<EmailThreadList>(`/api/emails/threads${qs ? `?${qs}` : ""}`);
}

export async function getEmailThread(id: string): Promise<EmailThreadDetail> {
  return apiFetch<EmailThreadDetail>(`/api/emails/threads/${id}`);
}

export async function markThreadRead(id: string): Promise<void> {
  await apiFetch(`/api/emails/threads/${id}/mark-read`, { method: "POST" });
}

export async function listAdminEmailThreads(): Promise<EmailThreadList> {
  return apiFetch<EmailThreadList>("/api/emails/admin/all-threads");
}

export type EmailActivityItem = {
  type: "email.sent_from_crm" | "email.reply_received";
  direction: "outbound" | "inbound";
  thread_id: string;
  message_id: string;
  subject: string | null;
  contact_id: string | null;
  contact_name: string | null;
  from_email: string;
  occurred_at: string;
  snippet: string | null;
};

export async function getEmailActivity(
  scope: "mine" | "all",
  limit = 5,
): Promise<EmailActivityItem[]> {
  return apiFetch<EmailActivityItem[]>(
    `/api/emails/activity?scope=${scope}&limit=${limit}`,
  );
}
