import { apiFetch } from "./api";

// PR-E (Sprint Filtros & Listas): `add_tag` y `remove_tag` viven
// también en el backend pero nunca tuvieron botón en `<ContactsBulkBar>`
// — los tag bulk ops se hacen desde `/admin/tags` vía
// `POST /api/contacts/bulk-tag` (otro endpoint). Para no exponer
// flags muertos al frontend, el TS union solo lista las acciones
// realmente disparables desde la lista de contactos.
export type BulkAction =
  | "assign_owner"
  | "change_status"
  | "deactivate";

export type BulkResult = {
  action: BulkAction;
  affected_count: number;
  contact_ids: string[];
};

export async function bulkContactAction(
  contactIds: string[],
  action: BulkAction,
  payload: Record<string, unknown> = {},
): Promise<BulkResult> {
  return apiFetch<BulkResult>("/api/contacts/bulk-action", {
    method: "POST",
    body: JSON.stringify({
      contact_ids: contactIds,
      action,
      payload,
    }),
  });
}
