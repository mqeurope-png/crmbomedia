"use client";

import { useCallback, useEffect, useState } from "react";
import { ErrorState } from "../../components/ErrorState";
import { PageHeader } from "../../components/PageHeader";
import {
  exportAuditLogs,
  getAuditLogs,
  getCurrentUser,
  getUsers,
  type AuditLog,
  type AuditLogFilters,
  type User,
} from "../../lib/api";
import { formatBackendDateTime } from "../../lib/dates";
import { extractErrorMessage } from "../../lib/errors";

const PAGE_SIZE = 50;

// PR-TagPicker-Ficha-Contacto Feature C. Prefijos de acción agrupados
// para el selector — evitan que el admin tenga que recordar el nombre
// exacto. El backend filtra por `action_prefix` (LIKE "prefix%").
const ACTION_PREFIXES: { value: string; label: string }[] = [
  { value: "", label: "Todas las acciones" },
  { value: "auth.", label: "Autenticación (login, password, 2FA)" },
  { value: "user.", label: "Usuarios" },
  { value: "contact.", label: "Contactos" },
  { value: "contact_tag.", label: "Tags de contacto" },
  { value: "tag.", label: "Tags (gestión)" },
  { value: "workflow.", label: "Workflows" },
  { value: "pipeline.", label: "Pipelines" },
  { value: "segment.", label: "Segmentos" },
  { value: "email.", label: "Emails" },
  { value: "gmail.", label: "Gmail (backfill, watches)" },
  { value: "integration_account.", label: "Integraciones" },
  { value: "gdpr.", label: "GDPR" },
  { value: "backup.", label: "Backups" },
  { value: "company.", label: "Empresas" },
];

const TARGET_TYPES: string[] = [
  "",
  "user",
  "contact",
  "company",
  "workflow",
  "pipeline",
  "pipeline_stage",
  "tag",
  "segment",
  "gmail_backfill_job",
  "audit_log",
];

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

function truncateUa(ua: string | null | undefined): string {
  if (!ua) return "—";
  return ua.length > 40 ? `${ua.slice(0, 40)}…` : ua;
}

export default function AuditLogsPage() {
  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [form, setForm] = useState<FormFilters>(EMPTY_FILTERS);
  const [appliedFilters, setAppliedFilters] = useState<FormFilters>(EMPTY_FILTERS);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [users, setUsers] = useState<User[]>([]);
  // PR-TagPicker-Ficha-Contacto Feature C. Fila seleccionada → modal
  // con el detalle completo (metadata JSON formateada + user-agent).
  const [selected, setSelected] = useState<AuditLog | null>(null);

  const refresh = useCallback(
    async (filters: FormFilters, currentPage: number) => {
      setIsLoading(true);
      setError(null);
      try {
        const result = await getAuditLogs(toApiFilters(filters, currentPage));
        setLogs(result.items);
        setTotal(result.total);
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
        // Cargamos la lista de users para el selector de actor.
        getUsers()
          .then(setUsers)
          .catch(() => setUsers([]));
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

  function userLabel(log: AuditLog): string {
    if (log.actor_email) return log.actor_email;
    if (log.actor_user_id) {
      const u = users.find((x) => x.id === log.actor_user_id);
      return u ? u.full_name || u.email : log.actor_user_id;
    }
    return "—";
  }

  return (
    <main className="shell">
      <PageHeader
        title="Auditoría"
        eyebrow="Administración"
        description="Actividad de usuarios y eventos sensibles: login (correcto y fallido con IP), cambios de password, gestión de usuarios, 2FA, integraciones, tags, workflows, pipelines, exportaciones, accesos denegados."
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
            Usuario
            <select
              value={form.actor_user_id}
              onChange={(event) =>
                setForm({ ...form, actor_user_id: event.target.value })
              }
            >
              <option value="">Todos los usuarios</option>
              {users.map((u) => (
                <option key={u.id} value={u.id}>
                  {u.full_name || u.email} ({u.role})
                </option>
              ))}
            </select>
          </label>
          <label>
            Acción
            <select
              value={form.action_prefix}
              onChange={(event) =>
                setForm({ ...form, action_prefix: event.target.value })
              }
            >
              {ACTION_PREFIXES.map((p) => (
                <option key={p.value} value={p.value}>
                  {p.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            Acción exacta (opcional)
            <input
              value={form.action}
              onChange={(event) => setForm({ ...form, action: event.target.value })}
              placeholder="auth.login_success"
            />
          </label>
          <label>
            Target type
            <select
              value={form.target_type}
              onChange={(event) =>
                setForm({ ...form, target_type: event.target.value })
              }
            >
              {TARGET_TYPES.map((t) => (
                <option key={t || "all"} value={t}>
                  {t || "Todos"}
                </option>
              ))}
            </select>
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
                  <th>Usuario</th>
                  <th>Acción</th>
                  <th>Target</th>
                  <th>IP</th>
                  <th>User-Agent</th>
                </tr>
              </thead>
              <tbody>
                {logs.map((log) => (
                  <tr
                    key={log.id}
                    className="audit-row"
                    onClick={() => setSelected(log)}
                    title="Ver detalle"
                  >
                    <td>{formatBackendDateTime(log.created_at)}</td>
                    <td>{userLabel(log)}</td>
                    <td><code>{log.action}</code></td>
                    <td>
                      <code>{log.target_type}</code>
                      {log.target_id ? <span className="muted"> · {log.target_id}</span> : null}
                    </td>
                    <td>{log.ip_address ?? "—"}</td>
                    <td className="muted small">{truncateUa(log.user_agent)}</td>
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

      {selected ? (
        <div
          className="modal-overlay"
          role="dialog"
          aria-modal="true"
          onClick={(e) => {
            if (e.target === e.currentTarget) setSelected(null);
          }}
        >
          <div className="modal-dialog">
            <div className="modal-header">
              <h2>Detalle del evento</h2>
              <button
                type="button"
                className="modal-close"
                onClick={() => setSelected(null)}
                aria-label="Cerrar"
              >
                ×
              </button>
            </div>
            <div className="modal-body">
              <dl className="audit-detail">
                <dt>Fecha</dt>
                <dd>{formatBackendDateTime(selected.created_at)}</dd>
                <dt>Usuario</dt>
                <dd>{userLabel(selected)}</dd>
                <dt>Acción</dt>
                <dd><code>{selected.action}</code></dd>
                <dt>Target</dt>
                <dd>
                  <code>{selected.target_type}</code>
                  {selected.target_id ? ` · ${selected.target_id}` : ""}
                </dd>
                <dt>IP</dt>
                <dd>{selected.ip_address ?? "—"}</dd>
                <dt>User-Agent</dt>
                <dd className="audit-detail-ua">{selected.user_agent ?? "—"}</dd>
                {selected.message ? (
                  <>
                    <dt>Mensaje</dt>
                    <dd>{selected.message}</dd>
                  </>
                ) : null}
                <dt>Metadata</dt>
                <dd>
                  {selected.metadata && Object.keys(selected.metadata).length > 0 ? (
                    <pre className="audit-detail-json">
                      {JSON.stringify(selected.metadata, null, 2)}
                    </pre>
                  ) : (
                    "—"
                  )}
                </dd>
              </dl>
              <div className="modal-footer">
                <button
                  type="button"
                  className="button"
                  onClick={() => setSelected(null)}
                >
                  Cerrar
                </button>
              </div>
            </div>
          </div>
        </div>
      ) : null}
    </main>
  );
}
