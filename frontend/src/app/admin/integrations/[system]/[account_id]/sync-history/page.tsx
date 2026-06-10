"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams, useSearchParams } from "next/navigation";
import { PageHeader } from "../../../../../components/PageHeader";
import { ErrorState } from "../../../../../components/ErrorState";
import { Modal } from "../../../../../components/Modal";
import {
  listIntegrationSyncLogs,
  type ExternalSystem,
  type SyncLogEntry,
  type SyncStatus,
} from "../../../../../lib/integrationSettings";
import { extractErrorMessage } from "../../../../../lib/errors";

const PAGE_SIZE = 50;

const STATUSES: SyncStatus[] = [
  "pending",
  "running",
  "success",
  "partial_success",
  "failed",
];

const STATUS_LABEL: Record<SyncStatus, string> = {
  pending: "Pendiente",
  running: "En curso",
  success: "Completada",
  partial_success: "Parcial",
  failed: "Fallida",
};

export default function SyncHistoryPage() {
  const params = useParams<{ system: ExternalSystem; account_id: string }>();
  const searchParams = useSearchParams();
  const focusId = searchParams?.get("focus") ?? null;

  const [logs, setLogs] = useState<SyncLogEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(0);
  const [statusFilter, setStatusFilter] = useState<SyncStatus | "">("");
  const [operationFilter, setOperationFilter] = useState("");
  const [fromFilter, setFromFilter] = useState("");
  const [toFilter, setToFilter] = useState("");
  const [detail, setDetail] = useState<SyncLogEntry | null>(null);

  const system = params.system;
  const accountId = params.account_id;

  const refresh = useCallback(async () => {
    setError(null);
    try {
      const rows = await listIntegrationSyncLogs(system, accountId, {
        skip: page * PAGE_SIZE,
        limit: PAGE_SIZE,
        status: statusFilter || undefined,
        operation: operationFilter || undefined,
        from: fromFilter || undefined,
        to: toFilter || undefined,
      });
      setLogs(rows);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo cargar el historial"));
    }
  }, [system, accountId, page, statusFilter, operationFilter, fromFilter, toFilter]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    if (focusId) {
      const match = logs.find((row) => row.id === focusId);
      if (match) setDetail(match);
    }
  }, [focusId, logs]);

  const headerTitle = useMemo(
    () => `Historial de sincronizaciones · ${system} / ${accountId}`,
    [system, accountId],
  );

  return (
    <main className="shell">
      <PageHeader
        title={headerTitle}
        eyebrow="Administración"
        crumbs={[
          { label: "Integraciones", href: "/admin/integrations" },
          { label: "Histórico" },
        ]}
      />

      {error ? <ErrorState title="Error" message={error} /> : null}

      <section className="card">
        <form
          className="audit-filters"
          onSubmit={(event) => {
            event.preventDefault();
            setPage(0);
            refresh();
          }}
        >
          <label>
            Estado
            <select
              value={statusFilter}
              onChange={(event) =>
                setStatusFilter(event.target.value as SyncStatus | "")
              }
            >
              <option value="">Todos</option>
              {STATUSES.map((status) => (
                <option key={status} value={status}>
                  {STATUS_LABEL[status]}
                </option>
              ))}
            </select>
          </label>
          <label>
            Operación
            <input
              value={operationFilter}
              onChange={(event) => setOperationFilter(event.target.value)}
              placeholder="sync_contacts"
            />
          </label>
          <label>
            Desde
            <input
              type="datetime-local"
              value={fromFilter}
              onChange={(event) => setFromFilter(event.target.value)}
            />
          </label>
          <label>
            Hasta
            <input
              type="datetime-local"
              value={toFilter}
              onChange={(event) => setToFilter(event.target.value)}
            />
          </label>
          <div className="actions">
            <button className="button" type="submit">
              Aplicar filtros
            </button>
            <button
              className="button secondary"
              type="button"
              onClick={() => {
                setStatusFilter("");
                setOperationFilter("");
                setFromFilter("");
                setToFilter("");
                setPage(0);
              }}
            >
              Reset
            </button>
          </div>
        </form>
      </section>

      <section className="card">
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Fecha</th>
                <th>Operación</th>
                <th>Estado</th>
                <th>Procesados</th>
                <th>Saltados</th>
                <th>Fallidos</th>
                <th>Trigger</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {logs.length === 0 ? (
                <tr>
                  <td colSpan={8}>
                    <span className="muted">Sin sincronizaciones para los filtros actuales.</span>
                  </td>
                </tr>
              ) : null}
              {logs.map((row) => (
                <tr key={row.id}>
                  <td>{new Date(row.created_at).toLocaleString()}</td>
                  <td>
                    <code>{row.operation ?? "—"}</code>
                  </td>
                  <td>{STATUS_LABEL[row.status as SyncStatus] ?? row.status}</td>
                  <td>{row.records_processed}</td>
                  <td>{row.records_skipped}</td>
                  <td>{row.records_failed}</td>
                  <td>{row.triggered_by ?? "—"}</td>
                  <td>
                    <button
                      className="button secondary small"
                      type="button"
                      onClick={() => setDetail(row)}
                    >
                      Detalle
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="actions">
          <button
            className="button secondary small"
            type="button"
            disabled={page <= 0}
            onClick={() => setPage((p) => Math.max(0, p - 1))}
          >
            ← Anterior
          </button>
          <span className="muted">Página {page + 1}</span>
          <button
            className="button secondary small"
            type="button"
            disabled={logs.length < PAGE_SIZE}
            onClick={() => setPage((p) => p + 1)}
          >
            Siguiente →
          </button>
        </div>
      </section>

      {detail ? (
        <Modal
          open
          onClose={() => setDetail(null)}
          title={`Sync ${detail.operation ?? ""} · ${detail.id.slice(0, 8)}…`}
        >
          <dl className="sync-detail">
            <dt>Estado</dt>
            <dd>{STATUS_LABEL[detail.status as SyncStatus] ?? detail.status}</dd>
            <dt>Operación</dt>
            <dd>
              <code>{detail.operation ?? "—"}</code>
            </dd>
            <dt>Started</dt>
            <dd>{detail.started_at ? new Date(detail.started_at).toLocaleString() : "—"}</dd>
            <dt>Finished</dt>
            <dd>{detail.finished_at ? new Date(detail.finished_at).toLocaleString() : "—"}</dd>
            <dt>Trigger</dt>
            <dd>{detail.triggered_by ?? "—"}</dd>
            <dt>Job ID</dt>
            <dd>
              <code>{detail.job_id ?? "—"}</code>
            </dd>
            <dt>Procesados / Saltados / Fallidos</dt>
            <dd>
              {detail.records_processed} / {detail.records_skipped} /{" "}
              {detail.records_failed}
            </dd>
            <dt>Error</dt>
            <dd>
              <code className="audit-metadata">{detail.error_summary ?? "—"}</code>
            </dd>
            <dt>Metadata</dt>
            <dd>
              <code className="audit-metadata">
                {detail.metadata ? JSON.stringify(detail.metadata, null, 2) : "—"}
              </code>
            </dd>
          </dl>
        </Modal>
      ) : null}
    </main>
  );
}
