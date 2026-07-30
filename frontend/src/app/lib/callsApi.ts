import { apiFetch } from "./api";

/** Sprint Ficha 360 — llamadas, timeline y workflows manuales. */

export type CallResultCode =
  | "contacted" | "no_answer" | "voicemail" | "call_back"
  | "interested" | "not_interested" | "info_requested" | "other";

export const CALL_RESULTS: { code: CallResultCode; label: string; icon: string }[] = [
  { code: "contacted", label: "Contactado", icon: "📞" },
  { code: "no_answer", label: "No contesta", icon: "📵" },
  { code: "voicemail", label: "Buzón de voz", icon: "📬" },
  { code: "call_back", label: "Volver a llamar", icon: "🔁" },
  { code: "interested", label: "Interesado", icon: "👍" },
  { code: "not_interested", label: "No interesado", icon: "👎" },
  { code: "info_requested", label: "Pidió información", icon: "📄" },
  { code: "other", label: "Otro…", icon: "✏️" },
];

export const DURATION_BUCKETS = [
  { code: "lt_1min", label: "<1 min" },
  { code: "1_to_5min", label: "1-5 min" },
  { code: "5_to_30min", label: "5-30 min" },
  { code: "gt_30min", label: ">30 min" },
] as const;

export type CallLogPayload = {
  result_code: CallResultCode;
  result_custom?: string | null;
  subject?: string | null;
  notes?: string | null;
  duration_bucket?: string | null;
  called_at?: string | null;
  actions?: {
    pipeline_change?: { pipeline_id: string; stage_id?: string | null } | null;
    lead_score_delta?: number | null;
    follow_up_task?: {
      title: string;
      due_at?: string | null;
      assigned_to_user_id?: string | null;
    } | null;
    trigger_workflow_ids?: string[];
  };
};

export type CallLogRead = {
  id: string;
  result_code: string;
  result_custom: string | null;
  subject: string | null;
  notes: string | null;
  duration_bucket: string | null;
  called_at: string;
  follow_up_task_id: string | null;
  actions_executed: string[];
};

export const createCallLog = (contactId: string, payload: CallLogPayload) =>
  apiFetch<CallLogRead>(`/api/contacts/${contactId}/calls`, {
    method: "POST",
    body: JSON.stringify(payload),
  });

export type TimelineEvent = {
  id: string;
  type: string;
  occurred_at: string;
  actor: { user_id: string; name: string } | null;
  icon: string;
  title: string;
  detail: string | null;
  metadata: Record<string, unknown>;
};

export type TimelinePage = {
  items: TimelineEvent[];
  total: number;
  limit: number;
  offset: number;
};

export const getContactTimeline = (
  contactId: string,
  opts: { limit?: number; offset?: number; types?: string[]; sort?: "asc" | "desc" } = {},
) => {
  const params = new URLSearchParams();
  params.set("limit", String(opts.limit ?? 100));
  params.set("offset", String(opts.offset ?? 0));
  if (opts.types?.length) params.set("types", opts.types.join(","));
  params.set("sort", opts.sort ?? "desc");
  return apiFetch<TimelinePage>(
    `/api/contacts/${contactId}/timeline?${params.toString()}`,
  );
};

export type ManualWorkflow = {
  id: string;
  name: string;
  description: string | null;
  owner_name: string | null;
  last_run_at: string | null;
};

export const listManualWorkflows = () =>
  apiFetch<ManualWorkflow[]>("/api/workflows/manual");

export const runManualWorkflow = (contactId: string, workflowId: string) =>
  apiFetch<{ run_id: string }>(
    `/api/contacts/${contactId}/workflows/${workflowId}/run`,
    { method: "POST" },
  );
