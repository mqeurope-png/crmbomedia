"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  defaultOperationFor,
  hasOperationsRegistered,
  listIntegrationSyncLogs,
  triggerIntegrationSync,
  type ExternalSystem,
  type SyncLogEntry,
  type SyncStatus,
} from "../lib/integrationSettings";
import { extractErrorMessage } from "../lib/errors";

const STATUS_LABEL: Record<SyncStatus, string> = {
  pending: "Pendiente",
  running: "En curso",
  success: "Completada",
  partial_success: "Parcial",
  failed: "Fallida",
};

function isActive(status: string | SyncStatus): boolean {
  return status === "pending" || status === "running";
}

function statusClass(status: string | SyncStatus): string {
  switch (status) {
    case "success":
      return "badge ok";
    case "partial_success":
      return "badge warn";
    case "failed":
      return "badge bad";
    case "running":
      return "badge active";
    default:
      return "badge muted";
  }
}

type SyncPanelProps = {
  system: ExternalSystem;
  accountId: string;
};

export function SyncPanel({ system, accountId }: SyncPanelProps) {
  const [logs, setLogs] = useState<SyncLogEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const supported = hasOperationsRegistered(system);
  const defaultOperation = defaultOperationFor(system);

  const refresh = useCallback(async () => {
    try {
      const rows = await listIntegrationSyncLogs(system, accountId, { limit: 10 });
      setLogs(rows);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudieron cargar las sincronizaciones"));
    }
  }, [system, accountId]);

  useEffect(() => {
    refresh();
    return () => {
      if (refreshTimer.current) clearTimeout(refreshTimer.current);
    };
  }, [refresh]);

  // Auto-refresh while any row is pending/running. Single-shot setTimeout
  // chains avoid drifting intervals when the page is backgrounded.
  useEffect(() => {
    if (logs.some((row) => isActive(row.status))) {
      if (refreshTimer.current) clearTimeout(refreshTimer.current);
      refreshTimer.current = setTimeout(() => {
        refresh();
      }, 5000);
    }
    return () => {
      if (refreshTimer.current) {
        clearTimeout(refreshTimer.current);
        refreshTimer.current = null;
      }
    };
  }, [logs, refresh]);

  async function onTrigger() {
    if (!defaultOperation) return;
    setError(null);
    setMessage(null);
    setSubmitting(true);
    try {
      const result = await triggerIntegrationSync(system, accountId, defaultOperation);
      setMessage(
        `Sincronización ${result.operation} encolada (sync_log ${result.sync_log_id.slice(0, 8)}…)`,
      );
      await refresh();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo encolar la sincronización"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="form-card embedded sync-panel">
      <h3>Sincronización</h3>
      <div className="actions">
        <button
          className="button"
          type="button"
          disabled={!supported || submitting}
          title={
            !supported
              ? "Conector no implementado todavía"
              : `Ejecuta '${defaultOperation}'`
          }
          onClick={onTrigger}
        >
          {submitting ? "Encolando…" : "Sincronizar ahora"}
        </button>
        <Link
          className="button secondary small"
          href={`/admin/integrations/${system}/${accountId}/sync-history`}
        >
          Ver historial completo
        </Link>
      </div>
      {error ? <p className="modal-error">{error}</p> : null}
      {message ? <p className="muted">{message}</p> : null}

      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Fecha</th>
              <th>Operación</th>
              <th>Estado</th>
              <th>Procesados</th>
              <th>Fallidos</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {logs.length === 0 ? (
              <tr>
                <td colSpan={6}>
                  <span className="muted">
                    Sin sincronizaciones registradas todavía.
                  </span>
                </td>
              </tr>
            ) : null}
            {logs.map((row) => (
              <tr key={row.id}>
                <td>{new Date(row.created_at).toLocaleString()}</td>
                <td>
                  <code>{row.operation ?? "—"}</code>
                </td>
                <td>
                  <span className={statusClass(row.status)}>
                    {STATUS_LABEL[row.status as SyncStatus] ?? row.status}
                  </span>
                </td>
                <td>{row.records_processed}</td>
                <td>{row.records_failed}</td>
                <td>
                  <Link
                    href={`/admin/integrations/${system}/${accountId}/sync-history?focus=${row.id}`}
                  >
                    Detalle
                  </Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
