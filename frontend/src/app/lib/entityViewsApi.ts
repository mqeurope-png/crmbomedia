import { apiFetch } from "./api";
import type { EntityKey } from "./entitySchema";

// Sprint Filtros & Listas (PR-B) — saved views per entity.
// Backed by `contact_views` with the new `entity_type` discriminator;
// the legacy `/api/contact-views` endpoint stays alive for the current
// contacts UI until PR-E migrates it.

export type EntityViewFilters = Record<string, unknown>;
export type EntityViewColumns = {
  visible?: string[];
  order?: string[];
  widths?: Record<string, number>;
};
export type EntityViewSort = {
  sort_by?: string;
  sort_dir?: "asc" | "desc";
};

export type EntityView = {
  id: string;
  entity_type: string;
  name: string;
  description: string | null;
  owner_user_id: string;
  is_owner: boolean;
  is_shared: boolean;
  is_default: boolean;
  // PR-Backlog-3-5-7 item 5. Default per-user — backend lo computa
  // join-eando con `user_default_view_prefs(current_user, entity)`.
  // El UI usa este flag para el indicador ★, NO `is_default` (que
  // es del owner). Opcional para tolerancia con APIs antiguas.
  is_default_for_me?: boolean;
  filters: EntityViewFilters;
  columns: EntityViewColumns;
  sort: EntityViewSort;
};

export type EntityViewWrite = {
  name: string;
  description?: string | null;
  is_shared?: boolean;
  is_default?: boolean;
  filters?: EntityViewFilters;
  columns?: EntityViewColumns;
  sort?: EntityViewSort;
};

export type EntityViewUpdate = Partial<EntityViewWrite>;

const base = (entity: EntityKey | string) =>
  `/api/entity-views/${entity}`;

export const listEntityViews = (entity: EntityKey | string) =>
  apiFetch<EntityView[]>(base(entity));

export const readEntityView = (
  entity: EntityKey | string,
  viewId: string,
) => apiFetch<EntityView>(`${base(entity)}/${viewId}`);

export const createEntityView = (
  entity: EntityKey | string,
  payload: EntityViewWrite,
) =>
  apiFetch<EntityView>(base(entity), {
    method: "POST",
    body: JSON.stringify(payload),
  });

export const updateEntityView = (
  entity: EntityKey | string,
  viewId: string,
  payload: EntityViewUpdate,
) =>
  apiFetch<EntityView>(`${base(entity)}/${viewId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });

export const deleteEntityView = async (
  entity: EntityKey | string,
  viewId: string,
) => {
  await apiFetch(`${base(entity)}/${viewId}`, { method: "DELETE" });
};

export const duplicateEntityView = (
  entity: EntityKey | string,
  viewId: string,
  payload: { name?: string } = {},
) =>
  apiFetch<EntityView>(`${base(entity)}/${viewId}/duplicate`, {
    method: "POST",
    body: JSON.stringify(payload),
  });

export const setDefaultEntityView = (
  entity: EntityKey | string,
  viewId: string,
) =>
  apiFetch<EntityView>(`${base(entity)}/${viewId}/set-default`, {
    method: "POST",
  });

// PR-Backlog-3-5-7 item 5. Limpiar la preferencia de default per-user
// para una entidad. Idempotente (200 también si no había default).
export const clearDefaultEntityView = (entity: EntityKey | string) =>
  apiFetch<{ message: string }>(
    `${base(entity)}/set-default/${entity}`,
    { method: "DELETE" },
  );
