import { apiFetch } from "./api";

export type WorkflowStatus = "draft" | "active" | "paused" | "archived";
export type WorkflowRunState =
  | "running"
  | "waiting"
  | "waiting_for_event"
  | "completed"
  | "cancelled"
  | "failed"
  | "cancelling";
export type WorkflowExitKind = "natural" | "won" | "lost" | "timeout";

export type WorkflowStepRead = {
  id: string;
  type: string;
  config: Record<string, unknown>;
  position_x: number;
  position_y: number;
  is_entry: boolean;
  display_name?: string | null;
};

export type DuplicateWarning = {
  workflow_id: string;
  workflow_name: string;
  kind: "exact" | "similar";
  created_by_user_id: string | null;
  created_at: string;
};

export type WorkflowEdgeRead = {
  id: string;
  from_step_id: string;
  to_step_id: string;
  branch_label: string;
};

export type WorkflowRead = {
  id: string;
  name: string;
  description: string | null;
  status: WorkflowStatus;
  trigger_type: string;
  trigger_config: Record<string, unknown>;
  allow_reentry: boolean;
  cancellation_events: string[];
  total_entered: number;
  total_completed: number;
  total_won: number;
  total_cancelled: number;
  total_failed: number;
  /** PR-Backlog-Consolidado A6. Cuántos de los `total_completed`
   *  llegaron al final habiendo saltado >=1 step. */
  total_completed_with_skipped: number;
  created_by_user_id: string | null;
  /** PR-Frontend-Workflows-Pipelines-Templates. NULL = global del
   *  equipo (visible para todos). Si != NULL, solo el owner + admin
   *  lo ven. `is_mine` y `is_global` los computa el backend contra
   *  el current_user. Mantengo opcional para tolerar respuestas pre-#250
   *  durante el rolling deploy. */
  owner_user_id?: string | null;
  is_mine?: boolean;
  is_global?: boolean;
  created_at: string;
  updated_at: string;
};

export type WorkflowDetail = WorkflowRead & {
  steps: WorkflowStepRead[];
  edges: WorkflowEdgeRead[];
  duplicate_warnings: DuplicateWarning[];
  definition_hash?: string | null;
};

export type WorkflowTemplate = {
  id: string;
  name: string;
  description: string;
  trigger_type: string;
  steps_count: number;
};

export type WorkflowDryRunStepResult = {
  step_id: string;
  step_type: string;
  display_name?: string | null;
  label: string;
  description: string;
  branch_taken?: string | null;
  config_summary: Record<string, unknown>;
};

export type WorkflowDryRunResponse = {
  workflow_id: string;
  contact_id: string;
  contact_email?: string | null;
  steps: WorkflowDryRunStepResult[];
  truncated: boolean;
  error?: string | null;
};

export type WorkflowRunRead = {
  id: string;
  workflow_id: string;
  workflow_name: string | null;
  contact_id: string;
  state: WorkflowRunState;
  exit_kind: WorkflowExitKind | null;
  current_step_id: string | null;
  started_at: string;
  completed_at: string | null;
  wake_at: string | null;
  error_summary: string | null;
};

export type WorkflowRunHistoryRead = {
  id: string;
  step_id: string | null;
  step_type: string;
  status: string;
  result: Record<string, unknown> | null;
  error_summary: string | null;
  executed_at: string;
};

export type WorkflowRunDetail = WorkflowRunRead & {
  history: WorkflowRunHistoryRead[];
};

export type WorkflowCatalog = {
  triggers: { type: string; label: string }[];
  steps: { type: string; category: string; label: string }[];
  fields: string[];
  variables: string[];
};

export type WorkflowCostEstimate = {
  matching_contacts_now: number;
  estimated_runs_30d: number;
  estimated_emails_30d: number;
  estimated_tasks_30d: number;
  validation_errors: string[];
};

export type WorkflowStepWrite = {
  client_id: string;
  type: string;
  config: Record<string, unknown>;
  position_x: number;
  position_y: number;
  is_entry: boolean;
  display_name?: string | null;
};

export type WorkflowEdgeWrite = {
  from_client_id: string;
  to_client_id: string;
  branch_label: string;
};

export type WorkflowUpdate = {
  name?: string;
  description?: string | null;
  trigger_type?: string;
  trigger_config?: Record<string, unknown>;
  allow_reentry?: boolean;
  cancellation_events?: string[];
  steps?: WorkflowStepWrite[];
  edges?: WorkflowEdgeWrite[];
  /** PR-Frontend-Workflows-Pipelines-Templates. Solo admin puede
   *  flipearlo; el backend responde 403 a managers/users si lo
   *  intentan. */
  is_global?: boolean;
};

export async function listWorkflows(
  statusFilter?: WorkflowStatus,
): Promise<WorkflowRead[]> {
  const qs = statusFilter ? `?status=${statusFilter}` : "";
  return apiFetch<WorkflowRead[]>(`/api/workflows${qs}`);
}

export async function getWorkflow(id: string): Promise<WorkflowDetail> {
  return apiFetch<WorkflowDetail>(`/api/workflows/${id}`);
}

export async function createWorkflow(payload: {
  name: string;
  description?: string;
  trigger_type: string;
  trigger_config?: Record<string, unknown>;
  allow_reentry?: boolean;
  cancellation_events?: string[];
  /** PR-Frontend-Workflows-Pipelines-Templates. Solo admin: marca el
   *  workflow recién creado como global (owner_user_id=NULL). El
   *  backend ignora el campo si current_user no es admin. */
  is_global?: boolean;
}): Promise<WorkflowDetail> {
  return apiFetch<WorkflowDetail>("/api/workflows", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updateWorkflow(
  id: string,
  payload: WorkflowUpdate,
): Promise<WorkflowDetail> {
  return apiFetch<WorkflowDetail>(`/api/workflows/${id}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export async function activateWorkflow(
  id: string,
  acknowledgedEstimate: boolean,
): Promise<WorkflowDetail> {
  return apiFetch<WorkflowDetail>(`/api/workflows/${id}/activate`, {
    method: "POST",
    body: JSON.stringify({ acknowledged_estimate: acknowledgedEstimate }),
  });
}

export async function pauseWorkflow(id: string): Promise<WorkflowDetail> {
  return apiFetch<WorkflowDetail>(`/api/workflows/${id}/pause`, {
    method: "POST",
  });
}

export async function archiveWorkflow(id: string): Promise<WorkflowDetail> {
  return apiFetch<WorkflowDetail>(`/api/workflows/${id}/archive`, {
    method: "POST",
  });
}

export async function deleteWorkflow(id: string): Promise<void> {
  await apiFetch(`/api/workflows/${id}`, { method: "DELETE" });
}

export async function getWorkflowCatalog(): Promise<WorkflowCatalog> {
  return apiFetch<WorkflowCatalog>("/api/workflows/_catalog");
}

export async function getWorkflowCostEstimate(
  id: string,
): Promise<WorkflowCostEstimate> {
  return apiFetch<WorkflowCostEstimate>(`/api/workflows/${id}/cost-estimate`, {
    method: "POST",
  });
}

export async function listWorkflowRuns(
  workflowId: string,
  state?: WorkflowRunState,
): Promise<WorkflowRunRead[]> {
  const qs = state ? `?state=${state}` : "";
  return apiFetch<WorkflowRunRead[]>(`/api/workflows/${workflowId}/runs${qs}`);
}

export async function getWorkflowRunDetail(
  runId: string,
): Promise<WorkflowRunDetail> {
  return apiFetch<WorkflowRunDetail>(`/api/workflows/runs/${runId}`);
}

export async function cancelWorkflowRun(runId: string): Promise<void> {
  await apiFetch(`/api/workflows/runs/${runId}/cancel`, { method: "POST" });
}

export async function listContactWorkflowRuns(
  contactId: string,
): Promise<WorkflowRunRead[]> {
  return apiFetch<WorkflowRunRead[]>(
    `/api/workflows/_contacts/${contactId}/runs`,
  );
}

export async function addContactToWorkflow(
  workflowId: string,
  contactId: string,
): Promise<{ run_id: string }> {
  return apiFetch<{ run_id: string }>(
    `/api/workflows/${workflowId}/add-contact/${contactId}`,
    { method: "POST" },
  );
}

// --- Sprint UX-Workflows-Editor ---

export async function duplicateWorkflow(id: string): Promise<WorkflowDetail> {
  return apiFetch<WorkflowDetail>(`/api/workflows/${id}/duplicate`, {
    method: "POST",
  });
}

export async function dryRunWorkflow(
  id: string,
  contactId: string,
): Promise<WorkflowDryRunResponse> {
  return apiFetch<WorkflowDryRunResponse>(`/api/workflows/${id}/dry-run`, {
    method: "POST",
    body: JSON.stringify({ contact_id: contactId }),
  });
}

export async function listWorkflowTemplates(): Promise<WorkflowTemplate[]> {
  return apiFetch<WorkflowTemplate[]>("/api/workflows/_templates");
}

export async function createWorkflowFromTemplate(
  templateId: string,
): Promise<WorkflowDetail> {
  return apiFetch<WorkflowDetail>(
    `/api/workflows/_templates/${templateId}/use`,
    { method: "POST" },
  );
}
