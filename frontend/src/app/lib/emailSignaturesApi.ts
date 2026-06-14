import { apiFetch } from "./api";

export type EmailSignature = {
  id: string;
  name: string;
  html_content: string;
  is_default: boolean;
  sort_order: number;
  created_at: string;
  updated_at: string;
};

export type EmailSignatureWrite = {
  name: string;
  html_content: string;
  is_default?: boolean;
  sort_order?: number;
};

export async function listEmailSignatures(): Promise<EmailSignature[]> {
  return apiFetch<EmailSignature[]>("/api/email-signatures");
}

export async function getDefaultEmailSignature(): Promise<
  EmailSignature | null
> {
  return apiFetch<EmailSignature | null>("/api/email-signatures/default");
}

export async function createEmailSignature(
  payload: EmailSignatureWrite,
): Promise<EmailSignature> {
  return apiFetch<EmailSignature>("/api/email-signatures", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updateEmailSignature(
  id: string,
  payload: EmailSignatureWrite,
): Promise<EmailSignature> {
  return apiFetch<EmailSignature>(`/api/email-signatures/${id}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export async function deleteEmailSignature(
  id: string,
): Promise<{ message: string }> {
  return apiFetch<{ message: string }>(`/api/email-signatures/${id}`, {
    method: "DELETE",
  });
}

export async function setDefaultEmailSignature(
  id: string,
): Promise<EmailSignature> {
  return apiFetch<EmailSignature>(`/api/email-signatures/${id}/default`, {
    method: "POST",
  });
}
