import { apiFetch } from "./api";

export type BulkAction =
  | "assign_owner"
  | "add_tag"
  | "remove_tag"
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
