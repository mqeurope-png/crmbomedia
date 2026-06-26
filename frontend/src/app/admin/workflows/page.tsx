"use client";

import {
  Archive,
  CirclePause,
  CirclePlay,
  Copy,
  LayoutTemplate,
  Plus,
  Trash2,
  Users,
  Workflow as WorkflowIcon,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { PageHeader } from "../../components/PageHeader";
import { ResourceVisibilityBadge } from "../../components/ResourceVisibilityBadge";
import {
  archiveWorkflow,
  createWorkflow,
  deleteWorkflow,
  duplicateWorkflow,
  getWorkflowCatalog,
  listWorkflowTemplates,
  listWorkflows,
  pauseWorkflow,
  updateWorkflow,
  createWorkflowFromTemplate,
  type WorkflowCatalog,
  type WorkflowRead,
  type WorkflowTemplate,
} from "../../lib/workflowsApi";
import { getCurrentUser, type User } from "../../lib/api";
import { extractErrorMessage } from "../../lib/errors";
import { humanizeTrigger } from "../../lib/workflowsHumanize";

/** Lista global de workflows (`/admin/workflows`).
 *
 *  Cabecera con métricas + tabla de workflows + botón crear. Cada fila
 *  enlaza al editor `/admin/workflows/{id}`. Pause/archive/delete
 *  inline desde la propia fila. */
export default function WorkflowsListPage() {
  const [items, setItems] = useState<WorkflowRead[]>([]);
  const [catalog, setCatalog] = useState<WorkflowCatalog | null>(null);
  const [templates, setTemplates] = useState<WorkflowTemplate[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [templatesOpen, setTemplatesOpen] = useState(false);
  const [draftName, setDraftName] = useState("");
  const [draftTrigger, setDraftTrigger] = useState("contact.created");
  const [currentUser, setCurrentUser] = useState<User | null>(null);
  // PR-Frontend-Workflows-Pipelines-Templates. Para admin, el checkbox
  // "Compartir con el equipo" arranca MARCADO — mantiene el comportamiento
  // histórico de "admin crea workflows compartidos por defecto" y evita
  // regresión silenciosa post-#250.
  const [draftIsGlobal, setDraftIsGlobal] = useState(true);

  const isAdmin = currentUser?.role === "admin";

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setItems(await listWorkflows());
      setCatalog(await getWorkflowCatalog());
      setTemplates(await listWorkflowTemplates());
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudieron cargar los workflows."));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    getCurrentUser()
      .then(setCurrentUser)
      .catch(() => setCurrentUser(null));
  }, [load]);

  const onCreate = async () => {
    if (!draftName.trim()) return;
    try {
      const created = await createWorkflow({
        name: draftName.trim(),
        trigger_type: draftTrigger,
        // Solo se manda si current_user es admin — el backend ignora el
        // campo (defaulteándolo a false) si current_user no tiene permisos.
        is_global: isAdmin ? draftIsGlobal : undefined,
      });
      setDraftName("");
      setCreating(false);
      window.location.href = `/admin/workflows/${created.id}`;
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo crear el workflow."));
    }
  };

  async function handleToggleGlobal(w: WorkflowRead) {
    const becomeGlobal = !(w.is_global ?? false);
    const verb = becomeGlobal ? "convertir en del equipo" : "convertir en privado (tuyo)";
    if (!confirm(`¿Estás seguro de ${verb} el workflow "${w.name}"?`)) return;
    try {
      await updateWorkflow(w.id, { is_global: becomeGlobal });
      await load();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo cambiar la visibilidad."));
    }
  }

  const counters = items.reduce(
    (acc, w) => ({
      active: acc.active + (w.status === "active" ? 1 : 0),
      draft: acc.draft + (w.status === "draft" ? 1 : 0),
      runs: acc.runs + (w.total_entered - w.total_completed - w.total_cancelled - w.total_failed),
    }),
    { active: 0, draft: 0, runs: 0 },
  );

  // PR-Frontend-Workflows-Pipelines-Templates. Agrupar Mis / Equipo.
  // Admin owner de un global → cae en "Mis" porque is_mine=true.
  const mineItems = items.filter((w) => w.is_mine);
  const teamItems = items.filter((w) => !w.is_mine && w.is_global);

  return (
    <div className="page">
      <PageHeader
        title="Workflows"
        description={`${counters.active} activos · ${counters.draft} borradores · ${Math.max(counters.runs, 0)} contactos en ejecución`}
        actions={
          <>
            <button
              type="button"
              className="button secondary"
              onClick={() => setTemplatesOpen(true)}
            >
              <LayoutTemplate size={14} aria-hidden /> Desde plantilla
            </button>
            <button
              type="button"
              className="button"
              onClick={() => setCreating(true)}
            >
              <Plus size={14} aria-hidden /> Nuevo workflow
            </button>
          </>
        }
      />

      {error ? <p className="form-error">{error}</p> : null}

      {templatesOpen ? (
        <div className="form-card">
          <h3>
            <LayoutTemplate size={14} aria-hidden /> Plantillas
          </h3>
          <p className="muted small">
            Arranca con un workflow preconfigurado. Lo puedes editar
            todo antes de activarlo.
          </p>
          <ul className="workflow-template-gallery">
            {templates.map((t) => (
              <li key={t.id}>
                <strong>{t.name}</strong>
                <p className="muted small">{t.description}</p>
                <p className="muted small">
                  Trigger: <code>{humanizeTrigger(t.trigger_type)}</code> · {t.steps_count} pasos
                </p>
                <button
                  type="button"
                  className="button"
                  onClick={async () => {
                    try {
                      const created = await createWorkflowFromTemplate(t.id);
                      setTemplatesOpen(false);
                      window.location.href = `/admin/workflows/${created.id}`;
                    } catch (err) {
                      setError(
                        extractErrorMessage(
                          err,
                          "No se pudo crear desde plantilla.",
                        ),
                      );
                    }
                  }}
                >
                  Usar plantilla
                </button>
              </li>
            ))}
          </ul>
          <button
            type="button"
            className="button secondary"
            onClick={() => setTemplatesOpen(false)}
          >
            Cerrar
          </button>
        </div>
      ) : null}

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
          {/* PR-Frontend-Workflows-Pipelines-Templates. Solo admin ve el
              checkbox. Default MARCADO para mantener el comportamiento
              histórico (admin → workflow compartido). */}
          {isAdmin ? (
            <label className="checkbox">
              <input
                type="checkbox"
                checked={draftIsGlobal}
                onChange={(e) => setDraftIsGlobal(e.target.checked)}
              />
              <span>
                <Users size={12} aria-hidden /> Compartir con el equipo
                <span className="muted small" style={{ marginLeft: 6 }}>
                  Todos los users podrán verlo y editar contactos suyos
                </span>
              </span>
            </label>
          ) : null}
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
        <>
          <WorkflowGroup
            title="Mis workflows"
            emptyHint="No tienes workflows propios."
            items={mineItems}
            isAdmin={isAdmin}
            onReload={load}
            onError={setError}
            onToggleGlobal={handleToggleGlobal}
          />
          <WorkflowGroup
            title="Workflows del equipo"
            emptyHint="Sin workflows del equipo."
            items={teamItems}
            isAdmin={isAdmin}
            onReload={load}
            onError={setError}
            onToggleGlobal={handleToggleGlobal}
          />
        </>
      )}
    </div>
  );
}

function WorkflowGroup({
  title,
  emptyHint,
  items,
  isAdmin,
  onReload,
  onError,
  onToggleGlobal,
}: {
  title: string;
  emptyHint: string;
  items: WorkflowRead[];
  isAdmin: boolean;
  onReload: () => Promise<void>;
  onError: (msg: string) => void;
  onToggleGlobal: (w: WorkflowRead) => Promise<void>;
}) {
  return (
    <section className="resource-group">
      <header className="resource-group-header">
        <h3>{title}</h3>
        <span className="resource-group-count">({items.length})</span>
      </header>
      {items.length === 0 ? (
        <p className="resource-group-empty">{emptyHint}</p>
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
                    <ResourceVisibilityBadge
                      isMine={!!w.is_mine}
                      isGlobal={!!w.is_global}
                    />
                  </td>
                  <td>
                    {humanizeTrigger(w.trigger_type)}
                    <span className="muted small"> · <code>{w.trigger_type}</code></span>
                  </td>
                  <td>
                    <span className={`badge ${statusClass(w.status)}`}>
                      {w.status}
                    </span>
                  </td>
                  <td>{Math.max(inFlight, 0)}</td>
                  <td>{w.total_entered}</td>
                  <td>
                    {w.total_won}
                    {w.total_completed_with_skipped > 0 ? (
                      <span
                        className="badge warn"
                        title={`${w.total_completed_with_skipped} run(s) completaron con pasos saltados — abre la ficha contacto afectada para ver detalles.`}
                        style={{ marginLeft: 6 }}
                      >
                        ⚠ {w.total_completed_with_skipped}
                      </span>
                    ) : null}
                  </td>
                  <td className="actions">
                    {w.status === "active" ? (
                      <button
                        type="button"
                        className="button secondary small"
                        onClick={async () => {
                          await pauseWorkflow(w.id);
                          await onReload();
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
                    <button
                      type="button"
                      className="button secondary small"
                      onClick={async () => {
                        try {
                          const dup = await duplicateWorkflow(w.id);
                          window.location.href = `/admin/workflows/${dup.id}`;
                        } catch (err) {
                          onError(
                            extractErrorMessage(err, "No se pudo duplicar."),
                          );
                        }
                      }}
                    >
                      <Copy size={11} aria-hidden /> Duplicar
                    </button>
                    {/* PR-Frontend-Workflows-Pipelines-Templates. Solo
                        admin ve el botón de convertir. Texto cambia según
                        el estado actual del workflow. */}
                    {isAdmin ? (
                      <button
                        type="button"
                        className="button secondary small"
                        onClick={() => onToggleGlobal(w)}
                        title={
                          w.is_global
                            ? "Quitar visibilidad para todo el equipo"
                            : "Hacer este workflow visible para todo el equipo"
                        }
                      >
                        <Users size={11} aria-hidden />{" "}
                        {w.is_global
                          ? "Convertir en privado"
                          : "Convertir en del equipo"}
                      </button>
                    ) : null}
                    {w.status !== "archived" ? (
                      <button
                        type="button"
                        className="button secondary small"
                        onClick={async () => {
                          if (!confirm(`¿Archivar "${w.name}"?`)) return;
                          await archiveWorkflow(w.id);
                          await onReload();
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
                        await onReload();
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
    </section>
  );
}

function statusClass(status: string): string {
  if (status === "active") return "ok";
  if (status === "paused") return "warn";
  if (status === "archived") return "muted";
  return "active";
}
