"use client";

import {
  AlertTriangle,
  CheckCircle2,
  Database,
  Download,
  Loader2,
  RefreshCw,
  Trash2,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ErrorState } from "../../components/ErrorState";
import { PageHeader } from "../../components/PageHeader";
import { getCurrentUser } from "../../lib/api";
import {
  createBackup,
  deleteBackup,
  downloadBackup,
  listBackups,
  type Backup,
} from "../../lib/backupsApi";
import { extractErrorMessage } from "../../lib/errors";
import { formatBackendDateTime } from "../../lib/dates";

function formatBytes(bytes: number): string {
  if (bytes <= 0) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) {
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

function StatusBadge({ status }: { status: Backup["status"] }) {
  if (status === "success") {
    return (
      <span className="badge ok">
        <CheckCircle2 size={11} aria-hidden /> OK
      </span>
    );
  }
  if (status === "running") {
    return (
      <span className="badge warn">
        <Loader2 size={11} aria-hidden className="spin" /> En curso
      </span>
    );
  }
  return (
    <span className="badge danger">
      <AlertTriangle size={11} aria-hidden /> Falló
    </span>
  );
}

export default function AdminBackupsPage() {
  const [backups, setBackups] = useState<Backup[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [unauthorized, setUnauthorized] = useState(false);

  const load = useCallback(async () => {
    try {
      const rows = await listBackups();
      setBackups(rows);
      setError(null);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudieron cargar los backups."));
    } finally {
      setLoading(false);
    }
  }, []);

  // Permisos: la página solo tiene sentido para admin. Si no, lanzamos
  // un ErrorState con el mensaje estándar — coherente con
  // /admin/users.
  useEffect(() => {
    getCurrentUser()
      .then((u) => {
        if (u.role !== "admin") {
          setUnauthorized(true);
          setLoading(false);
          return;
        }
        void load();
      })
      .catch(() => {
        setUnauthorized(true);
        setLoading(false);
      });
  }, [load]);

  // Polling 10s mientras haya backups en estado `running`. Cuando no
  // los hay, paramos para no machacar la API.
  useEffect(() => {
    if (unauthorized) return;
    const anyRunning = backups.some((b) => b.status === "running");
    if (!anyRunning) return;
    const handle = window.setInterval(() => {
      void load();
    }, 10_000);
    return () => window.clearInterval(handle);
  }, [backups, load, unauthorized]);

  const latest = backups[0] ?? null;
  const driveOk = useMemo(
    () => backups.filter((b) => b.drive_url).length,
    [backups],
  );

  async function handleCreate() {
    if (creating) return;
    if (
      !window.confirm(
        "Crear un backup ahora. Tarda varios minutos y bloquea otros backups hasta terminar. ¿Continuar?",
      )
    ) {
      return;
    }
    setCreating(true);
    setMessage(null);
    setError(null);
    try {
      const res = await createBackup();
      setMessage(`Backup ${res.backup_id.slice(0, 8)} encolado.`);
      await load();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo encolar el backup."));
    } finally {
      setCreating(false);
    }
  }

  async function handleDownload(backup: Backup) {
    try {
      await downloadBackup(backup);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo descargar el backup."));
    }
  }

  async function handleDelete(backup: Backup) {
    if (
      !window.confirm(
        `Borrar el backup ${backup.filename}? El archivo en VPS se elimina; la copia de Google Drive permanece.`,
      )
    ) {
      return;
    }
    try {
      await deleteBackup(backup.id);
      setMessage(`Backup ${backup.filename} borrado.`);
      await load();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo borrar el backup."));
    }
  }

  if (unauthorized) {
    return (
      <main className="shell">
        <ErrorState
          title="No autorizado"
          message="Esta página requiere rol admin."
        />
      </main>
    );
  }

  return (
    <main className="shell shell-wide">
      <PageHeader
        title="Backups"
        eyebrow="Admin"
        description="Backups cifrados con GPG (AES-256) del MySQL completo + .env.production. Cron cada 72 h, retención de los últimos 3, push automático a Google Drive si rclone está configurado."
      />

      {error ? <ErrorState title="Error" message={error} /> : null}
      {message ? <p className="form-success">{message}</p> : null}

      <section className="card backup-status-banner">
        <div className="backup-status-banner-info">
          <Database size={22} aria-hidden />
          <div>
            <strong>
              {latest
                ? `Último backup: ${formatBackendDateTime(latest.started_at)}`
                : "Sin backups todavía"}
            </strong>
            {latest ? (
              <p className="muted small">
                <StatusBadge status={latest.status} />
                {" · "}
                {formatBytes(latest.size_bytes)} ·{" "}
                {latest.triggered_by === "cron" ? "automático" : "manual"} ·{" "}
                {latest.drive_url ? "subido a Drive" : "solo local"}
              </p>
            ) : (
              <p className="muted small">
                Configura el cron en VPS (ver{" "}
                <code>docs/backup-setup.md</code>) o crea uno manual.
              </p>
            )}
          </div>
        </div>
        <div className="backup-status-banner-actions">
          <button
            type="button"
            className="button secondary"
            onClick={() => {
              setLoading(true);
              void load();
            }}
            disabled={loading || creating}
            title="Refrescar"
          >
            <RefreshCw size={12} aria-hidden /> Refrescar
          </button>
          <button
            type="button"
            className="button"
            onClick={handleCreate}
            disabled={creating}
          >
            📦 Crear backup ahora
          </button>
        </div>
      </section>

      <section className="card">
        <header className="card-header">
          <h2>Histórico</h2>
          <p className="muted small">
            {backups.length} backups conocidos · {driveOk} en Drive
          </p>
        </header>
        {loading && backups.length === 0 ? (
          <p className="muted">Cargando…</p>
        ) : backups.length === 0 ? (
          <p className="muted">
            No hay backups todavía. Pulsa &quot;Crear backup ahora&quot; o
            espera al cron.
          </p>
        ) : (
          <div className="table-wrapper">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Fecha</th>
                  <th>Archivo</th>
                  <th>Tamaño</th>
                  <th>Status</th>
                  <th>Origen</th>
                  <th>Drive</th>
                  <th aria-label="Acciones" />
                </tr>
              </thead>
              <tbody>
                {backups.map((b) => (
                  <tr key={b.id}>
                    <td>{formatBackendDateTime(b.started_at)}</td>
                    <td>
                      <code className="small">{b.filename || "—"}</code>
                    </td>
                    <td>{formatBytes(b.size_bytes)}</td>
                    <td>
                      <StatusBadge status={b.status} />
                      {b.status === "failed" && b.error_summary ? (
                        <p
                          className="muted small"
                          title={b.error_summary}
                          style={{
                            maxWidth: 320,
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap",
                          }}
                        >
                          {b.error_summary}
                        </p>
                      ) : null}
                    </td>
                    <td className="muted small">
                      {b.triggered_by === "cron" ? "Cron" : "Manual"}
                    </td>
                    <td className="muted small">
                      {b.drive_url ? (
                        <a
                          href={b.drive_url}
                          target="_blank"
                          rel="noreferrer"
                          title="Abrir en Google Drive"
                        >
                          ✓
                        </a>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td>
                      <div style={{ display: "flex", gap: 6 }}>
                        <button
                          type="button"
                          className="icon-button"
                          title="Descargar"
                          onClick={() => handleDownload(b)}
                          disabled={b.status !== "success"}
                        >
                          <Download size={12} aria-hidden />
                        </button>
                        <button
                          type="button"
                          className="icon-button danger"
                          title="Borrar"
                          onClick={() => handleDelete(b)}
                        >
                          <Trash2 size={12} aria-hidden />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </main>
  );
}
