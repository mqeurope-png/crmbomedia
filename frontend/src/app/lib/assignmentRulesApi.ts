/**
 * Sprint Reglas-Assign PR-E — assignment rules API helpers.
 *
 * Sirve a `/admin/assignment-rules`: lista, editor, preview de regla no
 * guardada, dry-run / run sobre regla existente.
 */
import type { RuleTree } from "./entitySchema";
import { apiFetch } from "./api";

export type AssignmentRuleApplyTo =
  | "new_only"
  | "unassigned_only"
  | "all_matching"
  | "all";

export interface AssignmentRule {
  id: string;
  name: string;
  description: string | null;
  is_active: boolean;
  priority: number;
  conditions: Record<string, unknown>;
  primary_user_id: string | null;
  secondary_user_ids: string[];
  apply_to: string;
  override_existing: boolean;
  stop_on_match: boolean;
  created_by_user_id: string;
  created_at: string;
  updated_at: string;
}

export interface AssignmentRuleWritePayload {
  name: string;
  description?: string | null;
  is_active?: boolean;
  priority: number;
  conditions: Record<string, unknown> | RuleTree;
  primary_user_id: string | null;
  secondary_user_ids: string[];
  apply_to: AssignmentRuleApplyTo;
  override_existing: boolean;
  stop_on_match: boolean;
}

export interface AssignmentRuleDryRunResult {
  rule_id: string;
  matched: number;
  applied: number;
  dry_run: boolean;
  auto_disabled: boolean;
  reason: string | null;
  error: string | null;
}

export async function listAssignmentRules(): Promise<AssignmentRule[]> {
  return apiFetch<AssignmentRule[]>("/api/assignment-rules");
}

export async function getAssignmentRule(id: string): Promise<AssignmentRule> {
  return apiFetch<AssignmentRule>(`/api/assignment-rules/${id}`);
}

export async function createAssignmentRule(
  payload: AssignmentRuleWritePayload,
): Promise<AssignmentRule> {
  return apiFetch<AssignmentRule>("/api/assignment-rules", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updateAssignmentRule(
  id: string,
  payload: AssignmentRuleWritePayload,
): Promise<AssignmentRule> {
  return apiFetch<AssignmentRule>(`/api/assignment-rules/${id}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export async function deleteAssignmentRule(id: string): Promise<void> {
  await apiFetch(`/api/assignment-rules/${id}`, { method: "DELETE" });
}

export async function dryRunAssignmentRule(
  id: string,
): Promise<AssignmentRuleDryRunResult> {
  return apiFetch<AssignmentRuleDryRunResult>(
    `/api/assignment-rules/${id}/dry-run`,
    { method: "POST" },
  );
}

export async function runAssignmentRule(
  id: string,
): Promise<AssignmentRuleDryRunResult> {
  return apiFetch<AssignmentRuleDryRunResult>(
    `/api/assignment-rules/${id}/run`,
    { method: "POST" },
  );
}

export async function previewAssignmentRule(
  payload: AssignmentRuleWritePayload,
): Promise<AssignmentRuleDryRunResult> {
  return apiFetch<AssignmentRuleDryRunResult>(
    "/api/assignment-rules/preview",
    {
      method: "POST",
      body: JSON.stringify(payload),
    },
  );
}
