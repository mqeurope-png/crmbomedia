import { apiFetch, getStoredToken } from "./api";

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

// QoL sprint — export CSV de la selección. Devuelve un Blob para que
// el caller lo dispare como descarga sin que `apiFetch` intente
// parsear JSON. Mantenemos el flujo de errores del backend (JSON con
// detail) para 4xx leyéndolos antes de tocar el Blob.
export async function bulkExportContactsCsv(
  contactIds: string[],
): Promise<Blob> {
  const token = getStoredToken();
  const resp = await fetch("/api/contacts/bulk-export-csv", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({ contact_ids: contactIds }),
  });
  if (!resp.ok) {
    let detail = `Export falló (${resp.status})`;
    try {
      const body = await resp.json();
      if (body?.detail) detail = String(body.detail);
    } catch {
      /* keep generic detail */
    }
    throw new Error(detail);
  }
  return resp.blob();
}
