"use client";

import { useEffect, useState } from "react";
import { listPipelines, type Pipeline } from "../lib/api";
import { extractErrorMessage } from "../lib/errors";

type Props = {
  open: boolean;
  /** Pipelines the contact is already in — disabled in the dropdown
   *  so the operator can't add the same row twice. */
  excludePipelineIds: string[];
  onSubmit: (
    pipelineId: string,
    stageId: string | undefined,
  ) => Promise<void> | void;
  onClose: () => void;
};

export function AddToPipelineModal({
  open,
  excludePipelineIds,
  onSubmit,
  onClose,
}: Props) {
  const [pipelines, setPipelines] = useState<Pipeline[]>([]);
  const [pipelineId, setPipelineId] = useState<string>("");
  const [stageId, setStageId] = useState<string>("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setError(null);
    setPipelineId("");
    setStageId("");
    listPipelines()
      .then(setPipelines)
      .catch((err) =>
        setError(
          extractErrorMessage(err, "No se pudo cargar la lista de pipelines."),
        ),
      );
  }, [open]);

  if (!open) return null;

  const excluded = new Set(excludePipelineIds);
  const selectedPipeline = pipelines.find((p) => p.id === pipelineId);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!pipelineId) {
      setError("Selecciona un pipeline.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await onSubmit(pipelineId, stageId || undefined);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo añadir al pipeline."));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="modal-overlay" role="dialog" aria-modal>
      <form onSubmit={handleSubmit} className="modal-card">
        <h2>Añadir a pipeline</h2>
        <label>
          <span>Pipeline</span>
          <select
            required
            value={pipelineId}
            onChange={(event) => {
              setPipelineId(event.target.value);
              setStageId("");
            }}
          >
            <option value="">Selecciona…</option>
            {pipelines.map((pipeline) => (
              <option
                key={pipeline.id}
                value={pipeline.id}
                disabled={excluded.has(pipeline.id)}
              >
                {pipeline.name}
                {excluded.has(pipeline.id) ? " (ya añadido)" : ""}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>Etapa inicial</span>
          <select
            value={stageId}
            onChange={(event) => setStageId(event.target.value)}
            disabled={!selectedPipeline}
          >
            <option value="">Primera etapa por defecto</option>
            {selectedPipeline?.stages.map((stage) => (
              <option key={stage.id} value={stage.id}>
                {stage.name}
              </option>
            ))}
          </select>
        </label>
        {error ? <p className="danger-text">{error}</p> : null}
        <div className="form-actions">
          <button type="submit" className="button" disabled={submitting}>
            {submitting ? "Añadiendo…" : "Añadir"}
          </button>
          <button
            type="button"
            className="button secondary"
            onClick={onClose}
            disabled={submitting}
          >
            Cancelar
          </button>
        </div>
      </form>
    </div>
  );
}
