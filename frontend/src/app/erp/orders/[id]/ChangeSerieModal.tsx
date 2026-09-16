"use client";

import { useState } from "react";
import {
  changeOrderFactusolSerie,
  factusolSerieLabel,
  FACTUSOL_SERIES,
  waitForQuoteJob,
} from "../../../lib/erpApi";
import { extractErrorMessage } from "../../../lib/errors";

/** Lote 7 · P1 — «Cambiar serie» (empresa emisora) de un pedido MANUAL. La
 *  serie manda en `resolve_serie`, así que TODO lo que BoHub emita desde el
 *  pedido (albarán, y luego proforma / factura) sale en la empresa elegida.
 *  Si el pedido YA tiene albarán, cambiar la serie lo BORRA en FACTUSOL y lo
 *  RE-CREA en la serie nueva (worker serial): se avisa y se confirma. Con
 *  factura emitida el backend lo rechaza (la factura se anula desde FACTUSOL). */
export function ChangeSerieModal({
  orderId,
  orderNumber,
  currentSerie,
  albaranNumber,
  onClose,
  onDone,
}: {
  orderId: string;
  orderNumber: string;
  currentSerie: number | null;
  /** nº del albarán FACTUSOL (`5-500008`) o null: si lo hay, se borra y recrea. */
  albaranNumber: string | null;
  onClose: () => void;
  /** Tras cambiar la serie (para refrescar la ficha). */
  onDone?: () => void;
}) {
  const [serie, setSerie] = useState<number>(currentSerie ?? 5);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [warning, setWarning] = useState<string | null>(null);

  const hasAlbaran = Boolean(albaranNumber);
  const changed = serie !== currentSerie;

  async function confirm() {
    if (!changed || busy) return;
    setBusy(true);
    setError(null);
    setWarning(null);
    try {
      const res = await changeOrderFactusolSerie(orderId, serie);
      if (res.factusol_serie_job_id) {
        // Con albarán: el worker serial borra el viejo y recrea en la serie
        // nueva. Se espera al job para enseñar el resultado real.
        const status = await waitForQuoteJob(res.factusol_serie_job_id);
        if (status.status === "failed") {
          throw new Error(
            status.error || "No se pudo cambiar la serie del albarán en FACTUSOL.",
          );
        }
        if (status.status === "finished" && status.result?.error) {
          // Borrado OK pero recreación KO: el pedido quedó sin albarán.
          setWarning(
            "La serie se cambió y el albarán viejo se borró, pero no se pudo "
            + "recrear en FACTUSOL. El pedido quedó sin albarán: reinténtalo "
            + "desde «Envío y seguimiento».",
          );
        }
      }
      onDone?.();
      if (!warning) onClose();
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo cambiar la serie del pedido."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-overlay" role="dialog" aria-modal="true"
         aria-label={`Cambiar serie del pedido ${orderNumber}`}>
      <div className="modal-dialog erp-modal">
        <h2>Cambiar serie <span className="muted">{orderNumber}</span></h2>
        <p className="muted small">
          La serie es la empresa emisora del pedido: manda en el albarán y —más
          tarde— en la proforma / factura. Serie actual:{" "}
          <strong>{currentSerie ? `${currentSerie} · ${factusolSerieLabel(currentSerie)}` : "sin fijar"}</strong>.
        </p>
        <label className="field">
          <span>Nueva serie (empresa emisora)</span>
          <select
            aria-label="Nueva serie del pedido"
            value={serie}
            disabled={busy}
            onChange={(e) => setSerie(Number(e.target.value))}
          >
            {FACTUSOL_SERIES.map((s) => (
              <option key={s.value} value={s.value}>{s.value} · {s.label}</option>
            ))}
          </select>
        </label>
        {hasAlbaran ? (
          <p className="form-warning" role="alert">
            El pedido tiene el albarán <strong>{albaranNumber}</strong> en
            FACTUSOL. Cambiar la serie lo BORRA y lo RE-CREA en la serie nueva
            (no se puede deshacer). La factura nunca se toca.
          </p>
        ) : (
          <p className="muted small">
            El pedido no tiene albarán en FACTUSOL: solo se registra la serie.
          </p>
        )}
        {warning ? <p className="form-warning" role="status">{warning}</p> : null}
        {error ? <p className="form-error" role="alert">{error}</p> : null}
        <div className="modal-actions">
          <button type="button" className="button secondary" onClick={onClose} disabled={busy}>
            {warning ? "Cerrar" : "Cancelar"}
          </button>
          <button type="button" className="button" onClick={confirm} disabled={!changed || busy}>
            {busy
              ? (hasAlbaran ? "Recreando albarán…" : "Guardando…")
              : hasAlbaran ? "Cambiar serie y recrear albarán" : "Cambiar serie"}
          </button>
        </div>
      </div>
    </div>
  );
}
