import { apiFetch, getStoredToken } from "./api";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export type BackupStatus = "running" | "success" | "failed";
export type BackupTrigger = "cron" | "manual";

export type Backup = {
  id: string;
  filename: string;
  filepath: string;
  size_bytes: number;
  status: BackupStatus;
  drive_url: string | null;
  error_summary: string | null;
  triggered_by: BackupTrigger;
  started_at: string;
  finished_at: string | null;
  created_by_user_id: string | null;
};

export type BackupCreateResponse = {
  backup_id: string;
  job_id: string;
  status: BackupStatus;
};

export async function listBackups(): Promise<Backup[]> {
  return apiFetch<Backup[]>("/api/admin/backups");
}

export async function createBackup(): Promise<BackupCreateResponse> {
  return apiFetch<BackupCreateResponse>("/api/admin/backups/create", {
    method: "POST",
  });
}

export async function deleteBackup(id: string): Promise<void> {
  await apiFetch(`/api/admin/backups/${id}`, { method: "DELETE" });
}

/** Triggers a browser download. The download endpoint streams the
 *  encrypted `.tar.gz.gpg` with `Content-Disposition: attachment`, so
 *  we use `window.location` rather than fetch — that way the browser
 *  handles the save dialog + progress UI. The Bearer token rides via
 *  cookie (`bohub_token`) because the link doesn't carry custom
 *  headers; the backend auth path accepts both.
 *
 *  Fallback: si la cookie no está (cliente sin login persistente),
 *  hacemos fetch con header Authorization + blob() para forzar la
 *  descarga manualmente.
 */
export async function downloadBackup(backup: Backup): Promise<void> {
  const url = `${API_BASE_URL}/api/admin/backups/${backup.id}/download`;
  const token = getStoredToken();
  // Si tenemos token (siempre lo tenemos tras login) usamos fetch con
  // Authorization header — algunos navegadores no propagan la cookie
  // bohub_token a un navegate() puro y el endpoint exigía Bearer
  // hasta PR-F.
  if (token) {
    const response = await fetch(url, {
      method: "GET",
      credentials: "include",
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!response.ok) {
      const detail = await response.json().catch(() => null);
      throw new Error(
        (detail && (detail as { detail?: string }).detail) ||
          `No se pudo descargar el backup (${response.status}).`,
      );
    }
    const blob = await response.blob();
    const objUrl = window.URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = objUrl;
    link.download = backup.filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.URL.revokeObjectURL(objUrl);
    return;
  }
  // Sin token (caso límite): navegamos directo y dejamos al backend
  // resolver via cookie.
  window.location.href = url;
}
