"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { ErrorState } from "../components/ErrorState";
import {
  createPipeline,
  deletePipeline,
  duplicatePipeline,
  listPipelines,
  type Pipeline,
} from "../lib/api";
import { extractErrorMessage } from "../lib/errors";
import { TAG_PALETTE } from "../lib/tagPalette";

const DEFAULT_STAGES = [
  { name: "Nuevo lead" },
  { name: "Contactado" },
  { name: "Cualificado" },
  { name: "Propuesta enviada" },
  { name: "Negociación" },
  { name: "Cerrado ganado", is_won: true },
  { name: "Cerrado perdido", is_lost: true },
];

export default function PipelinesAdminPage() {
  const [pipelines, setPipelines] = useState<Pipeline[]>([]);
  const [includeInactive, setIncludeInactive] = useState(false);
  const [showCreate, setShowCreate] = useState(false);
  const [creating, setCreating] = useState(false);
  const [draftName, setDraftName] = useState("");
  const [draftDescription, setDraftDescription] = useState("");
  const [draftColor, setDraftColor] = useState<string | null>(null);
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

  async function handleCreate(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!draftName.trim()) return;
    setCreating(true);
    try {
      await createPipeline({
        name: draftName,
        description: draftDescription || null,
        color: draftColor,
        stages: DEFAULT_STAGES.map((stage, index) => ({
          ...stage,
          position: index,
        })),
      });
      setDraftName("");
      setDraftDescription("");
      setDraftColor(null);
      setShowCreate(false);
      await refresh();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo crear el pipeline."));
    } finally {
      setCreating(false);
    }
  }

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
      <Link href="/" className="back-link">
        ← Volver al dashboard
      </Link>
      <section className="hero compact">
        <p className="eyebrow">CRM</p>
        <h1>Pipelines</h1>
        <p className="lead">
          Construye flujos de gestión de contactos con etapas
          reordenables. Cada contacto puede recorrer varios pipelines a la
          vez (ej: ventas + onboarding).
        </p>
        <div className="actions">
          <button
            type="button"
            className="button"
            onClick={() => setShowCreate((v) => !v)}
          >
            {showCreate ? "Cancelar" : "+ Nuevo pipeline"}
          </button>
        </div>
      </section>

      {showCreate ? (
        <section className="panel">
          <h2>Crear pipeline</h2>
          <form onSubmit={handleCreate} className="stacked-form">
            <label>
              <span>Nombre</span>
              <input
                type="text"
                required
                maxLength={100}
                value={draftName}
                onChange={(event) => setDraftName(event.target.value)}
              />
            </label>
            <label>
              <span>Descripción</span>
              <textarea
                rows={2}
                maxLength={2000}
                value={draftDescription}
                onChange={(event) => setDraftDescription(event.target.value)}
              />
            </label>
            <fieldset className="palette-fieldset">
              <legend>Color</legend>
              <div className="palette-grid" role="radiogroup">
                <button
                  type="button"
                  role="radio"
                  aria-checked={!draftColor}
                  title="Sin color"
                  className={`palette-swatch palette-swatch-empty${!draftColor ? " is-selected" : ""}`}
                  onClick={() => setDraftColor(null)}
                >
                  <span aria-hidden>∅</span>
                </button>
                {TAG_PALETTE.map((swatch) => {
                  const selected = draftColor === swatch.hex;
                  return (
                    <button
                      key={swatch.hex}
                      type="button"
                      role="radio"
                      aria-checked={selected}
                      title={swatch.label}
                      className={`palette-swatch${selected ? " is-selected" : ""}`}
                      style={{ background: swatch.hex }}
                      onClick={() => setDraftColor(swatch.hex)}
                    >
                      {selected ? <span aria-hidden>✓</span> : null}
                    </button>
                  );
                })}
              </div>
            </fieldset>
            <p className="muted small">
              Se crearán 7 etapas iniciales (Nuevo → Contactado → Cualificado
              → Propuesta → Negociación → Ganado / Perdido). Puedes editarlas
              después.
            </p>
            <div className="form-actions">
              <button type="submit" className="button" disabled={creating}>
                {creating ? "Creando…" : "Crear pipeline"}
              </button>
            </div>
          </form>
        </section>
      ) : null}

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
          <p className="muted">No hay pipelines todavía.</p>
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
    </main>
  );
}
