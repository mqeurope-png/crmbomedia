import { apiFetch } from "./api";

export type EmailTemplate = {
  id: string;
  name: string;
  subject: string | null;
  body_html: string;
  body_text: string | null;
  folder_id: string | null;
  owner_user_id: string | null;
  is_global: boolean;
  usage_count: number;
  last_used_at: string | null;
  created_at: string;
  updated_at: string;
};

export type EmailTemplateListItem = {
  id: string;
  name: string;
  subject: string | null;
  folder_id: string | null;
  owner_user_id: string | null;
  is_global: boolean;
  usage_count: number;
  last_used_at: string | null;
  updated_at: string;
};

export type EmailTemplateFolder = {
  id: string;
  name: string;
  parent_folder_id: string | null;
  owner_user_id: string | null;
  is_global: boolean;
  sort_order: number;
  created_at: string;
  updated_at: string;
};

export type EmailTemplateFolderNode = {
  id: string;
  name: string;
  is_global: boolean;
  sort_order: number;
  children: EmailTemplateFolderNode[];
  template_count: number;
};

export type EmailTemplateWrite = {
  name: string;
  subject?: string | null;
  body_html: string;
  folder_id?: string | null;
  is_global?: boolean;
};

export type EmailTemplateFolderWrite = {
  name: string;
  parent_folder_id?: string | null;
  is_global?: boolean;
  sort_order?: number;
};

export type BrevoPickerItem = {
  id: number;
  name: string;
  subject: string | null;
  sender_name: string | null;
  has_html: boolean;
};

export type EmailTemplatesPicker = {
  crm: EmailTemplateListItem[];
  brevo: BrevoPickerItem[];
  folders: EmailTemplateFolderNode[];
  recent: EmailTemplateListItem[];
};

export type ListTemplatesFilters = {
  folder_id?: string | null;
  q?: string;
  my_only?: boolean;
};

export async function listEmailTemplates(
  filters: ListTemplatesFilters = {},
): Promise<EmailTemplateListItem[]> {
  const params = new URLSearchParams();
  if (filters.folder_id !== undefined && filters.folder_id !== null) {
    params.set("folder_id", filters.folder_id);
  } else if (filters.folder_id === null) {
    params.set("folder_id", "");
  }
  if (filters.q) params.set("q", filters.q);
  if (filters.my_only) params.set("my-only", "true");
  const query = params.toString();
  return apiFetch<EmailTemplateListItem[]>(
    `/api/email-templates${query ? `?${query}` : ""}`,
  );
}

export async function getEmailTemplate(id: string): Promise<EmailTemplate> {
  return apiFetch<EmailTemplate>(`/api/email-templates/${id}`);
}

export async function createEmailTemplate(
  payload: EmailTemplateWrite,
): Promise<EmailTemplate> {
  return apiFetch<EmailTemplate>("/api/email-templates", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updateEmailTemplate(
  id: string,
  payload: EmailTemplateWrite,
): Promise<EmailTemplate> {
  return apiFetch<EmailTemplate>(`/api/email-templates/${id}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export async function deleteEmailTemplate(
  id: string,
): Promise<{ message: string }> {
  return apiFetch<{ message: string }>(`/api/email-templates/${id}`, {
    method: "DELETE",
  });
}

export async function recordEmailTemplateUse(
  id: string,
): Promise<{ usage_count: number }> {
  return apiFetch<{ usage_count: number }>(
    `/api/email-templates/${id}/use`,
    { method: "POST" },
  );
}

export async function listEmailTemplateFolders(): Promise<
  EmailTemplateFolderNode[]
> {
  return apiFetch<EmailTemplateFolderNode[]>("/api/email-template-folders");
}

export async function createEmailTemplateFolder(
  payload: EmailTemplateFolderWrite,
): Promise<EmailTemplateFolder> {
  return apiFetch<EmailTemplateFolder>("/api/email-template-folders", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updateEmailTemplateFolder(
  id: string,
  payload: EmailTemplateFolderWrite,
): Promise<EmailTemplateFolder> {
  return apiFetch<EmailTemplateFolder>(
    `/api/email-template-folders/${id}`,
    {
      method: "PUT",
      body: JSON.stringify(payload),
    },
  );
}

export async function deleteEmailTemplateFolder(
  id: string,
): Promise<{ message: string }> {
  return apiFetch<{ message: string }>(
    `/api/email-template-folders/${id}`,
    { method: "DELETE" },
  );
}

export async function getEmailTemplatesPicker(): Promise<EmailTemplatesPicker> {
  return apiFetch<EmailTemplatesPicker>("/api/emails/templates-picker");
}

export type ComposerSourceItem = {
  id: string;
  name: string;
  brand: string | null;
  blocks_count: number;
  open_url: string;
};

export type ComposerSourceResponse = {
  items: ComposerSourceItem[];
  error: string | null;
};

export async function getComposerSourceTemplates(): Promise<ComposerSourceResponse> {
  return apiFetch<ComposerSourceResponse>("/api/emails/composer-source");
}
