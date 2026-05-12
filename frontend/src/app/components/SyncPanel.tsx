"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ConfirmDialog } from "./ConfirmDialog";
import {
  defaultOperationFor,
  hasOperation,
  hasOperationsRegistered,
  listIntegrationSyncLogs,
  triggerIntegrationSync,
  type ExternalSystem,
  type IntegrationAccount,
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

function relativeTime(value: string | null): string {
  if (!value) return "";
  try {
    const dt = new Date(value);
    const diffMs = Date.now() - dt.getTime();
    if (diffMs < 60_000) return "hace unos segundos";
    const minutes = Math.floor(diffMs / 60_000);
    if (minutes < 60) return `hace ${minutes} min`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `hace ${hours} h`;
    const days = Math.floor(hours / 24);
    return `hace ${days} d`;
  } catch {
    return value;
  }
}

type SyncPanelProps = {
  system: ExternalSystem;
  accountId: string;
  /** Full account row from `listIntegrationAccounts`. Required for the
   * "Purgar cuota ahora" button to show only on quota-aware accounts. */
  account?: IntegrationAccount | null;
};

export function SyncPanel({ system, accountId, account }: SyncPanelProps) {
  const [logs, setLogs] = useState<SyncLogEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [purgeConfirm, setPurgeConfirm] = useState(false);
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const supported = hasOperationsRegistered(system);
  const defaultOperation = defaultOperationFor(system);
  const supportsPurge =
    hasOperation(system, "purge_quota") &&
    !!account &&
    account.quota_max_contacts != null &&
    account.quota_max_contacts > 0;

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

  const lastCompleted = useMemo(
    () =>
      logs.find(
        (row) =>
          row.status === "success" ||
          row.status === "partial_success" ||
          row.status === "failed",
      ) ?? null,
    [logs],
  );

  async function triggerOperation(operation: string) {
    setError(null);
    setMessage(null);
    setSubmitting(true);
    try {
      const result = await triggerIntegrationSync(system, accountId, operation);
      setMessage(
        `${operation} encolada (sync_log ${result.sync_log_id.slice(0, 8)}…)`,
      );
      await refresh();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo encolar la sincronización"));
    } finally {
      setSubmitting(false);
    }
  }

  async function onTriggerDefault() {
    if (!defaultOperation) return;
    await triggerOperation(defaultOperation);
  }

  async function onConfirmPurge() {
    setPurgeConfirm(false);
    await triggerOperation("purge_quota");
  }

  return (
    <div className="form-card embedded sync-panel">
      <h3>Sincronización</h3>
      {lastCompleted ? (
        <p className="muted">
          Última sincronización: {relativeTime(lastCompleted.finished_at ?? lastCompleted.created_at)}
          {" · "}
          {lastCompleted.records_processed} contactos procesados
          {lastCompleted.records_failed > 0
            ? `, ${lastCompleted.records_failed} errores`
            : null}
        </p>
      ) : null}
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
          onClick={onTriggerDefault}
        >
          {submitting ? "Encolando…" : "Sincronizar ahora"}
        </button>
        {supportsPurge ? (
          <button
            className="button secondary"
            type="button"
            disabled={submitting}
            onClick={() => setPurgeConfirm(true)}
          >
            Purgar cuota ahora
          </button>
        ) : null}
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

      <ConfirmDialog
        open={purgeConfirm}
        title="Purgar cuota en AgileCRM"
        message={
          account?.quota_max_contacts
            ? `Eliminará contactos del lado de AgileCRM hasta dejar como máximo ${account.quota_max_contacts} (estrategia ${account.quota_strategy ?? "none"}). No borra contactos del CRM local. Continuar?`
            : "Eliminará contactos del lado del proveedor hasta cumplir la cuota configurada. No borra contactos del CRM local. Continuar?"
        }
        confirmLabel="Purgar"
        onConfirm={onConfirmPurge}
        onCancel={() => setPurgeConfirm(false)}
      />
    </div>
  );
}
