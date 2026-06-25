// Sprint-Push-CRM-Brevo — cliente de los endpoints admin para el
// mapping owner ↔ lista Brevo + el backfill manual.
//
// Endpoints servidos por backend/app/api/brevo.py:
//
//   GET  /api/brevo/admin/user-list-mappings
//   PUT  /api/brevo/admin/user-list-mappings
//   POST /api/brevo/admin/backfill-push
//
// El picker de listas reusa `/api/brevo/lists?account_id=X` que ya
// existe (admin del modulo). Aquí solo modelamos las tipos del bloque
// de mappings + backfill.

import { apiFetch } from "./api";

export interface BrevoUserListMappingRow {
  user_id: string;
  user_full_name: string;
  user_email: string;
  user_is_active: boolean;
  brevo_list_id: number | null;
  brevo_list_name: string | null;
}

export interface BrevoUserListMappingsRead {
  rows: BrevoUserListMappingRow[];
}

export interface BrevoUserListMappingItem {
  user_id: string;
  brevo_list_id: number | null;
  brevo_list_name: string | null;
}

export interface BrevoBackfillPushResponse {
  // PR-Fix-Backfill-Brevo-Optimizado. La V2 del endpoint pre-filtra
  // contra el inventario de emails de Brevo y reporta los buckets
  // por separado, en vez de un único `queued_count`.
  total_with_owner: number;
  already_in_brevo_marked: number;
  queued_for_creation: number;
  queued_for_list_add_only: number;
  estimated_minutes: number;
  dry_run: boolean;
  cached_inventory: boolean;
  brevo_inventory_size: number;
}

export async function getBrevoUserListMappings(): Promise<BrevoUserListMappingsRead> {
  return apiFetch<BrevoUserListMappingsRead>(
    "/api/brevo/admin/user-list-mappings",
  );
}

export async function putBrevoUserListMappings(
  mappings: BrevoUserListMappingItem[],
): Promise<BrevoUserListMappingsRead> {
  return apiFetch<BrevoUserListMappingsRead>(
    "/api/brevo/admin/user-list-mappings",
    {
      method: "PUT",
      body: JSON.stringify({ mappings }),
    },
  );
}

export async function triggerBrevoBackfillPush(
  options: { dryRun?: boolean; refresh?: boolean } = {},
): Promise<BrevoBackfillPushResponse> {
  const params = new URLSearchParams();
  if (options.dryRun) params.set("dry_run", "true");
  if (options.refresh) params.set("refresh", "true");
  const qs = params.toString();
  const path = qs
    ? `/api/brevo/admin/backfill-push?${qs}`
    : "/api/brevo/admin/backfill-push";
  return apiFetch<BrevoBackfillPushResponse>(path, { method: "POST" });
}
