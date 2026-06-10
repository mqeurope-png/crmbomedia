"use client";

import { useCallback, useEffect, useState } from "react";
import { ErrorState } from "../../components/ErrorState";
import { PageHeader } from "../../components/PageHeader";
import {
  exportAuditLogs,
  getAuditLogs,
  getCurrentUser,
  type AuditLog,
  type AuditLogFilters,
} from "../../lib/api";
import { extractErrorMessage } from "../../lib/errors";

const PAGE_SIZE = 50;

type FormFilters = {
  action: string;
  action_prefix: string;
  actor_user_id: string;
  target_type: string;
  from: string;
  to: string;
};

const EMPTY_FILTERS: FormFilters = {
  action: "",
  action_prefix: "",
  actor_user_id: "",
  target_type: "",
  from: "",
  to: "",
};

function toApiFilters(form: FormFilters, page: number): AuditLogFilters {
  const filters: AuditLogFilters = {
    skip: page * PAGE_SIZE,
    limit: PAGE_SIZE,
  };
  if (form.action) filters.action = form.action;
  if (form.action_prefix) filters.action_prefix = form.action_prefix;
  if (form.actor_user_id) filters.actor_user_id = form.actor_user_id;
  if (form.target_type) filters.target_type = form.target_type;
  if (form.from) filters.from = form.from;
  if (form.to) filters.to = form.to;
  return filters;
}

export default function AuditLogsPage() {
  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [form, setForm] = useState<FormFilters>(EMPTY_FILTERS);
  const [appliedFilters, setAppliedFilters] = useState<FormFilters>(EMPTY_FILTERS);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const refresh = useCallback(
    async (filters: FormFilters, currentPage: number) => {
      setIsLoading(true);
      setError(null);
      try {
        const page = await getAuditLogs(toApiFilters(filters, currentPage));
        setLogs(page.items);
        setTotal(page.total);
      } catch (err) {
        setError(extractErrorMessage(err, "No se pudo cargar auditoría"));
      } finally {
        setIsLoading(false);
      }
    },
    [],
  );

  useEffect(() => {
    async function bootstrap() {
      try {
        const me = await getCurrentUser();
        if (me.role !== "admin") {
          throw new Error("No tienes permisos de administrador");
        }
        await refresh(EMPTY_FILTERS, 0);
      } catch (err) {
        setError(extractErrorMessage(err, "No se pudo cargar auditoría"));
        setIsLoading(false);
      }
    }
    bootstrap();
  }, [refresh]);

  function onApply(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setAppliedFilters(form);
    setPage(0);
    refresh(form, 0);
  }

  function onReset() {
    setForm(EMPTY_FILTERS);
    setAppliedFilters(EMPTY_FILTERS);
    setPage(0);
    refresh(EMPTY_FILTERS, 0);
  }

  function onPage(delta: number) {
    const next = Math.max(0, page + delta);
    setPage(next);
    refresh(appliedFilters, next);
  }

  async function onExport(format: "csv" | "json") {
    try {
      const blob = await exportAuditLogs(format, toApiFilters(appliedFilters, 0));
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `audit_logs.${format}`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo exportar auditoría"));
    }
  }

  const maxPage = Math.max(0, Math.ceil(total / PAGE_SIZE) - 1);

  return (
    <main className="shell">
      <PageHeader
        title="Auditoría"
        eyebrow="Administración"
        description="Eventos sensibles del sistema: login (correcto y fallido), cambios de password, gestión de usuarios, 2FA, integraciones, exportaciones, accesos denegados."
        actions={
          <>
            <button className="button secondary small" type="button" onClick={() => onExport("csv")}>
              Exportar CSV
            </button>
            <button className="button secondary small" type="button" onClick={() => onExport("json")}>
              Exportar JSON
            </button>
          </>
        }
      />

      <section className="card">
        <form className="audit-filters" onSubmit={onApply}>
          <label>
            Acción exacta
            <input
              value={form.action}
              onChange={(event) => setForm({ ...form, action: event.target.value })}
              placeholder="auth.login_success"
            />
          </label>
          <label>
            Prefijo de acción
            <input
              value={form.action_prefix}
              onChange={(event) => setForm({ ...form, action_prefix: event.target.value })}
              placeholder="auth., user., integration_api_key."
            />
          </label>
          <label>
            Actor user_id
            <input
              value={form.actor_user_id}
              onChange={(event) => setForm({ ...form, actor_user_id: event.target.value })}
            />
          </label>
          <label>
            Target type
            <input
              value={form.target_type}
              onChange={(event) => setForm({ ...form, target_type: event.target.value })}
              placeholder="user, contact, company, endpoint..."
            />
          </label>
          <label>
            Desde
            <input
              type="datetime-local"
              value={form.from}
              onChange={(event) => setForm({ ...form, from: event.target.value })}
            />
          </label>
          <label>
            Hasta
            <input
              type="datetime-local"
              value={form.to}
              onChange={(event) => setForm({ ...form, to: event.target.value })}
            />
          </label>
          <div className="actions">
            <button className="button" type="submit">Aplicar filtros</button>
            <button className="button secondary" type="button" onClick={onReset}>
              Reset
            </button>
          </div>
        </form>
      </section>

      {isLoading ? <p className="muted">Cargando auditoría...</p> : null}
      {error ? <ErrorState title="Error de auditoría" message={error} /> : null}

      {!error ? (
        <section className="card">
          <div className="section-title">
            <h2>Eventos</h2>
            <span className="muted">
              {total > 0
                ? `Mostrando ${page * PAGE_SIZE + 1}–${Math.min(total, (page + 1) * PAGE_SIZE)} de ${total}`
                : "Sin resultados"}
            </span>
          </div>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Fecha</th>
                  <th>Acción</th>
                  <th>Actor</th>
                  <th>Target</th>
                  <th>IP</th>
                  <th>Metadata</th>
                </tr>
              </thead>
              <tbody>
                {logs.map((log) => (
                  <tr key={log.id}>
                    <td>{new Date(log.created_at).toLocaleString()}</td>
                    <td><code>{log.action}</code></td>
                    <td>{log.actor_email ?? log.actor_user_id ?? "—"}</td>
                    <td>
                      <code>{log.target_type}</code>
                      {log.target_id ? <span className="muted"> · {log.target_id}</span> : null}
                    </td>
                    <td>{log.ip_address ?? "—"}</td>
                    <td>
                      {log.metadata && Object.keys(log.metadata).length > 0 ? (
                        <code className="audit-metadata">{JSON.stringify(log.metadata)}</code>
                      ) : (
                        log.message ?? "—"
                      )}
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
              onClick={() => onPage(-1)}
            >
              ← Anterior
            </button>
            <span className="muted">Página {page + 1} de {maxPage + 1}</span>
            <button
              className="button secondary small"
              type="button"
              disabled={page >= maxPage}
              onClick={() => onPage(1)}
            >
              Siguiente →
            </button>
          </div>
        </section>
      ) : null}
    </main>
  );
}
