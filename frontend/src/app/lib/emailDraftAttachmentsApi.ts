import { apiFetch, apiUpload } from "./api";

export type EmailDraftAttachment = {
  id: string;
  draft_id: string;
  filename: string;
  content_type: string | null;
  size_bytes: number;
  created_at: string;
};

export async function listEmailDraftAttachments(
  draftId: string,
): Promise<EmailDraftAttachment[]> {
  return apiFetch<EmailDraftAttachment[]>(
    `/api/email-drafts/${draftId}/attachments`,
  );
}

export async function uploadEmailDraftAttachment(
  draftId: string,
  file: File,
): Promise<EmailDraftAttachment> {
  const form = new FormData();
  form.append("file", file, file.name);
  return apiUpload<EmailDraftAttachment>(
    `/api/email-drafts/${draftId}/attachments`,
    form,
  );
}

export async function deleteEmailDraftAttachment(
  draftId: string,
  attachmentId: string,
): Promise<void> {
  await apiFetch(
    `/api/email-drafts/${draftId}/attachments/${attachmentId}`,
    { method: "DELETE" },
  );
}
