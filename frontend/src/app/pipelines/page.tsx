"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { CreatePipelineWizard } from "../components/CreatePipelineWizard";
import { PageHeader } from "../components/PageHeader";
import { ErrorState } from "../components/ErrorState";
import {
  deletePipeline,
  duplicatePipeline,
  getHealth,
  listPipelines,
  type Pipeline,
} from "../lib/api";
import { extractErrorMessage } from "../lib/errors";

export default function PipelinesAdminPage() {
  const [pipelines, setPipelines] = useState<Pipeline[]>([]);
  const [includeInactive, setIncludeInactive] = useState(false);
  const [wizardOpen, setWizardOpen] = useState(false);
  const [aiAvailable, setAiAvailable] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

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
                {pipelines.map((pipeline) => (
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
                        onClick={() => handleDuplicate(pipeline)}
                      >
                        Duplicar
                      </button>
                      {pipeline.is_active ? (
                        <button
                          type="button"
                          className="button secondary small"
                          onClick={() => handleArchive(pipeline)}
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
      </section>

      <CreatePipelineWizard
        open={wizardOpen}
        aiAvailable={aiAvailable}
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
