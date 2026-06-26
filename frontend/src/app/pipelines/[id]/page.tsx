"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { ErrorState } from "../../components/ErrorState";
import { PageHeader } from "../../components/PageHeader";
import { PipelineKanban } from "../../components/PipelineKanban";
import { ResourceVisibilityBadge } from "../../components/ResourceVisibilityBadge";
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
        <PageHeader
          title="Pipeline"
          crumbs={[{ label: "Pipelines", href: "/pipelines" }]}
        />
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
      <PageHeader
        title={pipeline.name}
        eyebrow="Pipeline"
        description={pipeline.description ?? undefined}
        crumbs={[
          { label: "Pipelines", href: "/pipelines" },
          { label: pipeline.name },
        ]}
        actions={
          <>
            <Link
              href={`/pipelines/${pipeline.id}/report`}
              className="button small"
            >
              Reporte
            </Link>
            <Link
              href={`/pipelines/${pipeline.id}/edit-stages`}
              className="button secondary small"
            >
              Editar etapas
            </Link>
            <button
              type="button"
              className="button secondary small"
              onClick={refresh}
            >
              Refrescar
            </button>
          </>
        }
      />

      {/* PR-Frontend-Workflows-Pipelines-Templates. Badge debajo del
          header — el `PageHeader` solo acepta texto en `eyebrow`. */}
      <div style={{ marginTop: -8, marginBottom: 12 }}>
        <ResourceVisibilityBadge
          isMine={!!pipeline.is_mine}
          isGlobal={!!pipeline.is_global}
        />
      </div>

      {error ? <ErrorState title="Error" message={error} /> : null}

      <PipelineKanban data={snapshot} onError={setError} />
    </main>
  );
}
