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
  queued_count: number;
  estimated_minutes: number;
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

export async function triggerBrevoBackfillPush(): Promise<BrevoBackfillPushResponse> {
  return apiFetch<BrevoBackfillPushResponse>(
    "/api/brevo/admin/backfill-push",
    { method: "POST" },
  );
}
