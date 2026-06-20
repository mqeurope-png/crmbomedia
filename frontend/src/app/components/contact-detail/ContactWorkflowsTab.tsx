"use client";

import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleX,
  Plus,
  Workflow as WorkflowIcon,
  XCircle,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  addContactToWorkflow,
  cancelWorkflowRun,
  getWorkflowRunDetail,
  listContactWorkflowRuns,
  listWorkflows,
  type WorkflowRead,
  type WorkflowRunDetail,
  type WorkflowRunHistoryRead,
  type WorkflowRunRead,
} from "../../lib/workflowsApi";
import { formatBackendDateTime } from "../../lib/dates";
import { extractErrorMessage } from "../../lib/errors";

type Props = {
  contactId: string;
  canManage: boolean;
};

/**
 * PR-Fix-Pestaña-Workflows-Y-Humanizar #1.
 *
 * Pestaña "Workflows" dentro de la ficha del contacto. Estructurada
 * en dos secciones:
 *
 *   - **Workflows activos**: runs en estado `running`, `waiting` o
 *     `waiting_for_event`. Cada fila con botón "Cancelar" si el user
 *     es admin/manager (gating reusa `canManage`).
 *   - **Histórico**: runs `completed`, `cancelled`, `failed`
 *     ordenados desc. Cada fila clickable expande un timeline de
 *     steps obtenido vía `GET /api/workflows/runs/{id}`.
 *
 * Si `canManage`, header tiene botón "Añadir manualmente a un
 * workflow" que abre un modal con dropdown de workflows ACTIVE.
 */
export function ContactWorkflowsTab({ contactId, canManage }: Props) {
  const [runs, setRuns] = useState<WorkflowRunRead[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [addModalOpen, setAddModalOpen] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setRuns(await listContactWorkflowRuns(contactId));
    } catch (err) {
      setError(
        extractErrorMessage(err, "No se pudieron cargar los workflows."),
      );
    } finally {
      setLoading(false);
    }
  }, [contactId]);

  useEffect(() => {
    void load();
  }, [load]);

  const { active, history } = useMemo(() => splitRuns(runs), [runs]);

  if (loading) return <p className="muted">Cargando…</p>;
  if (error) return <p className="form-error">{error}</p>;

  return (
    <div className="contact-workflows-tab">
      <header className="contact-workflows-header">
        <h3>
          <WorkflowIcon size={16} aria-hidden /> Workflows
        </h3>
        {canManage ? (
          <button
            type="button"
            className="button secondary small"
            onClick={() => setAddModalOpen(true)}
          >
            <Plus size={11} aria-hidden /> Añadir manualmente a un workflow
          </button>
        ) : null}
      </header>

      {runs.length === 0 ? (
        <p className="muted contact-workflows-empty">
          <WorkflowIcon size={14} aria-hidden /> Este contacto no ha entrado en
          ningún workflow todavía. Los workflows que filtren por sus
          características aparecerán aquí cuando se disparen.
        </p>
      ) : (
        <>
          <RunsSection
            title="Workflows activos"
            runs={active}
            canManage={canManage}
            onRefresh={load}
            onError={setError}
            emptyMessage="Ningún workflow activo ahora mismo."
            expandable={false}
          />
          <RunsSection
            title="Histórico"
            runs={history}
            canManage={canManage}
            onRefresh={load}
            onError={setError}
            emptyMessage="Sin runs en histórico."
            expandable
          />
        </>
      )}

      {addModalOpen && canManage ? (
        <AddToWorkflowModal
          contactId={contactId}
          onClose={() => setAddModalOpen(false)}
          onAdded={async () => {
            setAddModalOpen(false);
            await load();
          }}
        />
      ) : null}
    </div>
  );
}

function RunsSection({
  title,
  runs,
  canManage,
  onRefresh,
  onError,
  emptyMessage,
  expandable,
}: {
  title: string;
  runs: WorkflowRunRead[];
  canManage: boolean;
  onRefresh: () => Promise<void>;
  onError: (msg: string) => void;
  emptyMessage: string;
  expandable: boolean;
}) {
  return (
    <section className="contact-workflows-section">
      <h4>
        {title}
        <span className="muted small"> · {runs.length}</span>
      </h4>
      {runs.length === 0 ? (
        <p className="muted small">{emptyMessage}</p>
      ) : (
        <table className="workflow-runs-table">
          <thead>
            <tr>
              <th />
              <th>Workflow</th>
              <th>Estado</th>
              <th>Iniciado</th>
              <th>Terminado</th>
              {canManage ? <th /> : null}
            </tr>
          </thead>
          <tbody>
            {runs.map((r) => (
              <RunRow
                key={r.id}
                run={r}
                canManage={canManage}
                expandable={expandable}
                onRefresh={onRefresh}
                onError={onError}
              />
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function RunRow({
  run,
  canManage,
  expandable,
  onRefresh,
  onError,
}: {
  run: WorkflowRunRead;
  canManage: boolean;
  expandable: boolean;
  onRefresh: () => Promise<void>;
  onError: (msg: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [detail, setDetail] = useState<WorkflowRunDetail | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);

  const toggleExpand = useCallback(async () => {
    if (!expandable) return;
    const next = !expanded;
    setExpanded(next);
    if (next && detail === null) {
      setLoadingDetail(true);
      try {
        setDetail(await getWorkflowRunDetail(run.id));
      } catch (err) {
        onError(
          extractErrorMessage(err, "No se pudo cargar el detalle del run."),
        );
      } finally {
        setLoadingDetail(false);
      }
    }
  }, [expandable, expanded, detail, run.id, onError]);

  const skippedCount =
    run.error_summary?.match(/^completed_with_skipped:(\d+)/)?.[1];

  return (
    <>
      <tr
        className={expandable ? "workflow-runs-row is-clickable" : undefined}
        onClick={expandable ? () => void toggleExpand() : undefined}
      >
        <td>
          {expandable ? (
            expanded ? (
              <ChevronDown size={12} aria-hidden />
            ) : (
              <ChevronRight size={12} aria-hidden />
            )
          ) : null}
        </td>
        <td>
          <Link
            href={`/admin/workflows/${run.workflow_id}`}
            onClick={(e) => e.stopPropagation()}
          >
            {run.workflow_name ?? run.workflow_id}
          </Link>
        </td>
        <td>
          <span className={`badge ${badgeClass(run.state)}`}>{run.state}</span>
          {run.exit_kind ? (
            <span className="muted small"> · {run.exit_kind}</span>
          ) : null}
          {skippedCount ? (
            <span
              className="badge warn"
              style={{ marginLeft: 6 }}
              title={`${skippedCount} paso(s) se saltaron por una condición no cumplida.`}
            >
              ⚠ {skippedCount}
            </span>
          ) : null}
        </td>
        <td>{formatBackendDateTime(run.started_at)}</td>
        <td>
          {run.completed_at ? formatBackendDateTime(run.completed_at) : "—"}
        </td>
        {canManage ? (
          <td onClick={(e) => e.stopPropagation()}>
            {run.state === "running" ||
            run.state === "waiting" ||
            run.state === "waiting_for_event" ? (
              <button
                type="button"
                className="button secondary small"
                onClick={async () => {
                  if (!confirm("¿Cancelar este run?")) return;
                  try {
                    await cancelWorkflowRun(run.id);
                    await onRefresh();
                  } catch (err) {
                    onError(extractErrorMessage(err, "No se pudo cancelar."));
                  }
                }}
              >
                <CircleX size={11} aria-hidden /> Cancelar
              </button>
            ) : null}
          </td>
        ) : null}
      </tr>
      {expanded ? (
        <tr className="workflow-runs-timeline-row">
          <td colSpan={canManage ? 6 : 5}>
            {loadingDetail ? (
              <p className="muted small">Cargando timeline…</p>
            ) : detail ? (
              <Timeline history={detail.history} />
            ) : (
              <p className="muted small">Sin detalle disponible.</p>
            )}
          </td>
        </tr>
      ) : null}
    </>
  );
}

function Timeline({ history }: { history: WorkflowRunHistoryRead[] }) {
  if (history.length === 0) {
    return <p className="muted small">El run no ejecutó ningún step.</p>;
  }
  return (
    <ol className="workflow-runs-timeline">
      {history.map((h) => (
        <li key={h.id} className={`workflow-runs-step is-${h.status}`}>
          <span className="workflow-runs-step-icon" aria-hidden>
            {h.status === "ok" ? (
              <CheckCircle2 size={12} />
            ) : h.status === "failed" ? (
              <XCircle size={12} />
            ) : h.status === "skipped" ? (
              <CircleX size={12} />
            ) : (
              <ChevronRight size={12} />
            )}
          </span>
          <span className="workflow-runs-step-meta">
            <strong>{h.step_type}</strong>{" "}
            <span className="muted small">
              · {formatBackendDateTime(h.executed_at)} · {h.status}
            </span>
          </span>
          {h.error_summary ? (
            <span className="workflow-runs-step-error">
              {h.error_summary}
            </span>
          ) : null}
        </li>
      ))}
    </ol>
  );
}

function AddToWorkflowModal({
  contactId,
  onClose,
  onAdded,
}: {
  contactId: string;
  onClose: () => void;
  onAdded: () => Promise<void>;
}) {
  const [workflows, setWorkflows] = useState<WorkflowRead[]>([]);
  const [selected, setSelected] = useState("");
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    listWorkflows("active")
      .then((rows) => {
        if (!cancelled) setWorkflows(rows);
      })
      .catch(() => {
        if (!cancelled)
          setError("No se pudieron cargar los workflows activos.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const submit = async () => {
    if (!selected) return;
    setSubmitting(true);
    setError(null);
    try {
      await addContactToWorkflow(selected, contactId);
      await onAdded();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo añadir."));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="modal-backdrop" onClick={() => !submitting && onClose()}>
      <div
        className="modal-card"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
      >
        <h2>Añadir manualmente a un workflow</h2>
        <p className="muted small">
          Crea un workflow run forzado saltándose el trigger y los filtros.
          Audit log registra la entrada como manual.
        </p>
        {loading ? (
          <p className="muted">Cargando workflows…</p>
        ) : workflows.length === 0 ? (
          <p className="form-error small">
            No hay workflows activos para añadir.
          </p>
        ) : (
          <label>
            Workflow
            <select
              value={selected}
              onChange={(e) => setSelected(e.target.value)}
            >
              <option value="">— Selecciona —</option>
              {workflows.map((w) => (
                <option key={w.id} value={w.id}>
                  {w.name}
                </option>
              ))}
            </select>
          </label>
        )}
        {error ? <p className="form-error">{error}</p> : null}
        <div className="modal-actions">
          <button
            type="button"
            className="button secondary"
            onClick={onClose}
            disabled={submitting}
          >
            Cancelar
          </button>
          <button
            type="button"
            className="button"
            onClick={submit}
            disabled={!selected || submitting}
          >
            {submitting ? "Añadiendo…" : "Añadir"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------

const ACTIVE_STATES = new Set(["running", "waiting", "waiting_for_event"]);

function splitRuns(runs: WorkflowRunRead[]): {
  active: WorkflowRunRead[];
  history: WorkflowRunRead[];
} {
  const active: WorkflowRunRead[] = [];
  const history: WorkflowRunRead[] = [];
  for (const r of runs) {
    if (ACTIVE_STATES.has(r.state)) active.push(r);
    else history.push(r);
  }
  // historico ordenado por completed_at desc (fallback started_at).
  history.sort((a, b) => {
    const ax = a.completed_at ?? a.started_at;
    const bx = b.completed_at ?? b.started_at;
    return bx.localeCompare(ax);
  });
  return { active, history };
}

function badgeClass(state: string): string {
  if (
    state === "running" ||
    state === "waiting" ||
    state === "waiting_for_event"
  ) {
    return "active";
  }
  if (state === "completed") return "ok";
  if (state === "cancelled") return "muted";
  if (state === "failed") return "warn";
  return "muted";
}
