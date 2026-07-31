import { apiFetch } from "./api";

/** Sprint Web-Forms PR-B — cliente de la API admin de formularios web. */

export type FieldType =
  | "text" | "email" | "tel" | "textarea" | "select" | "checkbox" | "hidden";

export const FIELD_TYPES: { value: FieldType; label: string }[] = [
  { value: "text", label: "Texto" },
  { value: "email", label: "Email" },
  { value: "tel", label: "Teléfono" },
  { value: "textarea", label: "Texto largo" },
  { value: "select", label: "Desplegable" },
  { value: "checkbox", label: "Checkbox (casilla)" },
  { value: "hidden", label: "Oculto (UTM)" },
];

export const ASSIGNMENT_MODES = [
  { value: "rules", label: "Reglas de asignación del CRM" },
  { value: "fixed_owner", label: "Propietario fijo" },
  { value: "none", label: "Sin asignar" },
] as const;

export type FormField = {
  id?: string;
  field_key: string;
  label: string;
  field_type: FieldType;
  placeholder?: string | null;
  help_text?: string | null;
  is_required: boolean;
  is_hidden: boolean;
  default_value?: string | null;
  options: { value: string; label: string }[];
  validation_pattern?: string | null;
  position: number;
  maps_to_contact_field?: string | null;
};

export type WebFormBase = {
  slug: string;
  name: string;
  brand?: string | null;
  language: string;
  is_active: boolean;
  submit_success_mode: "modal" | "redirect";
  submit_success_message?: string | null;
  submit_redirect_url?: string | null;
  send_confirmation_email: boolean;
  confirmation_email_template_id?: string | null;
  assignment_mode: "rules" | "fixed_owner" | "none";
  fixed_owner_user_id?: string | null;
  notify_owner_on_new: boolean;
  recaptcha_enabled: boolean;
};

export type WebFormDetail = WebFormBase & {
  id: string;
  created_by_user_id: string;
  created_at: string;
  fields: FormField[];
};

export type WebFormListItem = {
  id: string;
  slug: string;
  name: string;
  brand: string | null;
  language: string;
  is_active: boolean;
  submissions_total: number;
  submissions_spam: number;
  created_at: string;
};

export type FormSubmissionRow = {
  id: string;
  contact_id: string | null;
  is_spam: boolean;
  spam_reason: string | null;
  recaptcha_score: number | null;
  ip_address: string | null;
  utm_source: string | null;
  utm_medium: string | null;
  utm_campaign: string | null;
  referrer: string | null;
  landing_page: string | null;
  payload: Record<string, unknown>;
  created_at: string;
};

export type EmbedCode = { script_snippet: string; iframe_snippet: string };

function qs(params: Record<string, string | boolean | undefined>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== "") sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

export async function listForms(filters: {
  brand?: string;
  language?: string;
  is_active?: boolean;
} = {}): Promise<WebFormListItem[]> {
  return apiFetch<WebFormListItem[]>(`/api/admin/forms${qs(filters)}`);
}

export async function getForm(id: string): Promise<WebFormDetail> {
  return apiFetch<WebFormDetail>(`/api/admin/forms/${id}`);
}

export async function createForm(
  payload: WebFormBase & { fields: FormField[] },
): Promise<WebFormDetail> {
  return apiFetch<WebFormDetail>("/api/admin/forms", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updateForm(
  id: string,
  payload: WebFormBase & { fields: FormField[] },
): Promise<WebFormDetail> {
  return apiFetch<WebFormDetail>(`/api/admin/forms/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function deleteForm(id: string): Promise<{ message: string }> {
  return apiFetch<{ message: string }>(`/api/admin/forms/${id}`, {
    method: "DELETE",
  });
}

export async function getSubmissions(
  id: string,
  filters: { is_spam?: boolean; limit?: number } = {},
): Promise<{ items: FormSubmissionRow[] }> {
  const params: Record<string, string | boolean | undefined> = {};
  if (filters.is_spam !== undefined) params.is_spam = filters.is_spam;
  if (filters.limit) params.limit = String(filters.limit);
  return apiFetch<{ items: FormSubmissionRow[] }>(
    `/api/admin/forms/${id}/submissions${qs(params)}`,
  );
}

export async function getEmbedCode(id: string): Promise<EmbedCode> {
  return apiFetch<EmbedCode>(`/api/admin/forms/${id}/embed-code`);
}

/** Nuevo campo con valores por defecto. */
export function blankField(position: number): FormField {
  return {
    field_key: "",
    label: "",
    field_type: "text",
    is_required: false,
    is_hidden: false,
    options: [],
    position,
  };
}

/** Form vacío para el editor "crear". */
export function blankForm(): WebFormBase & { fields: FormField[] } {
  return {
    slug: "",
    name: "",
    brand: "",
    language: "es",
    is_active: true,
    submit_success_mode: "modal",
    submit_success_message: "¡Gracias! Hemos recibido tu solicitud.",
    submit_redirect_url: "",
    send_confirmation_email: false,
    assignment_mode: "rules",
    notify_owner_on_new: true,
    recaptcha_enabled: true,
    fields: [
      { ...blankField(0), field_key: "name", label: "Nombre", field_type: "text", maps_to_contact_field: "contact.first_name" },
      { ...blankField(1), field_key: "email", label: "Email", field_type: "email", is_required: true, maps_to_contact_field: "contact.email" },
    ],
  };
}
