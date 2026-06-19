"use client";

import {
  Archive,
  CirclePause,
  CirclePlay,
  Plus,
  Trash2,
  Workflow as WorkflowIcon,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { PageHeader } from "../../components/PageHeader";
import {
  archiveWorkflow,
  createWorkflow,
  deleteWorkflow,
  getWorkflowCatalog,
  listWorkflows,
  pauseWorkflow,
  type WorkflowCatalog,
  type WorkflowRead,
} from "../../lib/workflowsApi";
import { extractErrorMessage } from "../../lib/errors";

/** Lista global de workflows (`/admin/workflows`).
 *
 *  Cabecera con métricas + tabla de workflows + botón crear. Cada fila
 *  enlaza al editor `/admin/workflows/{id}`. Pause/archive/delete
 *  inline desde la propia fila. */
export default function WorkflowsListPage() {
  const [items, setItems] = useState<WorkflowRead[]>([]);
  const [catalog, setCatalog] = useState<WorkflowCatalog | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [draftName, setDraftName] = useState("");
  const [draftTrigger, setDraftTrigger] = useState("contact.created");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setItems(await listWorkflows());
      setCatalog(await getWorkflowCatalog());
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudieron cargar los workflows."));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const onCreate = async () => {
    if (!draftName.trim()) return;
    try {
      const created = await createWorkflow({
        name: draftName.trim(),
        trigger_type: draftTrigger,
      });
      setDraftName("");
      setCreating(false);
      window.location.href = `/admin/workflows/${created.id}`;
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo crear el workflow."));
    }
  };

  const counters = items.reduce(
    (acc, w) => ({
      active: acc.active + (w.status === "active" ? 1 : 0),
      draft: acc.draft + (w.status === "draft" ? 1 : 0),
      runs: acc.runs + (w.total_entered - w.total_completed - w.total_cancelled - w.total_failed),
    }),
    { active: 0, draft: 0, runs: 0 },
  );

  return (
    <div className="page">
      <PageHeader
        title="Workflows"
        description={`${counters.active} activos · ${counters.draft} borradores · ${Math.max(counters.runs, 0)} contactos en ejecución`}
        actions={
          <button
            type="button"
            className="button"
            onClick={() => setCreating(true)}
          >
            <Plus size={14} aria-hidden /> Nuevo workflow
          </button>
        }
      />

      {error ? <p className="form-error">{error}</p> : null}

      {creating ? (
        <div className="form-card">
          <h3>Nuevo workflow</h3>
          <label>
            Nombre
            <input
              autoFocus
              value={draftName}
              onChange={(e) => setDraftName(e.target.value)}
              placeholder="ej. Onboarding lead FESPA"
            />
          </label>
          <label>
            Trigger inicial
            <select
              value={draftTrigger}
              onChange={(e) => setDraftTrigger(e.target.value)}
            >
              {catalog?.triggers.map((t) => (
                <option key={t.type} value={t.type}>
                  {t.label}
                </option>
              ))}
            </select>
          </label>
          <div className="actions">
            <button type="button" className="button" onClick={onCreate}>
              Crear y editar
            </button>
            <button
              type="button"
              className="button secondary"
              onClick={() => setCreating(false)}
            >
              Cancelar
            </button>
          </div>
        </div>
      ) : null}

      {loading ? (
        <p className="muted">Cargando…</p>
      ) : items.length === 0 ? (
        <p className="muted">
          <WorkflowIcon size={14} aria-hidden /> Aún no has creado ningún workflow.
        </p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>Nombre</th>
              <th>Trigger</th>
              <th>Estado</th>
              <th>En ejecución</th>
              <th>Total</th>
              <th>Ganados</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {items.map((w) => {
              const inFlight =
                w.total_entered - w.total_completed - w.total_cancelled - w.total_failed;
              return (
                <tr key={w.id}>
                  <td>
                    <Link href={`/admin/workflows/${w.id}`}>{w.name}</Link>
                  </td>
                  <td>
                    <code className="small">{w.trigger_type}</code>
                  </td>
                  <td>
                    <span className={`badge ${statusClass(w.status)}`}>
                      {w.status}
                    </span>
                  </td>
                  <td>{Math.max(inFlight, 0)}</td>
                  <td>{w.total_entered}</td>
                  <td>{w.total_won}</td>
                  <td className="actions">
                    {w.status === "active" ? (
                      <button
                        type="button"
                        className="button secondary small"
                        onClick={async () => {
                          await pauseWorkflow(w.id);
                          await load();
                        }}
                      >
                        <CirclePause size={11} aria-hidden /> Pausar
                      </button>
                    ) : w.status === "paused" || w.status === "draft" ? (
                      <Link
                        href={`/admin/workflows/${w.id}`}
                        className="button secondary small"
                      >
                        <CirclePlay size={11} aria-hidden /> Editar
                      </Link>
                    ) : null}
                    {w.status !== "archived" ? (
                      <button
                        type="button"
                        className="button secondary small"
                        onClick={async () => {
                          if (!confirm(`¿Archivar "${w.name}"?`)) return;
                          await archiveWorkflow(w.id);
                          await load();
                        }}
                      >
                        <Archive size={11} aria-hidden /> Archivar
                      </button>
                    ) : null}
                    <button
                      type="button"
                      className="button secondary small"
                      onClick={async () => {
                        if (
                          !confirm(
                            `¿Borrar "${w.name}"? Se cancelarán las ejecuciones activas.`,
                          )
                        )
                          return;
                        await deleteWorkflow(w.id);
                        await load();
                      }}
                    >
                      <Trash2 size={11} aria-hidden /> Borrar
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}

function statusClass(status: string): string {
  if (status === "active") return "ok";
  if (status === "paused") return "warn";
  if (status === "archived") return "muted";
  return "active";
}
