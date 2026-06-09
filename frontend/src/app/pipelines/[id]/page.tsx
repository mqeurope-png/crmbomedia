"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { ErrorState } from "../../components/ErrorState";
import { PipelineKanban } from "../../components/PipelineKanban";
import {
  listPipelineContacts,
  type PipelineContactsResponse,
} from "../../lib/api";
import { extractErrorMessage } from "../../lib/errors";

export default function PipelineDetailPage() {
  const params = useParams<{ id: string }>();
  const [snapshot, setSnapshot] = useState<PipelineContactsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const refresh = useCallback(async () => {
    setIsLoading(true);
    try {
      const data = await listPipelineContacts(params.id);
      setSnapshot(data);
      setError(null);
    } catch (err) {
      setError(
        extractErrorMessage(err, "No se pudo cargar el pipeline."),
      );
    } finally {
      setIsLoading(false);
    }
  }, [params.id]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  if (isLoading && !snapshot) {
    return (
      <main className="shell shell-wide">
        <p className="muted">Cargando…</p>
      </main>
    );
  }
  if (error || !snapshot) {
    return (
      <main className="shell narrow">
        <Link href="/pipelines" className="back-link">
          ← Pipelines
        </Link>
        <ErrorState
          title="No se pudo cargar el pipeline"
          message={error ?? "Pipeline no encontrado"}
        />
      </main>
    );
  }

  const { pipeline } = snapshot;
  return (
    <main className="shell shell-wide">
      <Link href="/pipelines" className="back-link">
        ← Pipelines
      </Link>
      <section className="hero compact">
        <p className="eyebrow">Pipeline</p>
        <h1>{pipeline.name}</h1>
        {pipeline.description ? (
          <p className="lead">{pipeline.description}</p>
        ) : null}
        <div className="actions">
          <Link
            href={`/pipelines/${pipeline.id}/edit-stages`}
            className="button secondary"
          >
            Editar etapas
          </Link>
          <button
            type="button"
            className="button secondary"
            onClick={refresh}
          >
            Refrescar
          </button>
        </div>
      </section>

      {error ? <ErrorState title="Error" message={error} /> : null}

      <PipelineKanban data={snapshot} onError={setError} />
    </main>
  );
}
