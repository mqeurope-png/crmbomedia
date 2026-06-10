"use client";

import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { PageHeader } from "../../../components/PageHeader";
import { ErrorState } from "../../../components/ErrorState";
import {
  addPipelineStage,
  deletePipelineStage,
  getPipeline,
  reorderPipelineStages,
  updatePipelineStage,
  type Pipeline,
  type PipelineStage,
} from "../../../lib/api";
import { extractErrorMessage } from "../../../lib/errors";
import { TAG_PALETTE } from "../../../lib/tagPalette";

export default function EditStagesPage() {
  const params = useParams<{ id: string }>();
  const [pipeline, setPipeline] = useState<Pipeline | null>(null);
  const [stages, setStages] = useState<PipelineStage[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const dragKey = useRef<string | null>(null);

  const refresh = useCallback(async () => {
    setIsLoading(true);
    try {
      const fresh = await getPipeline(params.id);
      setPipeline(fresh);
      setStages([...fresh.stages].sort((a, b) => a.position - b.position));
      setError(null);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo cargar el pipeline."));
    } finally {
      setIsLoading(false);
    }
  }, [params.id]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function persistOrder(order: PipelineStage[]) {
    if (!pipeline) return;
    setStages(order);
    try {
      await reorderPipelineStages(
        pipeline.id,
        order.map((stage) => stage.id),
      );
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo guardar el orden."));
      await refresh();
    }
  }

  async function handleAddStage() {
    if (!pipeline) return;
    const name = window.prompt("Nombre de la nueva etapa")?.trim();
    if (!name) return;
    try {
      await addPipelineStage(pipeline.id, { name });
      await refresh();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo añadir la etapa."));
    }
  }

  async function handleUpdate(stage: PipelineStage, patch: Partial<PipelineStage>) {
    try {
      await updatePipelineStage(stage.id, patch);
      await refresh();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo guardar la etapa."));
    }
  }

  async function handleDelete(stage: PipelineStage) {
    if (!pipeline) return;
    const targetId = window.prompt(
      `Borrar "${stage.name}". Si tiene contactos, indica la id de la etapa destino:`,
      "",
    );
    try {
      await deletePipelineStage(stage.id, targetId?.trim() || undefined);
      await refresh();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo borrar la etapa."));
    }
  }

  if (isLoading) {
    return (
      <main className="shell">
        <p className="muted">Cargando…</p>
      </main>
    );
  }
  if (error || !pipeline) {
    return (
      <main className="shell narrow">
        <PageHeader
          title="Etapas"
          crumbs={[{ label: "Pipelines", href: "/pipelines" }]}
        />
        <ErrorState
          title="No se pudo cargar el pipeline"
          message={error ?? "Pipeline no encontrado"}
        />
      </main>
    );
  }

  return (
    <main className="shell shell-wide">
      <PageHeader
        title={pipeline.name}
        eyebrow="Etapas"
        description="Arrastra para reordenar; click en el nombre o el color para editar."
        crumbs={[
          { label: "Pipelines", href: "/pipelines" },
          { label: pipeline.name, href: `/pipelines/${pipeline.id}` },
          { label: "Etapas" },
        ]}
        actions={
          <button type="button" className="button small" onClick={handleAddStage}>
            + Añadir etapa
          </button>
        }
      />

      {error ? <ErrorState title="Error" message={error} /> : null}

      <section className="panel">
        <ol className="stage-editor-list">
          {stages.map((stage) => (
            <li
              key={stage.id}
              className="stage-editor-row"
              draggable
              onDragStart={() => {
                dragKey.current = stage.id;
              }}
              onDragOver={(event) => event.preventDefault()}
              onDrop={(event) => {
                event.preventDefault();
                const sourceId = dragKey.current;
                dragKey.current = null;
                if (!sourceId || sourceId === stage.id) return;
                const next = [...stages];
                const sourceIndex = next.findIndex((s) => s.id === sourceId);
                const targetIndex = next.findIndex((s) => s.id === stage.id);
                if (sourceIndex < 0 || targetIndex < 0) return;
                const [moved] = next.splice(sourceIndex, 1);
                next.splice(targetIndex, 0, moved);
                persistOrder(next);
              }}
            >
              <span className="column-config-handle" aria-hidden>
                ☰
              </span>
              <input
                type="text"
                value={stage.name}
                onChange={(event) => {
                  const value = event.target.value;
                  setStages((current) =>
                    current.map((s) =>
                      s.id === stage.id ? { ...s, name: value } : s,
                    ),
                  );
                }}
                onBlur={(event) => {
                  if (event.target.value.trim() !== stage.name) {
                    handleUpdate(stage, { name: event.target.value.trim() });
                  }
                }}
                className="stage-name-input"
              />
              <select
                value={stage.color || ""}
                onChange={(event) =>
                  handleUpdate(stage, {
                    color: event.target.value || null,
                  })
                }
              >
                <option value="">Sin color</option>
                {TAG_PALETTE.map((swatch) => (
                  <option key={swatch.hex} value={swatch.hex}>
                    {swatch.label}
                  </option>
                ))}
              </select>
              <input
                type="number"
                min={0}
                placeholder="target d"
                value={stage.target_days ?? ""}
                onChange={(event) => {
                  const raw = event.target.value;
                  handleUpdate(stage, {
                    target_days: raw ? Number(raw) : null,
                  });
                }}
                className="stage-target-input"
              />
              <label className="checkbox">
                <input
                  type="checkbox"
                  checked={stage.is_won}
                  onChange={(event) =>
                    handleUpdate(stage, { is_won: event.target.checked })
                  }
                />
                <span>Ganado</span>
              </label>
              <label className="checkbox">
                <input
                  type="checkbox"
                  checked={stage.is_lost}
                  onChange={(event) =>
                    handleUpdate(stage, { is_lost: event.target.checked })
                  }
                />
                <span>Perdido</span>
              </label>
              <button
                type="button"
                className="button secondary small"
                onClick={() => handleDelete(stage)}
              >
                Borrar
              </button>
            </li>
          ))}
        </ol>
      </section>
    </main>
  );
}
