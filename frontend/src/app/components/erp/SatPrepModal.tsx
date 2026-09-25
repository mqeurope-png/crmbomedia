"use client";

import { useEffect, useRef, useState } from "react";
import { extractErrorMessage } from "../../lib/errors";
import {
  attachDocument,
  customerLabel,
  fireTransition,
  reportException,
  STATUS_LABELS,
  type SatQueueItem,
} from "../../lib/erpApi";
import { PackingForm } from "./EmbalarModal";
import { ReportExceptionModal } from "./ReportExceptionModal";
import { SatObservaciones } from "./SatTechData";

/** Preparar y embalar un pedido de la Cola SAT en un MODAL sobre la propia
 *  cola (sin cambiar de pantalla ni de ruta): «Empezar preparación» → líneas
 *  para cotejar, peso y medidas de cada bulto (varios si hace falta) →
 *  «📦 Embalar». Con `startOnOpen`, la preparación se empieza al abrirlo (es lo
 *  que hace el botón «▶ Empezar preparación» de la card). Al cerrar, sigues en
 *  la cola, en su sitio; la cola se refresca sola (`onChanged`). */
export function SatPrepModal({
  order,
  startOnOpen = false,
  onChanged,
  onClose,
}: {
  order: SatQueueItem;
  startOnOpen?: boolean;
  /** Algo cambió (empezado, embalado, bloqueado…): la cola se recarga. */
  onChanged: () => void;
  onClose: () => void;
}) {
  const [prep, setPrep] = useState(order.preparation_status);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [showReport, setShowReport] = useState(false);
  const started = useRef(false);

  async function empezar() {
    setBusy(true);
    setError(null);
    try {
      await fireTransition(order.id, { domain: "preparation", to_status: "preparing" });
      setPrep("preparing");
      onChanged();
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo empezar la preparación."));
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    if (startOnOpen && !started.current && order.preparation_status === "in_queue") {
      started.current = true;
      void empezar();
    }
    // Solo al abrir.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function embalado() {
    onChanged();
    onClose();
  }

  async function subirDocumento(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      await attachDocument(order.id, file);
      setNotice("Documento adjuntado.");
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo subir el documento."));
    } finally {
      setBusy(false);
      e.target.value = "";
    }
  }

  async function reportar(data: { type: string; subtype?: string; description: string }) {
    setBusy(true);
    try {
      await reportException(order.id, data);
      setShowReport(false);
      setPrep("blocked");
      setNotice("Problema reportado: el pedido queda bloqueado.");
      onChanged();
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo reportar."));
    } finally {
      setBusy(false);
    }
  }

  const who = customerLabel(order);
  return (
    <div className="modal-overlay" role="dialog" aria-modal="true"
         aria-label={`Preparar ${order.order_number}`}>
      <div className="modal-dialog erp-modal sat-prep-modal">
        <div className="sat-prep-head">
          <h2>📦 {order.order_number}</h2>
          <span className={`badge ${STATUS_LABELS[prep]?.tone ?? "muted"}`}>
            {STATUS_LABELS[prep]?.label ?? prep}
          </span>
        </div>
        {who ? <p className="sat-card-customer">{who}</p> : null}
        {error ? <p className="form-error" role="alert">{error}</p> : null}
        {notice ? <p className="form-success" role="status">{notice}</p> : null}

        <SatObservaciones notes={order.notes} />

        <section className="sat-prep-lines" aria-label="Líneas">
          <h3>Líneas</h3>
          {order.lines.map((l, i) => (
            <label key={i} className="sat-line-check">
              <input type="checkbox" /> <strong>{l.quantity}×</strong> {l.description}
              {l.sku ? <code className="muted"> {l.sku}</code> : null}
            </label>
          ))}
        </section>

        {prep === "in_queue" ? (
          <button type="button" className="button lg" disabled={busy} onClick={() => void empezar()}>
            {busy ? "Empezando…" : "▶ Empezar preparación"}
          </button>
        ) : null}
        {prep === "preparing" ? (
          <section className="sat-prep-embalar" aria-label="Embalar">
            <h3>Bultos</h3>
            <PackingForm orderId={order.id} onDone={embalado} submitLabel="📦 Embalar" />
          </section>
        ) : null}
        {prep === "blocked" ? (
          <p className="muted">Pedido bloqueado — se resuelve desde la bandeja de excepciones.</p>
        ) : null}

        <div className="modal-actions sat-prep-actions">
          <label className="button secondary sat-upload">
            📷 Subir foto / documento
            <input type="file" accept="image/*,application/pdf" onChange={subirDocumento}
                   aria-label="Subir foto o documento" disabled={busy} hidden />
          </label>
          {prep !== "blocked" ? (
            <button type="button" className="button secondary" disabled={busy}
                    onClick={() => setShowReport(true)}>
              ⚠ Reportar problema
            </button>
          ) : null}
          <button type="button" className="button secondary" onClick={onClose}>
            Cerrar
          </button>
        </div>
      </div>
      {showReport ? (
        <ReportExceptionModal onSubmit={reportar} onClose={() => setShowReport(false)} busy={busy} />
      ) : null}
    </div>
  );
}
