"use client";

// Sprint-Backfill-Gmail. UI 3 pasos:
//
//  1. "Estimar espacio" → POST /estimate, devuelve job_id.
//  2. Poll /backfill/{id} hasta status terminal. El job de estimate
//     pinta el desglose: total_emails, MB de adjuntos, per_user.
//  3. Admin elige `incluir adjuntos` + `tamaño máximo`, click
//     "Confirmar y ejecutar" → POST /execute, otro job poll, progress
//     bar con total_processed / total_estimated.
//
// El usuario puede cancelar un execute en marcha (POST /cancel) y la
// UI muestra el estado terminal correspondiente.

import { History, Play, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { extractErrorMessage } from "../lib/errors";
import {
  cancelGmailBackfill,
  getGmailBackfillStatus,
  listGmailBackfillJobs,
  triggerGmailBackfillEstimate,
  triggerGmailBackfillExecute,
  type GmailBackfillAliasesScope,
  type GmailBackfillEstimateResult,
  type GmailBackfillJobRead,
} from "../lib/gmailBackfillApi";

type Props = {
  onError: (message: string | null) => void;
  onMessage: (message: string | null) => void;
};

const POLL_MS = 5000;

const ACTIVE_STATUSES = new Set(["queued", "running", "cancelling"]);

export function GmailBackfillSection({ onError, onMessage }: Props) {
  const [estimateJob, setEstimateJob] = useState<GmailBackfillJobRead | null>(null);
  const [executeJob, setExecuteJob] = useState<GmailBackfillJobRead | null>(null);
  const [busy, setBusy] = useState(false);
  const [monthsBack, setMonthsBack] = useState(36);
  const [includeAttachments, setIncludeAttachments] = useState(true);
  const [maxAttachmentMb, setMaxAttachmentMb] = useState(25);
  const [aliasesScope, setAliasesScope] =
    useState<GmailBackfillAliasesScope>("primary_only");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const stopPolling = useCallback(() => {
    if (timer.current) {
      clearTimeout(timer.current);
      timer.current = null;
    }
  }, []);

  useEffect(() => stopPolling, [stopPolling]);

  const pollUntilDone = useCallback(
    async (jobId: string, setter: (j: GmailBackfillJobRead) => void) => {
      stopPolling();
      const tick = async () => {
        try {
          const j = await getGmailBackfillStatus(jobId);
          setter(j);
          if (ACTIVE_STATUSES.has(j.status)) {
            timer.current = setTimeout(tick, POLL_MS);
          }
        } catch (err) {
          onError(
            extractErrorMessage(err, "No se pudo cargar el estado del backfill."),
          );
        }
      };
      await tick();
    },
    [onError, stopPolling],
  );

  // PR-Fix-Backfill-Gmail-Cero-Importados. Hidratación al montar: si
  // existe un estimate o execute en marcha (cualquier admin), lo
  // adoptamos y resumimos el polling. Antes el operador no veía un
  // estimate iniciado en otra sesión y arrancaba uno nuevo encima.
  useEffect(() => {
    let cancelled = false;
    listGmailBackfillJobs(10)
      .then((jobs) => {
        if (cancelled) return;
        const activeEstimate = jobs.find(
          (j) => j.mode === "estimate" && ACTIVE_STATUSES.has(j.status),
        );
        const lastEstimate =
          activeEstimate ?? jobs.find((j) => j.mode === "estimate");
        const activeExecute = jobs.find(
          (j) => j.mode === "execute" && ACTIVE_STATUSES.has(j.status),
        );
        const lastExecute =
          activeExecute ?? jobs.find((j) => j.mode === "execute");
        if (lastEstimate) setEstimateJob(lastEstimate);
        if (lastExecute) setExecuteJob(lastExecute);
        if (activeEstimate) {
          void pollUntilDone(activeEstimate.id, setEstimateJob);
        }
        if (activeExecute) {
          void pollUntilDone(activeExecute.id, setExecuteJob);
        }
      })
      .catch(() => {
        // Soft-fail — al montar no queremos romper la página por
        // un fallo del listado. El operador puede lanzar un estimate
        // nuevo y la UI seguirá funcionando.
      });
    return () => {
      cancelled = true;
    };
  }, [pollUntilDone]);

  async function onEstimate() {
    // PR-Fix-Backfill-Gmail-Tras-Validación bug 5. Dedup: si ya hay
    // un estimate `queued`/`running`/`cancelling`, reusamos ese row
    // en vez de crear uno nuevo. Antes el operador podía pulsar
    // "Estimar espacio" dos veces y se encolaban DOS estimates
    // simultáneos — el más reciente queued tapaba al que estaba
    // running.
    if (estimateJob && ACTIVE_STATUSES.has(estimateJob.status)) {
      onMessage(
        "Ya hay una estimación en curso, espera a que termine.",
      );
      await pollUntilDone(estimateJob.id, setEstimateJob);
      return;
    }
    setBusy(true);
    onError(null);
    onMessage(null);
    try {
      const job = await triggerGmailBackfillEstimate(monthsBack, aliasesScope);
      setEstimateJob(job);
      await pollUntilDone(job.id, setEstimateJob);
    } catch (err) {
      onError(extractErrorMessage(err, "No se pudo lanzar la estimación."));
    } finally {
      setBusy(false);
    }
  }

  async function onExecute() {
    // Mismo dedup que onEstimate — si ya hay un execute activo,
    // reusamos. No queremos dos backfills procesando en paralelo
    // (duplicarían inserts en email_messages y consumirían cuota
    // Gmail por nada).
    if (executeJob && ACTIVE_STATUSES.has(executeJob.status)) {
      onMessage(
        "Ya hay un backfill en curso, espera a que termine o cancélalo antes de lanzar otro.",
      );
      await pollUntilDone(executeJob.id, setExecuteJob);
      return;
    }
    setBusy(true);
    onError(null);
    onMessage(null);
    try {
      const job = await triggerGmailBackfillExecute({
        monthsBack,
        includeAttachments,
        maxAttachmentSizeMb: maxAttachmentMb,
        aliasesScope,
      });
      setExecuteJob(job);
      onMessage(
        "Backfill encolado. Puede tardar varios minutos u horas según volumen — déjalo correr de fondo y vuelve a este panel para ver el progreso.",
      );
      await pollUntilDone(job.id, setExecuteJob);
    } catch (err) {
      onError(extractErrorMessage(err, "No se pudo lanzar el backfill."));
    } finally {
      setBusy(false);
    }
  }

  async function onCancel() {
    if (!executeJob) return;
    if (!window.confirm("¿Cancelar el backfill en marcha?")) return;
    try {
      const updated = await cancelGmailBackfill(executeJob.id);
      setExecuteJob(updated);
    } catch (err) {
      onError(extractErrorMessage(err, "No se pudo cancelar el backfill."));
    }
  }

  const estimateResult =
    estimateJob?.status === "completed" && estimateJob.result
      ? (estimateJob.result as GmailBackfillEstimateResult)
      : null;
  const executeActive =
    executeJob?.status === "queued" ||
    executeJob?.status === "running" ||
    executeJob?.status === "cancelling";
  const executeTotal =
    executeJob?.total_processed ??
    0;

  return (
    <section
      style={{
        border: "1px solid #ccc",
        borderRadius: 8,
        padding: "1rem",
        marginTop: "1rem",
      }}
    >
      <h3 style={{ marginTop: 0 }}>
        <History size={16} aria-hidden /> Backfill histórico Gmail
      </h3>
      <p className="muted">
        Carga conversaciones entre cualquier alias de los comerciales del CRM
        y los contactos del CRM en los últimos N meses. Solo se importan
        conversaciones con contactos YA existentes — los emails de remitentes
        sin contacto se ignoran. Los adjuntos son opcionales y filtran por
        tamaño.
      </p>

      <div style={{ display: "flex", gap: "1rem", flexWrap: "wrap", marginBottom: "1rem" }}>
        <label>
          Meses hacia atrás:{" "}
          <input
            type="number"
            min={1}
            max={120}
            value={monthsBack}
            disabled={busy || executeActive}
            onChange={(e) => setMonthsBack(Number(e.target.value))}
            style={{ width: 80 }}
          />
        </label>
        <label>
          <input
            type="checkbox"
            checked={includeAttachments}
            disabled={busy || executeActive}
            onChange={(e) => setIncludeAttachments(e.target.checked)}
          />{" "}
          Incluir adjuntos
        </label>
        <label>
          Tamaño máximo adjunto (MB):{" "}
          <input
            type="number"
            min={0}
            max={200}
            value={maxAttachmentMb}
            disabled={busy || executeActive || !includeAttachments}
            onChange={(e) => setMaxAttachmentMb(Number(e.target.value))}
            style={{ width: 80 }}
          />
        </label>
      </div>

      {/* PR-Fix-Backfill-Gmail-Cero-Importados. Acotar el alcance de
       * aliases reduce el volumen del primer backfill real y baja el
       * riesgo de pegarse contra el rate limit de Gmail. Default
       * `primary_only` para que el operador no se sorprenda. */}
      <fieldset
        style={{
          border: "1px solid #ccc",
          borderRadius: 6,
          padding: ".5rem .75rem",
          marginBottom: "1rem",
        }}
      >
        <legend style={{ padding: "0 .5rem", fontWeight: 600 }}>
          Alcance de aliases
        </legend>
        <label style={{ display: "block", marginBottom: ".25rem" }}>
          <input
            type="radio"
            name="aliases_scope"
            value="primary_only"
            checked={aliasesScope === "primary_only"}
            disabled={busy || executeActive}
            onChange={() => setAliasesScope("primary_only")}
          />{" "}
          Solo alias principal (recomendado, más rápido)
        </label>
        <label style={{ display: "block" }}>
          <input
            type="radio"
            name="aliases_scope"
            value="all_visible"
            checked={aliasesScope === "all_visible"}
            disabled={busy || executeActive}
            onChange={() => setAliasesScope("all_visible")}
          />{" "}
          Todos los aliases visibles
        </label>
        <p className="muted small" style={{ marginTop: ".5rem", marginBottom: 0 }}>
          &quot;Solo alias principal&quot; importa conversaciones del email
          principal del comercial. Si tus comerciales reciben respuestas en
          aliases secundarios (ej. por marca), elige &quot;Todos los aliases
          visibles&quot; para no perder esas conversaciones.
        </p>
      </fieldset>

      <div style={{ display: "flex", gap: ".5rem", flexWrap: "wrap" }}>
        <button
          type="button"
          className="button small secondary"
          onClick={onEstimate}
          disabled={busy || executeActive}
        >
          <History size={12} aria-hidden /> {busy && !estimateResult ? "Estimando…" : "Estimar espacio"}
        </button>
        <button
          type="button"
          className="button small"
          onClick={onExecute}
          disabled={busy || executeActive || !estimateResult}
          title={
            estimateResult
              ? "Ejecuta el backfill real"
              : "Primero estima el espacio para confirmar"
          }
        >
          <Play size={12} aria-hidden /> Confirmar y ejecutar backfill
        </button>
        {executeActive ? (
          <button
            type="button"
            className="button small secondary"
            onClick={onCancel}
          >
            <X size={12} aria-hidden /> Cancelar
          </button>
        ) : null}
      </div>

      {estimateJob ? (
        <div style={{ marginTop: "1rem" }}>
          <strong>Estimación:</strong> status {estimateJob.status}
          {estimateJob.status === "running" || estimateJob.status === "queued"
            ? ` (procesados ${estimateJob.total_processed})`
            : null}
        </div>
      ) : null}

      {estimateResult ? (
        <div
          style={{
            marginTop: ".5rem",
            padding: ".75rem",
            background: "#f8f8f8",
            borderRadius: 4,
          }}
        >
          <ul style={{ lineHeight: 1.6, margin: 0 }}>
            <li>
              Mensajes a importar: <strong>{estimateResult.total_emails.toLocaleString()}</strong>
            </li>
            <li>
              Adjuntos: <strong>{estimateResult.total_attachments_count.toLocaleString()}</strong>
              {" ("}
              <strong>{estimateResult.total_attachments_size_mb.toLocaleString()} MB</strong>
              {" ≈ "}
              <strong>{estimateResult.estimated_storage_gb} GB</strong>
              {")"}
            </li>
            <li>
              Duración estimada: <strong>{estimateResult.estimated_duration_minutes} min</strong>
            </li>
          </ul>
          <h4 style={{ marginBottom: ".25rem" }}>Desglose por comercial</h4>
          <table className="data-table">
            <thead>
              <tr>
                <th>Comercial</th>
                <th>Emails</th>
                <th>Adjuntos</th>
                <th>MB</th>
                <th>Estado</th>
              </tr>
            </thead>
            <tbody>
              {estimateResult.per_user_breakdown.map((row) => (
                <tr key={row.user_id}>
                  <td>{row.email}</td>
                  <td>{row.emails}</td>
                  <td>{row.attachments_count}</td>
                  <td>{row.attachments_mb.toFixed(1)}</td>
                  <td>
                    {row.needs_reconnect ? "⚠ Reconectar Gmail" : "OK"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {executeJob ? (
        <div style={{ marginTop: "1rem" }}>
          <strong>Ejecución:</strong> status {executeJob.status}
          {" — "}
          procesados {executeTotal.toLocaleString()}, importados {executeJob.total_imported.toLocaleString()},
          ya en CRM {executeJob.total_skipped.toLocaleString()}, errores {executeJob.total_errors.toLocaleString()}
          {executeJob.error_summary ? (
            <div className="error-state" style={{ marginTop: ".5rem" }}>
              {executeJob.error_summary}
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
