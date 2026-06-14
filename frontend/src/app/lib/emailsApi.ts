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
  /** v2.4e: nullable while a scheduled-send row waits for Gmail. */
  gmail_message_id: string | null;
  direction: "outbound" | "inbound";
  from_email: string;
  from_name: string | null;
  to_emails: string[];
  cc_emails: string[] | null;
  subject: string | null;
  body_html: string | null;
  body_text: string | null;
  snippet: string | null;
  /** v2.4e: nullable for the same reason — set by the sweep on send. */
  sent_at: string | null;
  contact_id: string | null;
  created_by_user_id: string | null;
  read_at: string | null;
  /** v2.4e scheduled-send fields. */
  scheduled_for?: string | null;
  scheduled_status?: "pending" | "sent" | "cancelled" | "failed" | null;
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
  /** v2.4a: mailbox state — inbox / archived / trashed / spam. */
  state?: EmailThreadStateValue;
  folder_id?: string | null;
  is_starred?: boolean;
  snooze_until?: string | null;
  labels?: EmailLabel[];
};

export type EmailThreadStateValue =
  | "inbox"
  | "archived"
  | "trashed"
  | "spam";

export type EmailFolder = {
  id: string;
  name: string;
  parent_id: string | null;
  color: string | null;
  icon: string | null;
  sort_order: number;
  is_system: boolean;
  unread_count?: number;
  total_count?: number;
};

export type EmailLabel = {
  id: string;
  name: string;
  color: string | null;
  sort_order: number;
};

export type EmailFolderWrite = {
  name: string;
  parent_id?: string | null;
  color?: string | null;
  icon?: string | null;
  sort_order?: number;
};

export type EmailLabelWrite = {
  name: string;
  color?: string | null;
  sort_order?: number;
};

export type EmailThreadListFilters = {
  state?: EmailThreadStateValue;
  folder_id?: string;
  label_id?: string;
  starred?: boolean;
  has_unread?: boolean;
  since?: string;
  until?: string;
  include_snoozed?: boolean;
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
  /** Sprint Email v2.4e — ISO date in the future routes the send
   *  through the pending queue instead of Gmail. NULL = send now. */
  scheduled_for?: string | null;
};

export type ScheduledMessageUpdate = {
  scheduled_for?: string;
  subject?: string;
  body_html?: string;
  body_text?: string;
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
  filters?: EmailThreadListFilters,
): Promise<EmailThreadList> {
  const params = new URLSearchParams();
  if (contactId) params.set("contact_id", contactId);
  if (q && q.trim()) params.set("q", q.trim());
  if (filters?.state) params.set("state", filters.state);
  if (filters?.folder_id) params.set("folder_id", filters.folder_id);
  if (filters?.label_id) params.set("label_id", filters.label_id);
  if (filters?.starred !== undefined) {
    params.set("starred", String(filters.starred));
  }
  if (filters?.has_unread !== undefined) {
    params.set("has_unread", String(filters.has_unread));
  }
  if (filters?.since) params.set("since", filters.since);
  if (filters?.until) params.set("until", filters.until);
  if (filters?.include_snoozed) params.set("include_snoozed", "true");
  const qs = params.toString();
  return apiFetch<EmailThreadList>(`/api/emails/threads${qs ? `?${qs}` : ""}`);
}

// --- v2.4a/b mailbox: folders, labels, mutations, bulk ----------------

export async function listEmailFolders(): Promise<EmailFolder[]> {
  return apiFetch<EmailFolder[]>("/api/emails/folders");
}

export async function createEmailFolder(
  payload: EmailFolderWrite,
): Promise<EmailFolder> {
  return apiFetch<EmailFolder>("/api/emails/folders", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updateEmailFolder(
  id: string,
  payload: EmailFolderWrite,
): Promise<EmailFolder> {
  return apiFetch<EmailFolder>(`/api/emails/folders/${id}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export async function deleteEmailFolder(id: string): Promise<void> {
  await apiFetch(`/api/emails/folders/${id}`, { method: "DELETE" });
}

export async function listEmailLabels(): Promise<EmailLabel[]> {
  return apiFetch<EmailLabel[]>("/api/emails/labels");
}

export async function createEmailLabel(
  payload: EmailLabelWrite,
): Promise<EmailLabel> {
  return apiFetch<EmailLabel>("/api/emails/labels", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updateEmailLabel(
  id: string,
  payload: EmailLabelWrite,
): Promise<EmailLabel> {
  return apiFetch<EmailLabel>(`/api/emails/labels/${id}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export async function deleteEmailLabel(id: string): Promise<void> {
  await apiFetch(`/api/emails/labels/${id}`, { method: "DELETE" });
}

// Per-thread mutations.
export async function starThread(id: string): Promise<void> {
  await apiFetch(`/api/emails/threads/${id}/star`, { method: "POST" });
}

export async function unstarThread(id: string): Promise<void> {
  await apiFetch(`/api/emails/threads/${id}/unstar`, { method: "POST" });
}

export async function moveThread(
  id: string,
  folder_id: string | null,
): Promise<void> {
  await apiFetch(`/api/emails/threads/${id}/move`, {
    method: "POST",
    body: JSON.stringify({ folder_id }),
  });
}

export async function archiveThread(id: string): Promise<void> {
  await apiFetch(`/api/emails/threads/${id}/archive`, { method: "POST" });
}

export async function trashThread(id: string): Promise<void> {
  await apiFetch(`/api/emails/threads/${id}/trash`, { method: "POST" });
}

export async function spamThread(id: string): Promise<void> {
  await apiFetch(`/api/emails/threads/${id}/spam`, { method: "POST" });
}

export async function restoreThread(id: string): Promise<void> {
  await apiFetch(`/api/emails/threads/${id}/restore`, { method: "POST" });
}

export async function addThreadLabel(
  thread_id: string,
  label_id: string,
): Promise<EmailLabel> {
  return apiFetch<EmailLabel>(
    `/api/emails/threads/${thread_id}/labels/${label_id}`,
    { method: "POST" },
  );
}

export async function removeThreadLabel(
  thread_id: string,
  label_id: string,
): Promise<void> {
  await apiFetch(
    `/api/emails/threads/${thread_id}/labels/${label_id}`,
    { method: "DELETE" },
  );
}

export async function markThreadUnread(id: string): Promise<void> {
  await apiFetch(`/api/emails/threads/${id}/mark-unread`, {
    method: "POST",
  });
}

// Bulk operations. All routes accept `{ thread_ids: string[], ... }`
// and return `{ affected: number }`.
export type BulkAffected = { affected: number };

async function bulkPost<T = BulkAffected>(
  path: string,
  body: Record<string, unknown>,
): Promise<T> {
  return apiFetch<T>(`/api/emails/threads-bulk/${path}`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export const bulkArchive = (ids: string[]) =>
  bulkPost("archive", { thread_ids: ids });
export const bulkTrash = (ids: string[]) =>
  bulkPost("trash", { thread_ids: ids });
export const bulkSpam = (ids: string[]) =>
  bulkPost("spam", { thread_ids: ids });
export const bulkRestore = (ids: string[]) =>
  bulkPost("restore", { thread_ids: ids });
export const bulkStar = (ids: string[]) =>
  bulkPost("star", { thread_ids: ids });
export const bulkUnstar = (ids: string[]) =>
  bulkPost("unstar", { thread_ids: ids });
export const bulkMarkRead = (ids: string[]) =>
  bulkPost("mark-read", { thread_ids: ids });
export const bulkMarkUnread = (ids: string[]) =>
  bulkPost("mark-unread", { thread_ids: ids });
export const bulkMove = (ids: string[], folder_id: string | null) =>
  bulkPost("move", { thread_ids: ids, folder_id });
export const bulkAddLabel = (ids: string[], label_id: string) =>
  bulkPost("labels/add", { thread_ids: ids, label_id });
export const bulkRemoveLabel = (ids: string[], label_id: string) =>
  bulkPost("labels/remove", { thread_ids: ids, label_id });

// --- v2.4e scheduled send --------------------------------------------

export async function listScheduledMessages(): Promise<EmailMessage[]> {
  return apiFetch<EmailMessage[]>("/api/emails/scheduled");
}

export async function cancelScheduledMessage(
  id: string,
): Promise<EmailMessage> {
  return apiFetch<EmailMessage>(`/api/emails/scheduled/${id}/cancel`, {
    method: "POST",
  });
}

export async function updateScheduledMessage(
  id: string,
  payload: ScheduledMessageUpdate,
): Promise<EmailMessage> {
  return apiFetch<EmailMessage>(`/api/emails/scheduled/${id}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
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
