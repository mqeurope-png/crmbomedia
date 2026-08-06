"use client";

/** Sprint Ficha 360 — Bloque 1: modal "Registrar llamada".
 * Reemplaza al comportamiento anterior del botón (abría el creador de
 * tareas por error). Incluye acciones post-llamada colapsables y, como
 * Bloque 3, el disparo de workflows manuales. */
import { useEffect, useState } from "react";
import { getUsers, listPipelines, type Pipeline, type User } from "../../lib/api";
import {
  CALL_RESULTS,
  DURATION_BUCKETS,
  createCallLog,
  listManualWorkflows,
  runManualWorkflow,
  type CallResultCode,
  type ManualWorkflow,
} from "../../lib/callsApi";
import { extractErrorMessage } from "../../lib/errors";
import { StarRating } from "../StarRating";

type Props = {
  contactId: string;
  open: boolean;
  onClose: () => void;
  onSaved: () => void;
  /** Si el user marcó "Enviar email de seguimiento", se llama tras
   *  guardar para abrir el composer con el contacto precargado. */
  onRequestCompose: () => void;
  /** CRM-1: valoración actual del contacto, para mostrarla en la acción
   *  «Ajustar star score». `null`/0 = sin valorar. */
  currentStarRating?: number | null;
};

export function RegisterCallModal({
  contactId, open, onClose, onSaved, onRequestCompose,
  currentStarRating = null,
}: Props) {
  const [result, setResult] = useState<CallResultCode>("contacted");
  const [custom, setCustom] = useState("");
  const [duration, setDuration] = useState<string | null>(null);
  const [subject, setSubject] = useState("");
  const [notes, setNotes] = useState("");
  const [calledAt, setCalledAt] = useState("");
  const [showActions, setShowActions] = useState(false);
  const [doPipeline, setDoPipeline] = useState(false);
  const [pipelines, setPipelines] = useState<Pipeline[]>([]);
  const [pipelineId, setPipelineId] = useState("");
  const [stageId, setStageId] = useState("");
  const [doScore, setDoScore] = useState(false);
  const [scoreDelta, setScoreDelta] = useState(10);
  const [doStar, setDoStar] = useState(false);
  const [starValue, setStarValue] = useState(0);
  const [doTask, setDoTask] = useState(false);
  const [taskTitle, setTaskTitle] = useState("");
  const [taskDue, setTaskDue] = useState("");
  const [taskAssignee, setTaskAssignee] = useState("");
  const [users, setUsers] = useState<User[]>([]);
  const [doEmail, setDoEmail] = useState(false);
  const [doWorkflows, setDoWorkflows] = useState(false);
  const [manualWfs, setManualWfs] = useState<ManualWorkflow[]>([]);
  const [selectedWfs, setSelectedWfs] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    // Datos de los sub-formularios, cargados al abrir.
    listPipelines().then(setPipelines).catch(() => undefined);
    getUsers().then((rows) => setUsers(rows.filter((u) => u.is_active))).catch(() => undefined);
    listManualWorkflows().then(setManualWfs).catch(() => setManualWfs([]));
    const now = new Date();
    now.setSeconds(0, 0);
    setCalledAt(new Date(now.getTime() - now.getTimezoneOffset() * 60000)
      .toISOString().slice(0, 16));
    // Arranca el selector en la valoración actual del contacto.
    setStarValue(currentStarRating ?? 0);
  }, [open, currentStarRating]);

  if (!open) return null;

  const stages = pipelines.find((p) => p.id === pipelineId)?.stages ?? [];

  async function handleSave() {
    if (result === "other" && !custom.trim()) {
      setError("Indica el resultado en el campo libre (obligatorio con «Otro»).");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await createCallLog(contactId, {
        result_code: result,
        result_custom: result === "other" ? custom.trim() : null,
        subject: subject.trim() || null,
        notes: notes.trim() || null,
        duration_bucket: duration,
        called_at: calledAt ? new Date(calledAt).toISOString() : null,
        actions: {
          pipeline_change: doPipeline && pipelineId
            ? { pipeline_id: pipelineId, stage_id: stageId || null }
            : null,
          lead_score_delta: doScore ? scoreDelta : null,
          adjust_star_score: doStar && starValue >= 1 ? starValue : null,
          follow_up_task: doTask && taskTitle.trim()
            ? {
                title: taskTitle.trim(),
                due_at: taskDue ? new Date(taskDue).toISOString() : null,
                assigned_to_user_id: taskAssignee || null,
              }
            : null,
          trigger_workflow_ids: doWorkflows ? selectedWfs : [],
        },
      });
      onSaved();
      onClose();
      if (doEmail) onRequestCompose();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo registrar la llamada."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-overlay" role="dialog" aria-modal="true">
      {/* PR-Hotfix-Ficha-360 Bug 1: la caja del modal es `.modal-dialog`
        * (patrón de TaskModal/TemplatePicker) — la clase `.modal` no
        * existe en styles.css y los inputs flotaban sobre el backdrop. */}
      <div className="modal-dialog register-call-modal">
        <header className="modal-header">
          <h2>📞 Registrar llamada</h2>
          <button type="button" className="modal-close" onClick={onClose}>×</button>
        </header>
        <div className="modal-body">
          {error ? <p className="form-error">{error}</p> : null}
          <label>
            Resultado *
            <select value={result} onChange={(e) => setResult(e.target.value as CallResultCode)}>
              {CALL_RESULTS.map((r) => (
                <option key={r.code} value={r.code}>{r.icon} {r.label}</option>
              ))}
            </select>
          </label>
          {result === "other" ? (
            <label>
              Especifica ({custom.length}/150)
              <input type="text" maxLength={150} value={custom}
                onChange={(e) => setCustom(e.target.value)} />
            </label>
          ) : null}
          <fieldset className="call-duration">
            <legend>Duración</legend>
            {DURATION_BUCKETS.map((d) => (
              <label key={d.code} className="checkbox-inline small">
                <input type="radio" name="duration" checked={duration === d.code}
                  onChange={() => setDuration(d.code)} /> {d.label}
              </label>
            ))}
          </fieldset>
          <label>
            Asunto
            <input type="text" maxLength={255} value={subject}
              onChange={(e) => setSubject(e.target.value)} />
          </label>
          <label>
            Notas
            <textarea rows={3} value={notes} onChange={(e) => setNotes(e.target.value)} />
          </label>
          <label>
            Fecha
            <input type="datetime-local" value={calledAt}
              onChange={(e) => setCalledAt(e.target.value)} />
          </label>

          <button type="button" className="link-button"
            onClick={() => setShowActions(!showActions)}>
            {showActions ? "▼" : "▶"} Acciones tras la llamada
          </button>
          {showActions ? (
            <div className="call-actions">
              <label className="checkbox-inline">
                <input type="checkbox" checked={doPipeline}
                  onChange={(e) => setDoPipeline(e.target.checked)} />
                Cambiar pipeline/stage
              </label>
              {doPipeline ? (
                <div className="call-subform">
                  <select value={pipelineId} onChange={(e) => { setPipelineId(e.target.value); setStageId(""); }}>
                    <option value="">— Pipeline —</option>
                    {pipelines.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                  </select>
                  <select value={stageId} onChange={(e) => setStageId(e.target.value)}>
                    <option value="">— Stage inicial —</option>
                    {stages.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                  </select>
                </div>
              ) : null}
              <label className="checkbox-inline">
                <input type="checkbox" checked={doScore}
                  onChange={(e) => setDoScore(e.target.checked)} />
                Ajustar lead score
              </label>
              {doScore ? (
                <div className="call-subform">
                  <button type="button" className="button small secondary" onClick={() => setScoreDelta(-10)}>-10</button>
                  <button type="button" className="button small secondary" onClick={() => setScoreDelta(10)}>+10</button>
                  <input type="number" value={scoreDelta}
                    onChange={(e) => setScoreDelta(Number(e.target.value) || 0)} />
                </div>
              ) : null}
              <label className="checkbox-inline">
                <input type="checkbox" checked={doStar}
                  onChange={(e) => setDoStar(e.target.checked)} />
                Ajustar star score
              </label>
              {doStar ? (
                <div className="call-subform call-star-action">
                  <span className="muted small">
                    Actual:{" "}
                    {currentStarRating
                      ? <StarRating value={currentStarRating} size="sm"
                                    ariaLabel="Star score actual" />
                      : "sin valorar"}
                  </span>
                  <span className="muted small">Nuevo:</span>
                  <StarRating value={starValue} editable size="md"
                    ariaLabel="Nuevo star score"
                    onChange={(v) => setStarValue(v)} />
                </div>
              ) : null}
              <label className="checkbox-inline">
                <input type="checkbox" checked={doTask}
                  onChange={(e) => setDoTask(e.target.checked)} />
                Crear tarea de rellamada
              </label>
              {doTask ? (
                <div className="call-subform">
                  <input type="text" placeholder="Título" value={taskTitle}
                    onChange={(e) => setTaskTitle(e.target.value)} />
                  <input type="datetime-local" value={taskDue}
                    onChange={(e) => setTaskDue(e.target.value)} />
                  <select value={taskAssignee} onChange={(e) => setTaskAssignee(e.target.value)}>
                    <option value="">Yo</option>
                    {users.map((u) => <option key={u.id} value={u.id}>{u.full_name || u.email}</option>)}
                  </select>
                </div>
              ) : null}
              <label className="checkbox-inline">
                <input type="checkbox" checked={doEmail}
                  onChange={(e) => setDoEmail(e.target.checked)} />
                Enviar email de seguimiento (abre el compositor al guardar)
              </label>
              <label className="checkbox-inline">
                <input type="checkbox" checked={doWorkflows}
                  onChange={(e) => setDoWorkflows(e.target.checked)} />
                Añadir a workflow
              </label>
              {doWorkflows ? (
                <div className="call-subform">
                  {manualWfs.length === 0 ? (
                    <p className="muted small">
                      No hay workflows con trigger «Ejecución manual» disponibles.
                    </p>
                  ) : manualWfs.map((wf) => (
                    <label key={wf.id} className="checkbox-inline small">
                      <input type="checkbox"
                        checked={selectedWfs.includes(wf.id)}
                        onChange={(e) => setSelectedWfs(
                          e.target.checked
                            ? [...selectedWfs, wf.id]
                            : selectedWfs.filter((id) => id !== wf.id),
                        )} />
                      {wf.name}{wf.owner_name ? ` (${wf.owner_name})` : ""}
                    </label>
                  ))}
                </div>
              ) : null}
            </div>
          ) : null}
          <footer className="modal-footer">
            <button type="button" className="button secondary" onClick={onClose} disabled={busy}>
              Cancelar
            </button>
            <button type="button" className="button" onClick={handleSave} disabled={busy}>
              💾 {busy ? "Guardando…" : "Guardar"}
            </button>
          </footer>
        </div>
      </div>
    </div>
  );
}

/** Bloque 3 — botón/menú "Ejecutar workflow" para la cabecera de ficha. */
export function RunWorkflowMenu({
  contactId, open, onClose, onRan,
}: {
  contactId: string;
  open: boolean;
  onClose: () => void;
  onRan: () => void;
}) {
  const [wfs, setWfs] = useState<ManualWorkflow[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) listManualWorkflows().then(setWfs).catch(() => setWfs([]));
  }, [open]);

  if (!open) return null;
  return (
    <div className="modal-overlay" role="dialog" aria-modal="true">
      {/* PR-Hotfix-Ficha-360 Bug 1: `.modal-dialog` = caja real. */}
      <div className="modal-dialog small run-workflow-modal">
        <header className="modal-header">
          <h2>⚡ Ejecutar workflow</h2>
          <button type="button" className="modal-close" onClick={onClose}>×</button>
        </header>
        <div className="modal-body">
          {error ? <p className="form-error">{error}</p> : null}
          {wfs.length === 0 ? (
            <p className="muted small">
              No tienes workflows manuales configurados. Crea uno en
              /workflows con trigger «Ejecución manual».
            </p>
          ) : (
            <ul className="bulk-bar-options">
              {wfs.map((wf) => (
                <li key={wf.id}>
                  <button type="button" className="button small secondary" disabled={busy}
                    onClick={async () => {
                      setBusy(true);
                      setError(null);
                      try {
                        await runManualWorkflow(contactId, wf.id);
                        onRan();
                        onClose();
                      } catch (err) {
                        setError(extractErrorMessage(err, "No se pudo ejecutar."));
                      } finally {
                        setBusy(false);
                      }
                    }}>
                    {wf.name}{wf.owner_name ? ` (${wf.owner_name})` : ""}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}
