"use client";

import { useEffect, useState } from "react";
import { listPipelines, type Pipeline } from "../../lib/api";

type Props = {
  pipelineId: string | undefined;
  stageId: string | undefined;
  onChange: (pipelineId: string | undefined, stageId: string | undefined) => void;
  /** Si es true, "Pipeline" es opcional ("cualquiera") — útil en
   *  triggers donde el usuario puede dejar el filtro sin acotar. */
  allowEmpty?: boolean;
  pipelineLabel?: string;
  stageLabel?: string;
};

/** PR-Fixes-Pase-2 Bug C/E. Selector cascade de Pipeline → Stage
 *  reutilizable desde el editor de workflows (paso "Mover oportunidad"
 *  + trigger config de oportunidad). Carga pipelines del CRM la
 *  primera vez. Si solo hay 1 pipeline, lo selecciona automáticamente
 *  para que el usuario solo elija stage. */
export function PipelineStageSelector({
  pipelineId,
  stageId,
  onChange,
  allowEmpty = false,
  pipelineLabel = "Pipeline",
  stageLabel = "Stage",
}: Props) {
  const [pipelines, setPipelines] = useState<Pipeline[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    listPipelines()
      .then((rows) => {
        if (cancelled) return;
        setPipelines(rows);
        if (!pipelineId && rows.length === 1 && !allowEmpty) {
          // Atajo: 1 solo pipeline → preseleccionar para que el user
          // solo elija stage.
          onChange(rows[0].id, undefined);
        }
      })
      .catch(() => {
        if (!cancelled) setError("No se pudieron cargar los pipelines.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (loading) {
    return <p className="muted small">Cargando pipelines…</p>;
  }
  if (error) {
    return <p className="form-error small">{error}</p>;
  }
  if (pipelines.length === 0) {
    return (
      <p className="muted small">
        No hay pipelines configurados. Ve a{" "}
        <code>/pipelines</code> para crear uno antes de usar este paso.
      </p>
    );
  }

  const selectedPipeline = pipelines.find((p) => p.id === pipelineId);

  return (
    <>
      <label>
        {pipelineLabel}
        <select
          value={pipelineId ?? ""}
          onChange={(e) => onChange(e.target.value || undefined, undefined)}
        >
          {allowEmpty ? (
            <option value="">— Cualquier pipeline —</option>
          ) : (
            <option value="">— Selecciona —</option>
          )}
          {pipelines.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
      </label>
      {selectedPipeline ? (
        <label>
          {stageLabel}
          <select
            value={stageId ?? ""}
            onChange={(e) => onChange(pipelineId, e.target.value || undefined)}
          >
            {allowEmpty ? (
              <option value="">— Cualquier stage —</option>
            ) : (
              <option value="">— Selecciona —</option>
            )}
            {selectedPipeline.stages.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
                {s.is_won ? " (ganada)" : s.is_lost ? " (perdida)" : ""}
              </option>
            ))}
          </select>
        </label>
      ) : null}
    </>
  );
}
