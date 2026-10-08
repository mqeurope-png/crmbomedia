import { apiFetch } from "./api";

/** Sprint Web-Forms PR-B — cliente de la API admin de formularios web. */

export type FieldType =
  | "text" | "email" | "tel" | "textarea" | "select" | "checkbox" | "hidden"
  | "tags" | "stars";

export const FIELD_TYPES: { value: FieldType; label: string }[] = [
  { value: "text", label: "Texto" },
  { value: "email", label: "Email" },
  { value: "tel", label: "Teléfono" },
  { value: "textarea", label: "Texto largo" },
  { value: "select", label: "Desplegable" },
  { value: "checkbox", label: "Checkbox (casilla)" },
  { value: "tags", label: "Tags del CRM (multi-select)" },
  { value: "stars", label: "Estrellas (widget 1-5)" },
  { value: "hidden", label: "Oculto (UTM)" },
];

/** Idiomas soportados en el selector. Añadir uno es 1 línea. */
export const FORM_LANGUAGES = [
  { value: "es", label: "ES" },
  { value: "en", label: "EN" },
  { value: "fr", label: "FR" },
  { value: "de", label: "DE" },
  { value: "pt", label: "PT" },
  { value: "nl", label: "NL" },
] as const;

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
  // select/checkbox: {value,label}; tags: {tag_id,label}.
  options: { value?: string; label: string; tag_id?: string }[];
  validation_pattern?: string | null;
  position: number;
  maps_to_contact_field?: string | null;
};

/** Apariencia por formulario (ancho, alineación, estilo). Cada opción vacía
 *  deja el aspecto de siempre; el servidor valida colores (#rrggbb) y rangos. */
export type FormAppearance = {
  width_pct?: number | null;
  max_width_px?: number | null;
  align?: "left" | "center" | "right" | null;
  theme?: "light" | "dark" | "inherit" | null;
  primary_color?: string | null;
  text_color?: string | null;
  background_color?: string | null;
  radius_px?: number | null;
  font?: "inherit" | "system" | "sans" | "serif" | "humanista" | null;
  font_size_px?: number | null;
  submit_text?: string | null;
};

export const APPEARANCE_ALIGN = [
  { value: "left", label: "Izquierda" },
  { value: "center", label: "Centrado" },
  { value: "right", label: "Derecha" },
] as const;

export const APPEARANCE_THEMES = [
  { value: "light", label: "Claro" },
  { value: "dark", label: "Oscuro" },
  { value: "inherit", label: "Heredar de la web" },
] as const;

export const APPEARANCE_FONTS = [
  { value: "inherit", label: "Heredada del sitio" },
  { value: "system", label: "Sistema" },
  { value: "sans", label: "Sans (Helvetica / Arial)" },
  { value: "serif", label: "Serif (Georgia)" },
  { value: "humanista", label: "Humanista (Segoe / Trebuchet)" },
] as const;

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
  /** Formulario de respaldo de su web en el embed por web. */
  is_site_default: boolean;
  appearance?: FormAppearance | null;
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
  /** Envíos reales y bloqueados (reCAPTCHA, honeypot…), por separado. */
  submissions_real?: number;
  submissions_blocked?: number;
  created_at: string;
};

export type FormSubmissionRow = {
  id: string;
  contact_id: string | null;
  is_spam: boolean;
  spam_reason: string | null;
  // v3 Bug 4: created | updated | spam | null (submits pre-migración).
  contact_action: "created" | "updated" | "spam" | null;
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

export type EmbedCode = {
  script_snippet: string;
  iframe_snippet: string;
  // v3 Bug 3: HTML puro copiable (sin estilar) para pegar en cualquier web.
  html_snippet: string;
  /** Un solo código por web (la clave del slug + idioma de la página). */
  site_snippet?: string | null;
  site?: string | null;
  site_web?: string | null;
  /** Desactivado no se ve en la web: la pantalla avisa al copiar. */
  is_active?: boolean;
};

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

/** HTML de la vista previa (vía iframe) con lo que hay en el editor, sin
 *  guardar. */
export async function previewForm(payload: {
  name: string;
  language: string;
  fields: FormField[];
  appearance?: FormAppearance | null;
}): Promise<{ html: string }> {
  return apiFetch<{ html: string }>("/api/admin/forms/preview", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export type MappableField = {
  value: string;
  label: string;
  type: string;
  group: string;
};

/** Campos del contacto a los que mapear cada campo del form (Bug 1). */
export async function getContactFieldsMappable(): Promise<{
  standard: MappableField[];
  custom: MappableField[];
}> {
  return apiFetch<{ standard: MappableField[]; custom: MappableField[] }>(
    "/api/admin/contact-fields-mappable",
  );
}

export type EmailTemplateItem = {
  id: string;
  name: string;
  subject: string | null;
};

/** Plantillas de email para el dropdown de confirmación (Bug 7). */
export type TagOption = { id: string; name: string; color: string | null };

export async function getTagsSelectable(search?: string): Promise<TagOption[]> {
  const q = search && search.trim() ? `?search=${encodeURIComponent(search.trim())}` : "";
  return apiFetch<TagOption[]>(`/api/admin/tags-selectable${q}`);
}

export async function listEmailTemplates(): Promise<EmailTemplateItem[]> {
  return apiFetch<EmailTemplateItem[]>("/api/email-templates");
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
    is_site_default: false,
    fields: [
      { ...blankField(0), field_key: "name", label: "Nombre", field_type: "text", maps_to_contact_field: "contact.first_name" },
      { ...blankField(1), field_key: "email", label: "Email", field_type: "email", is_required: true, maps_to_contact_field: "contact.email" },
    ],
  };
}
