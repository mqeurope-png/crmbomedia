"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import {
  addContactToPipeline,
  archivePipelineAssignment,
  getPipeline,
  listContactPipelines,
  moveContactToStage,
  type ContactPipelineSummary,
} from "../lib/api";
import { extractErrorMessage } from "../lib/errors";
import { AddToPipelineModal } from "./AddToPipelineModal";

type Props = {
  contactId: string;
};

/**
 * Contact-detail card showing every pipeline this contact lives in
 * plus a quick stage selector per row. Single fetch on mount via
 * `listContactPipelines`; the dropdown loads the stage list on
 * demand so a contact in 5 pipelines doesn't trigger 5 secondary
 * fetches on render.
 */
export function ContactPipelinesSection({ contactId }: Props) {
  const [rows, setRows] = useState<ContactPipelineSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [modalOpen, setModalOpen] = useState(false);

  const refresh = useCallback(async () => {
    setIsLoading(true);
    try {
      setRows(await listContactPipelines(contactId));
      setError(null);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudieron cargar los pipelines."));
    } finally {
      setIsLoading(false);
    }
  }, [contactId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function handleAdd(
    pipelineId: string,
    stageId: string | undefined,
  ) {
    await addContactToPipeline(contactId, {
      pipeline_id: pipelineId,
      stage_id: stageId,
    });
    setModalOpen(false);
    await refresh();
  }

  async function handleArchive(row: ContactPipelineSummary) {
    if (
      !window.confirm(`¿Sacar al contacto del pipeline "${row.pipeline_name}"?`)
    )
      return;
    try {
      await archivePipelineAssignment(row.assignment_id);
      await refresh();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo archivar."));
    }
  }

  return (
    <article className="card card-wide">
      <div className="section-title">
        <h2>Pipelines</h2>
        <button
          type="button"
          className="button secondary small"
          onClick={() => setModalOpen(true)}
        >
          + Añadir
        </button>
      </div>
      {error ? <p className="danger-text">{error}</p> : null}
      {isLoading && rows.length === 0 ? (
        <p className="muted">Cargando…</p>
      ) : rows.length === 0 ? (
        <p className="muted">El contacto no está en ningún pipeline.</p>
      ) : (
        <ul className="contact-pipeline-list">
          {rows.map((row) => (
            <ContactPipelineRow
              key={row.assignment_id}
              row={row}
              onChange={refresh}
              onError={setError}
              onArchive={() => handleArchive(row)}
            />
          ))}
        </ul>
      )}
      <AddToPipelineModal
        open={modalOpen}
        excludePipelineIds={rows.map((row) => row.pipeline_id)}
        onSubmit={handleAdd}
        onClose={() => setModalOpen(false)}
      />
    </article>
  );
}

function ContactPipelineRow({
  row,
  onChange,
  onError,
  onArchive,
}: {
  row: ContactPipelineSummary;
  onChange: () => Promise<void> | void;
  onError: (message: string) => void;
  onArchive: () => void;
}) {
  const [stages, setStages] = useState<{ id: string; name: string }[] | null>(
    null,
  );
  const [pending, setPending] = useState(false);

  async function ensureStagesLoaded() {
    if (stages !== null) return;
    try {
      const pipeline = await getPipeline(row.pipeline_id);
      setStages(
        pipeline.stages
          .sort((a, b) => a.position - b.position)
          .map((stage) => ({ id: stage.id, name: stage.name })),
      );
    } catch (err) {
      onError(extractErrorMessage(err, "No se pudieron cargar las etapas."));
    }
  }

  async function handleChange(stageId: string) {
    if (stageId === row.stage_id) return;
    setPending(true);
    try {
      await moveContactToStage(row.assignment_id, { stage_id: stageId });
      await onChange();
    } catch (err) {
      onError(extractErrorMessage(err, "No se pudo cambiar de etapa."));
    } finally {
      setPending(false);
    }
  }

  const stageOptions = stages ?? [
    { id: row.stage_id, name: row.stage_name },
  ];
  return (
    <li className="contact-pipeline-row">
      <div className="contact-pipeline-meta">
        <Link href={`/pipelines/${row.pipeline_id}`}>
          <strong>{row.pipeline_name}</strong>
        </Link>
        <span className="muted small">{row.days_in_stage} días en etapa</span>
      </div>
      <select
        value={row.stage_id}
        onClick={ensureStagesLoaded}
        onFocus={ensureStagesLoaded}
        onChange={(event) => handleChange(event.target.value)}
        disabled={pending}
      >
        {stageOptions.map((stage) => (
          <option key={stage.id} value={stage.id}>
            {stage.name}
          </option>
        ))}
      </select>
      {row.is_won ? (
        <span className="status status-done">Ganado</span>
      ) : row.is_lost ? (
        <span className="status status-denied">Perdido</span>
      ) : null}
      <button
        type="button"
        className="button secondary small"
        onClick={onArchive}
      >
        Sacar
      </button>
    </li>
  );
}
