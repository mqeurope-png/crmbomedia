"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { ErrorState } from "../../components/ErrorState";
import { getPipeline, type Pipeline } from "../../lib/api";
import { extractErrorMessage } from "../../lib/errors";

/**
 * Stub overview for Sprint P.2 PR-A. The kanban with drag-and-drop
 * lands in PR-B; for now this page just confirms the pipeline + its
 * stages and links to the stage editor.
 */
export default function PipelineDetailPage() {
  const params = useParams<{ id: string }>();
  const [pipeline, setPipeline] = useState<Pipeline | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    getPipeline(params.id)
      .then(setPipeline)
      .catch((err) => setError(extractErrorMessage(err, "No se pudo cargar el pipeline.")))
      .finally(() => setIsLoading(false));
  }, [params.id]);

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
        <Link href="/pipelines" className="back-link">← Pipelines</Link>
        <ErrorState
          title="No se pudo cargar el pipeline"
          message={error ?? "Pipeline no encontrado"}
        />
      </main>
    );
  }

  return (
    <main className="shell shell-wide">
      <Link href="/pipelines" className="back-link">← Pipelines</Link>
      <section className="hero compact">
        <p className="eyebrow">Pipeline</p>
        <h1>{pipeline.name}</h1>
        {pipeline.description ? (
          <p className="lead">{pipeline.description}</p>
        ) : null}
        <div className="actions">
          <Link
            href={`/pipelines/${pipeline.id}/edit-stages`}
            className="button"
          >
            Editar etapas
          </Link>
        </div>
      </section>

      <section className="panel">
        <h2>Etapas</h2>
        <p className="muted">
          La vista kanban con drag-and-drop llega en el próximo PR. De
          momento puedes inspeccionar y editar las etapas:
        </p>
        <ol className="pipeline-stage-summary">
          {pipeline.stages.map((stage) => (
            <li key={stage.id}>
              <span
                className="tag-color-swatch"
                style={{ background: stage.color || pipeline.color || "#cdd5e1" }}
                aria-hidden
              />
              <span>{stage.name}</span>
              {stage.is_won ? <span className="status status-done">Ganado</span> : null}
              {stage.is_lost ? <span className="status status-denied">Perdido</span> : null}
              {stage.target_days ? (
                <span className="muted small">target {stage.target_days}d</span>
              ) : null}
            </li>
          ))}
        </ol>
      </section>
    </main>
  );
}
