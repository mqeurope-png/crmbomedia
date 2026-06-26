"use client";

import { Users } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { CreatePipelineWizard } from "../components/CreatePipelineWizard";
import { PageHeader } from "../components/PageHeader";
import { ErrorState } from "../components/ErrorState";
import { ResourceVisibilityBadge } from "../components/ResourceVisibilityBadge";
import {
  deletePipeline,
  duplicatePipeline,
  getCurrentUser,
  getHealth,
  listPipelines,
  updatePipeline,
  type Pipeline,
  type User,
} from "../lib/api";
import { extractErrorMessage } from "../lib/errors";

export default function PipelinesAdminPage() {
  const [pipelines, setPipelines] = useState<Pipeline[]>([]);
  const [includeInactive, setIncludeInactive] = useState(false);
  const [wizardOpen, setWizardOpen] = useState(false);
  const [aiAvailable, setAiAvailable] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [currentUser, setCurrentUser] = useState<User | null>(null);

  const isAdmin = currentUser?.role === "admin";

  const refresh = useCallback(async () => {
    setIsLoading(true);
    try {
      const list = await listPipelines(includeInactive);
      setPipelines(list);
      setError(null);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudieron cargar los pipelines."));
    } finally {
      setIsLoading(false);
    }
  }, [includeInactive]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    getHealth()
      .then((health) => setAiAvailable(health.ai_features_enabled))
      .catch(() => setAiAvailable(false));
    getCurrentUser()
      .then(setCurrentUser)
      .catch(() => setCurrentUser(null));
  }, []);

  async function handleDuplicate(pipeline: Pipeline) {
    try {
      await duplicatePipeline(pipeline.id);
      await refresh();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo duplicar."));
    }
  }

  async function handleArchive(pipeline: Pipeline) {
    if (!window.confirm(`¿Archivar pipeline "${pipeline.name}"?`)) return;
    try {
      await deletePipeline(pipeline.id);
      await refresh();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo archivar el pipeline."));
    }
  }

  async function handleToggleGlobal(pipeline: Pipeline) {
    const becomeGlobal = !(pipeline.is_global ?? false);
    const verb = becomeGlobal
      ? "convertir en del equipo"
      : "convertir en privado (tuyo)";
    if (!window.confirm(`¿Estás seguro de ${verb} el pipeline "${pipeline.name}"?`))
      return;
    try {
      await updatePipeline(pipeline.id, { is_global: becomeGlobal });
      await refresh();
    } catch (err) {
      setError(
        extractErrorMessage(err, "No se pudo cambiar la visibilidad."),
      );
    }
  }

  // PR-Frontend-Workflows-Pipelines-Templates. Agrupación Mis / Equipo.
  const minePipelines = pipelines.filter((p) => p.is_mine);
  const teamPipelines = pipelines.filter((p) => !p.is_mine && p.is_global);

  return (
    <main className="shell shell-wide">
      <PageHeader
        title="Pipelines"
        eyebrow="CRM"
        description="Construye flujos de gestión de contactos con etapas reordenables. Empieza desde cero, parte de una plantilla, o deja que la IA proponga una estructura."
        actions={
          <button
            type="button"
            className="button small"
            onClick={() => setWizardOpen(true)}
          >
            + Nuevo pipeline
          </button>
        }
      />

      <section className="panel">
        <div className="contact-toolbar">
          <label className="checkbox">
            <input
              type="checkbox"
              checked={includeInactive}
              onChange={(event) => setIncludeInactive(event.target.checked)}
            />
            <span>Incluir archivados</span>
          </label>
        </div>
        {error ? <ErrorState title="Error" message={error} /> : null}
        {isLoading && pipelines.length === 0 ? (
          <p className="muted">Cargando…</p>
        ) : pipelines.length === 0 ? (
          <p className="muted">
            No hay pipelines todavía. Pulsa &ldquo;Nuevo pipeline&rdquo; para
            empezar.
          </p>
        ) : (
          <>
            <PipelineGroup
              title="Mis pipelines"
              emptyHint="No tienes pipelines propios."
              items={minePipelines}
              isAdmin={isAdmin}
              onDuplicate={handleDuplicate}
              onArchive={handleArchive}
              onToggleGlobal={handleToggleGlobal}
            />
            <PipelineGroup
              title="Pipelines del equipo"
              emptyHint="Sin pipelines del equipo."
              items={teamPipelines}
              isAdmin={isAdmin}
              onDuplicate={handleDuplicate}
              onArchive={handleArchive}
              onToggleGlobal={handleToggleGlobal}
            />
          </>
        )}
      </section>

      <CreatePipelineWizard
        open={wizardOpen}
        aiAvailable={aiAvailable}
        isAdmin={isAdmin}
        onCreated={async (pipeline) => {
          setWizardOpen(false);
          await refresh();
          // Optimistic prepend so the new pipeline shows up immediately
          // even before refresh resolves.
          setPipelines((current) =>
            current.some((p) => p.id === pipeline.id)
              ? current
              : [pipeline, ...current],
          );
        }}
        onClose={() => setWizardOpen(false)}
      />
    </main>
  );
}

function PipelineGroup({
  title,
  emptyHint,
  items,
  isAdmin,
  onDuplicate,
  onArchive,
  onToggleGlobal,
}: {
  title: string;
  emptyHint: string;
  items: Pipeline[];
  isAdmin: boolean;
  onDuplicate: (p: Pipeline) => void;
  onArchive: (p: Pipeline) => void;
  onToggleGlobal: (p: Pipeline) => void;
}) {
  return (
    <div className="resource-group">
      <header className="resource-group-header">
        <h3>{title}</h3>
        <span className="resource-group-count">({items.length})</span>
      </header>
      {items.length === 0 ? (
        <p className="resource-group-empty">{emptyHint}</p>
      ) : (
        <div className="table-wrapper">
          <table className="data-table">
            <thead>
              <tr>
                <th>Color</th>
                <th>Nombre</th>
                <th>Etapas</th>
                <th>Contactos</th>
                <th>Estado</th>
                <th aria-label="Acciones" />
              </tr>
            </thead>
            <tbody>
              {items.map((pipeline) => (
                <tr key={pipeline.id}>
                  <td>
                    <span
                      className="tag-color-swatch"
                      style={{ background: pipeline.color || "#cdd5e1" }}
                      aria-hidden
                    />
                  </td>
                  <td>
                    <Link href={`/pipelines/${pipeline.id}`}>
                      <strong>{pipeline.name}</strong>
                    </Link>
                    <ResourceVisibilityBadge
                      isMine={!!pipeline.is_mine}
                      isGlobal={!!pipeline.is_global}
                    />
                    {pipeline.description ? (
                      <div className="muted small">{pipeline.description}</div>
                    ) : null}
                  </td>
                  <td>{pipeline.stages.length}</td>
                  <td>{pipeline.contact_count}</td>
                  <td>
                    {pipeline.is_active ? (
                      <span className="status status-open">Activo</span>
                    ) : (
                      <span className="status status-cancelled">Archivado</span>
                    )}
                  </td>
                  <td>
                    <Link
                      href={`/pipelines/${pipeline.id}/edit-stages`}
                      className="button secondary small"
                    >
                      Etapas
                    </Link>
                    <button
                      type="button"
                      className="button secondary small"
                      onClick={() => onDuplicate(pipeline)}
                    >
                      Duplicar
                    </button>
                    {isAdmin ? (
                      <button
                        type="button"
                        className="button secondary small"
                        onClick={() => onToggleGlobal(pipeline)}
                        title={
                          pipeline.is_global
                            ? "Quitar visibilidad para todo el equipo"
                            : "Hacer este pipeline visible para todo el equipo"
                        }
                      >
                        <Users size={11} aria-hidden />{" "}
                        {pipeline.is_global
                          ? "Convertir en privado"
                          : "Convertir en del equipo"}
                      </button>
                    ) : null}
                    {pipeline.is_active ? (
                      <button
                        type="button"
                        className="button secondary small"
                        onClick={() => onArchive(pipeline)}
                      >
                        Archivar
                      </button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
