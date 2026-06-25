// Sprint-Backfill-Gmail — cliente tipado de los endpoints admin.
//
// Backend: backend/app/api/gmail_backfill.py
//
//   POST /api/admin/gmail/backfill/estimate
//   POST /api/admin/gmail/backfill/execute
//   GET  /api/admin/gmail/backfill/{job_id}
//   POST /api/admin/gmail/backfill/{job_id}/cancel
//   GET  /api/email-messages/{id}/attachments/{id}/download  (servido por
//       el componente de attachment download, no por este módulo)

import { apiFetch } from "./api";

export type GmailBackfillStatus =
  | "queued"
  | "running"
  | "cancelling"
  | "completed"
  | "failed"
  | "cancelled";

export type GmailBackfillMode = "estimate" | "execute";

export interface GmailBackfillPerUserBreakdown {
  user_id: string;
  email: string;
  emails: number;
  attachments_count: number;
  attachments_mb: number;
  needs_reconnect: boolean;
}

export interface GmailBackfillEstimateResult {
  total_emails: number;
  total_attachments_count: number;
  total_attachments_size_mb: number;
  estimated_storage_gb: number;
  estimated_duration_minutes: number;
  per_user_breakdown: GmailBackfillPerUserBreakdown[];
  months_back: number;
}

export interface GmailBackfillJobRead {
  id: string;
  mode: GmailBackfillMode;
  status: GmailBackfillStatus;
  initiated_by_user_id: string | null;
  total_estimated: number | null;
  total_processed: number;
  total_imported: number;
  total_skipped: number;
  total_errors: number;
  started_at: string | null;
  finished_at: string | null;
  error_summary: string | null;
  config: Record<string, unknown> | null;
  result: GmailBackfillEstimateResult | Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}

export async function triggerGmailBackfillEstimate(
  monthsBack = 36,
): Promise<GmailBackfillJobRead> {
  return apiFetch<GmailBackfillJobRead>(
    "/api/admin/gmail/backfill/estimate",
    {
      method: "POST",
      body: JSON.stringify({ months_back: monthsBack }),
    },
  );
}

export async function triggerGmailBackfillExecute(opts: {
  monthsBack?: number;
  includeAttachments?: boolean;
  maxAttachmentSizeMb?: number;
}): Promise<GmailBackfillJobRead> {
  return apiFetch<GmailBackfillJobRead>(
    "/api/admin/gmail/backfill/execute",
    {
      method: "POST",
      body: JSON.stringify({
        months_back: opts.monthsBack ?? 36,
        include_attachments: opts.includeAttachments ?? true,
        max_attachment_size_mb: opts.maxAttachmentSizeMb ?? 25,
      }),
    },
  );
}

export async function getGmailBackfillStatus(
  jobId: string,
): Promise<GmailBackfillJobRead> {
  return apiFetch<GmailBackfillJobRead>(
    `/api/admin/gmail/backfill/${encodeURIComponent(jobId)}`,
  );
}

export async function cancelGmailBackfill(
  jobId: string,
): Promise<GmailBackfillJobRead> {
  return apiFetch<GmailBackfillJobRead>(
    `/api/admin/gmail/backfill/${encodeURIComponent(jobId)}/cancel`,
    { method: "POST" },
  );
}

export async function forceFailGmailBackfill(
  jobId: string,
): Promise<GmailBackfillJobRead> {
  // PR-Fix-Backfill-Gmail-Arquitectura. Para jobs colgados que no
  // responden a cancel — marca el row failed sin esperar al worker.
  return apiFetch<GmailBackfillJobRead>(
    `/api/admin/gmail/backfill/${encodeURIComponent(jobId)}/force-fail`,
    { method: "POST" },
  );
}
