"use client";

/** Typed wrapper around the `/api/composer/*` backend.
 *
 * Mirrors `backend/app/composer/schemas.py`. Field names use snake_case
 * to match the Python source — adding a camelCase layer would force
 * every consumer to translate twice without any real ergonomic win.
 */

import { apiFetch } from "../../lib/api";

export type ComposerBrand = {
  id: string;
  type: string;
  label: string;
  logo: string | null;
  logo_text: string | null;
  color: string;
  divider: string | null;
  logo_height: string | null;
  logo_max_width: string | null;
  visible: boolean;
  sort_order: number;
  i18n: Record<string, unknown>;
  created_at: string;
  updated_at: string;
};

export type ComposerProduct = {
  id: string;
  brand_id: string;
  name: string;
  badge: string | null;
  badge_bg: string | null;
  badge_color: string | null;
  img: string;
  description: string | null;
  area: string | null;
  alt: string | null;
  feat1: string | null;
  feat2: string | null;
  price: string | null;
  link: string | null;
  accent: string | null;
  gradient: string | null;
  visible: boolean;
  sort_order: number;
  tags: string[];
  i18n: Record<string, unknown>;
  created_at: string;
  updated_at: string;
};

export type ComposerPrewrittenText = {
  id: string;
  name: string;
  icon: string | null;
  brand_id: string | null;
  text: string;
  visible: boolean;
  sort_order: number;
  i18n: Record<string, unknown>;
};

export type ComposerComposedBlock = {
  id: string;
  title: string;
  description: string | null;
  price_range: string | null;
  color_tag: string | null;
  intro_text: string | null;
  brand_strip: string | null;
  block_type: string;
  products: string[];
  include_hero: boolean;
  include_steps: boolean;
  visible: boolean;
  sort_order: number;
  i18n: Record<string, unknown>;
  config: Record<string, unknown>;
};

export type ComposerStandaloneBlock = {
  id: string;
  title: string;
  description: string | null;
  icon: string | null;
  icon_bg: string | null;
  brand_id: string | null;
  section: string | null;
  block_type: string;
  config: Record<string, unknown>;
  visible: boolean;
  sort_order: number;
  i18n: Record<string, unknown>;
};

export type ComposerCatalog = {
  brands: ComposerBrand[];
  products: ComposerProduct[];
  prewritten_texts: ComposerPrewrittenText[];
  composed_blocks: ComposerComposedBlock[];
  standalone_blocks: ComposerStandaloneBlock[];
};

export type ComposerTemplate = {
  id: string;
  name: string;
  description: string | null;
  color_class: string | null;
  brand_id: string | null;
  blocks: string[];
  compositor_blocks: unknown[] | null;
  visible: boolean;
  is_global: boolean;
  owner_user_id: string | null;
  created_at: string;
  updated_at: string;
};

export type ComposerTemplateWrite = {
  name: string;
  description?: string | null;
  color_class?: string | null;
  brand_id?: string | null;
  blocks?: string[];
  compositor_blocks?: unknown[] | null;
  is_global?: boolean;
};

export type ComposerTemplateRevision = {
  id: string;
  template_id: string;
  snapshot: Record<string, unknown>;
  created_by_user_id: string | null;
  created_at: string;
};

export type ComposerDraft = {
  state: Record<string, unknown>;
  updated_at: string | null;
};

export type ComposerAsset = {
  id: string;
  user_id: string | null;
  filename: string;
  mime_type: string;
  size_bytes: number;
  sha256: string;
  public_url: string;
  source: string;
  created_at: string;
};

export type ComposerSettings = {
  openai_configured: boolean;
  ai_styles: Record<string, unknown>;
  agent_system_prompt: string | null;
  updated_at: string | null;
};

export type ComposerSettingsWrite = {
  openai_api_key?: string | null;
  ai_styles?: Record<string, unknown> | null;
  agent_system_prompt?: string | null;
};

export function getCatalog(): Promise<ComposerCatalog> {
  return apiFetch<ComposerCatalog>("/api/composer/catalog");
}

export function listTemplates(): Promise<ComposerTemplate[]> {
  return apiFetch<ComposerTemplate[]>("/api/composer/templates");
}

export function getTemplate(id: string): Promise<ComposerTemplate> {
  return apiFetch<ComposerTemplate>(
    `/api/composer/templates/${encodeURIComponent(id)}`,
  );
}

export function createTemplate(
  body: ComposerTemplateWrite,
): Promise<ComposerTemplate> {
  return apiFetch<ComposerTemplate>("/api/composer/templates", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function updateTemplate(
  id: string,
  body: ComposerTemplateWrite,
): Promise<ComposerTemplate> {
  return apiFetch<ComposerTemplate>(
    `/api/composer/templates/${encodeURIComponent(id)}`,
    {
      method: "PUT",
      body: JSON.stringify(body),
    },
  );
}

export function deleteTemplate(id: string): Promise<unknown> {
  return apiFetch<unknown>(
    `/api/composer/templates/${encodeURIComponent(id)}`,
    { method: "DELETE" },
  );
}

export function listTemplateRevisions(
  id: string,
): Promise<ComposerTemplateRevision[]> {
  return apiFetch<ComposerTemplateRevision[]>(
    `/api/composer/templates/${encodeURIComponent(id)}/revisions`,
  );
}

export function getDraft(): Promise<ComposerDraft> {
  return apiFetch<ComposerDraft>("/api/composer/drafts");
}

export function saveDraft(state: Record<string, unknown>): Promise<ComposerDraft> {
  return apiFetch<ComposerDraft>("/api/composer/drafts", {
    method: "PUT",
    body: JSON.stringify({ state }),
  });
}

export function listAssets(): Promise<ComposerAsset[]> {
  return apiFetch<ComposerAsset[]>("/api/composer/assets");
}

export function deleteAsset(id: string): Promise<unknown> {
  return apiFetch<unknown>(`/api/composer/assets/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

/** Multipart upload bypasses the JSON-only `apiFetch`. */
export async function uploadAsset(file: File): Promise<ComposerAsset> {
  const API_BASE_URL =
    process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
  const token =
    typeof window === "undefined"
      ? null
      : window.localStorage.getItem("crmbomedia_access_token");
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${API_BASE_URL}/api/composer/assets`, {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
    body: form,
  });
  if (!res.ok) {
    const detail = await res
      .json()
      .then((b) => (b as { detail?: string }).detail)
      .catch(() => undefined);
    throw new Error(detail || `Error subiendo el archivo (${res.status}).`);
  }
  return (await res.json()) as ComposerAsset;
}

export function getSettings(): Promise<ComposerSettings> {
  return apiFetch<ComposerSettings>("/api/composer/settings");
}

export function updateSettings(
  body: ComposerSettingsWrite,
): Promise<ComposerSettings> {
  return apiFetch<ComposerSettings>("/api/composer/settings", {
    method: "PUT",
    body: JSON.stringify(body),
  });
}
