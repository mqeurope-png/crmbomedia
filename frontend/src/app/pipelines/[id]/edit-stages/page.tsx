"use client";

import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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

/** PR-Hotfix-Pipelines-Use-Template (bug adicional UX form). Patrón
 *  form clásico: estado local con cambios pendientes + botones
 *  Guardar/Cancelar + beforeunload guard. Antes cada onChange/onBlur
 *  hacía PATCH/POST/DELETE inmediato — el operador no tenía cómo
 *  descartar cambios accidentales.
 *
 *  Las stages nuevas tienen id que empieza por "tmp:" hasta que el
 *  POST devuelve el id real. El `originalById` snapshot del primer
 *  load es la referencia para el diff y para Cancelar. */
type LocalStage = PipelineStage & { _new?: boolean };

const TMP_PREFIX = "tmp:";

function makeTempId(): string {
  // No usamos Date.now() porque el harness de algunos tests lo bloquea
  // — un contador local basta dado que solo conviven en memoria de la
  // página.
  return `${TMP_PREFIX}${Math.floor(Math.random() * 1e9)}`;
}

function stagesEqual(a: LocalStage[], b: LocalStage[]): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    if (a[i].id !== b[i].id) return false;
  }
  return true;
}

function diffStage(local: LocalStage, original: PipelineStage): Partial<PipelineStage> | null {
  const patch: Partial<PipelineStage> = {};
  if (local.name.trim() !== original.name) patch.name = local.name.trim();
  if ((local.color || null) !== (original.color || null)) patch.color = local.color || null;
  if ((local.target_days ?? null) !== (original.target_days ?? null)) {
    patch.target_days = local.target_days ?? null;
  }
  if (local.is_won !== original.is_won) patch.is_won = local.is_won;
  if (local.is_lost !== original.is_lost) patch.is_lost = local.is_lost;
  return Object.keys(patch).length > 0 ? patch : null;
}

export default function EditStagesPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const [pipeline, setPipeline] = useState<Pipeline | null>(null);
  const [stages, setStages] = useState<LocalStage[]>([]);
  const [originalById, setOriginalById] = useState<Record<string, PipelineStage>>(
    {},
  );
  const [originalOrder, setOriginalOrder] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const dragKey = useRef<string | null>(null);

  const snapshotPipeline = useCallback((fresh: Pipeline) => {
    const sorted = [...fresh.stages].sort((a, b) => a.position - b.position);
    setPipeline(fresh);
    setStages(sorted.map((s) => ({ ...s })));
    setOriginalById(Object.fromEntries(sorted.map((s) => [s.id, { ...s }])));
    setOriginalOrder(sorted.map((s) => s.id));
  }, []);

  const refresh = useCallback(async () => {
    setIsLoading(true);
    try {
      const fresh = await getPipeline(params.id);
      snapshotPipeline(fresh);
      setError(null);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo cargar el pipeline."));
    } finally {
      setIsLoading(false);
    }
  }, [params.id, snapshotPipeline]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Diff vs. snapshot. Si hay nuevas, borradas, cualquier campo
  // distinto, o el orden cambió → dirty.
  const isDirty = useMemo(() => {
    if (stages.some((s) => s._new)) return true;
    const currentIds = new Set(stages.map((s) => s.id));
    for (const origId of originalOrder) {
      if (!currentIds.has(origId)) return true; // borrada localmente
    }
    for (const s of stages) {
      if (s._new) continue;
      const orig = originalById[s.id];
      if (!orig) continue;
      if (diffStage(s, orig) !== null) return true;
    }
    const remainingOrder = stages
      .filter((s) => !s._new)
      .map((s) => s.id);
    if (remainingOrder.length !== originalOrder.length) return true;
    for (let i = 0; i < remainingOrder.length; i++) {
      if (remainingOrder[i] !== originalOrder[i]) return true;
    }
    return false;
  }, [stages, originalById, originalOrder]);

  // beforeunload — modal nativo del browser cuando hay cambios.
  useEffect(() => {
    if (!isDirty) return;
    const handler = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [isDirty]);

  function patchLocal(stageId: string, patch: Partial<LocalStage>) {
    setStages((current) =>
      current.map((s) => (s.id === stageId ? { ...s, ...patch } : s)),
    );
  }

  function handleAddStage() {
    const name = window.prompt("Nombre de la nueva etapa")?.trim();
    if (!name) return;
    const tempId = makeTempId();
    setStages((current) => [
      ...current,
      {
        id: tempId,
        pipeline_id: pipeline?.id ?? "",
        name,
        description: null,
        position: current.length,
        color: null,
        is_won: false,
        is_lost: false,
        target_days: null,
        created_at: "",
        updated_at: "",
        _new: true,
      },
    ]);
  }

  function handleRemove(stage: LocalStage) {
    if (
      !window.confirm(
        `¿Quitar la etapa "${stage.name}"? Solo se aplicará al pulsar "Guardar cambios".`,
      )
    )
      return;
    setStages((current) => current.filter((s) => s.id !== stage.id));
  }

  function handleCancel() {
    if (
      isDirty &&
      !window.confirm("Descartar los cambios sin guardar?")
    )
      return;
    if (pipeline) snapshotPipeline(pipeline);
  }

  async function handleSave() {
    if (!pipeline || isSaving) return;
    setIsSaving(true);
    setError(null);
    setToast(null);
    try {
      // 1. Borrar etapas eliminadas localmente (ids originales que ya
      // no están). Si alguna tiene contactos y el backend devuelve 400,
      // propagamos el error y dejamos el resto del save abortado para
      // que el operador elija destino.
      const currentIds = new Set(stages.map((s) => s.id));
      const toDelete = originalOrder.filter((id) => !currentIds.has(id));
      for (const id of toDelete) {
        await deletePipelineStage(id);
      }
      // 2. Añadir las nuevas. Mantengo mapping tempId → real id para
      // el reorder final.
      const idMap = new Map<string, string>();
      for (const s of stages) {
        if (!s._new) continue;
        const created = await addPipelineStage(pipeline.id, {
          name: s.name.trim() || "(sin nombre)",
          color: s.color,
          target_days: s.target_days,
          is_won: s.is_won,
          is_lost: s.is_lost,
        });
        idMap.set(s.id, created.id);
      }
      // 3. Actualizar las existentes con cambios.
      for (const s of stages) {
        if (s._new) continue;
        const orig = originalById[s.id];
        if (!orig) continue;
        const patch = diffStage(s, orig);
        if (patch) await updatePipelineStage(s.id, patch);
      }
      // 4. Reorder según el orden local (sustituyendo tempIds).
      const finalOrder = stages.map((s) => idMap.get(s.id) ?? s.id);
      const orderChanged =
        finalOrder.length !== originalOrder.length ||
        finalOrder.some((id, i) => id !== originalOrder[i]);
      if (orderChanged) {
        await reorderPipelineStages(pipeline.id, finalOrder);
      }
      await refresh();
      setToast("Cambios guardados.");
      window.setTimeout(() => setToast(null), 2500);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudieron guardar los cambios."));
    } finally {
      setIsSaving(false);
    }
  }

  if (isLoading) {
    return (
      <main className="shell">
        <p className="muted">Cargando…</p>
      </main>
    );
  }
  if (!pipeline) {
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
        description="Arrastra para reordenar; los cambios se aplican al pulsar Guardar."
        crumbs={[
          { label: "Pipelines", href: "/pipelines" },
          { label: pipeline.name, href: `/pipelines/${pipeline.id}` },
          { label: "Etapas" },
        ]}
        actions={
          <>
            <button
              type="button"
              className="button small"
              onClick={handleAddStage}
            >
              + Añadir etapa
            </button>
            <button
              type="button"
              className="button secondary small"
              onClick={() => {
                if (
                  isDirty &&
                  !window.confirm("Tienes cambios sin guardar. ¿Salir?")
                )
                  return;
                router.push(`/pipelines/${pipeline.id}`);
              }}
            >
              Volver al pipeline
            </button>
          </>
        }
      />

      {error ? <ErrorState title="Error" message={error} /> : null}
      {toast ? (
        <p className="muted" style={{ color: "#15803d" }}>
          {toast}
        </p>
      ) : null}

      {/* Toolbar Save/Cancel — sticky abajo cuando hay cambios. */}
      {isDirty ? (
        <div className="edit-stages-savebar">
          <span className="edit-stages-dirty-dot" aria-hidden /> Cambios sin
          guardar
          <div style={{ flex: 1 }} />
          <button
            type="button"
            className="button secondary small"
            onClick={handleCancel}
            disabled={isSaving}
          >
            Cancelar
          </button>
          <button
            type="button"
            className="button small"
            onClick={handleSave}
            disabled={isSaving}
          >
            {isSaving ? "Guardando…" : "Guardar cambios"}
          </button>
        </div>
      ) : null}

      <section className="panel">
        <ol className="stage-editor-list">
          {stages.map((stage) => {
            const isNew = stage._new;
            return (
              <li
                key={stage.id}
                className={`stage-editor-row${isNew ? " is-new" : ""}`}
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
                  setStages((current) => {
                    const next = [...current];
                    const sourceIndex = next.findIndex((s) => s.id === sourceId);
                    const targetIndex = next.findIndex((s) => s.id === stage.id);
                    if (sourceIndex < 0 || targetIndex < 0) return current;
                    const [moved] = next.splice(sourceIndex, 1);
                    next.splice(targetIndex, 0, moved);
                    return stagesEqual(next, current) ? current : next;
                  });
                }}
              >
                <span className="column-config-handle" aria-hidden>
                  ☰
                </span>
                <input
                  type="text"
                  value={stage.name}
                  onChange={(event) =>
                    patchLocal(stage.id, { name: event.target.value })
                  }
                  className="stage-name-input"
                />
                <select
                  value={stage.color || ""}
                  onChange={(event) =>
                    patchLocal(stage.id, {
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
                    patchLocal(stage.id, {
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
                      patchLocal(stage.id, { is_won: event.target.checked })
                    }
                  />
                  <span>Ganado</span>
                </label>
                <label className="checkbox">
                  <input
                    type="checkbox"
                    checked={stage.is_lost}
                    onChange={(event) =>
                      patchLocal(stage.id, { is_lost: event.target.checked })
                    }
                  />
                  <span>Perdido</span>
                </label>
                {isNew ? (
                  <span className="rv-badge rv-badge-sm rv-badge-mine">
                    Nueva
                  </span>
                ) : null}
                <button
                  type="button"
                  className="button secondary small"
                  onClick={() => handleRemove(stage)}
                >
                  Quitar
                </button>
              </li>
            );
          })}
        </ol>
      </section>
    </main>
  );
}
