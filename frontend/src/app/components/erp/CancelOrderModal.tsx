"use client";

import { useEffect, useState } from "react";
import {
  cancelOrder,
  previewCancelOrder,
  type CancelOrderDoc,
  type CancelOrderPreview,
  type CancelOrderResult,
} from "../../lib/erpApi";
import { extractErrorMessage } from "../../lib/errors";

const DOC_LABEL: Record<CancelOrderDoc["doc_type"], string> = {
  albaranes: "Albarán",
  presupuestos: "Presupuesto / proforma",
};

/** Lote ERP · «Anular pedido» (manual / FACTUSOL; nunca web). Distinto de
 *  «quitar»: es un estado FINAL del pedido (reversible con «Restaurar»). Antes
 *  de anular se AVISA: si no se puede (web / con factura) y qué documentos
 *  tiene en FACTUSOL; el operador decide si borrar allí el albarán y/o el
 *  presupuesto que sigan vivos. La factura nunca: se anula desde FACTUSOL. */
export function CancelOrderModal({
  orderId,
  orderNumber,
  onClose,
  onDone,
}: {
  orderId: string;
  orderNumber: string;
  onClose: () => void;
  /** Tras anular (para refrescar la ficha). */
  onDone?: (result: CancelOrderResult) => void;
}) {
  const [preview, setPreview] = useState<CancelOrderPreview | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [deleteDocs, setDeleteDocs] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<CancelOrderResult | null>(null);

  useEffect(() => {
    let alive = true;
    previewCancelOrder(orderId)
      .then((p) => { if (alive) setPreview(p); })
      .catch((e) => {
        if (alive) setLoadError(extractErrorMessage(e, "No se pudo comprobar el pedido."));
      });
    return () => { alive = false; };
  }, [orderId]);

  const deletable = (preview?.factusol_docs ?? []).filter((d) => d.deletable);
  const canCancel = !!preview && preview.can_cancel && !busy;

  async function confirm() {
    if (!preview || !canCancel) return;
    setBusy(true);
    setError(null);
    try {
      const r = await cancelOrder(orderId, {
        confirm: true,
        reason: reason.trim() || null,
        delete_factusol_docs: deleteDocs && deletable.length > 0,
      });
      setResult(r);
      onDone?.(r);
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo anular el pedido."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-overlay" role="dialog" aria-modal="true"
         aria-label={`Anular pedido ${orderNumber}`}>
      <div className="modal-dialog erp-modal">
        <h2>Anular pedido <span className="muted">{orderNumber}</span></h2>

        {result ? (
          <>
            <p className="form-success" role="status">
              Pedido anulado. Sale de la bandeja, de las colas y del seguimiento;
              se puede restaurar desde la ficha.
            </p>
            {result.factusol_delete_job_id ? (
              <p className="muted small">
                Borrado en FACTUSOL encolado ({(result.factusol_docs_to_delete ?? [])
                  .map((d) => `${DOC_LABEL[d.doc_type]} ${d.numero}`).join(", ")}).
                El worker lo hace en unos segundos y queda en la actividad del pedido.
              </p>
            ) : null}
            {(result.cancel_warnings ?? []).map((w) => (
              <p key={w} className="form-error">{w}</p>
            ))}
            <div className="modal-actions">
              <button type="button" className="button" onClick={onClose}>Cerrar</button>
            </div>
          </>
        ) : (
          <>
            <p className="muted small">
              Anular es un estado final del pedido en BoHub, distinto de «quitar de
              la bandeja»: deja de contar en todas las listas y queda como anulado
              en su historial. Es reversible («Restaurar»). No se borra nada del
              pedido en BoHub.
            </p>
            {loadError ? <p className="form-error">{loadError}</p> : null}
            {!preview && !loadError ? <p className="muted">Comprobando…</p> : null}
            {preview ? (
              <>
                {preview.blockers.map((b) => (
                  <p key={b} className="form-error" role="alert">{b}</p>
                ))}
                {preview.warnings.map((w) => (
                  <p key={w} className="muted small">{w}</p>
                ))}
                {preview.can_cancel ? (
                  <>
                    {preview.factusol_docs.length > 0 ? (
                      <div className="field">
                        <span>Documentos en FACTUSOL</span>
                        <ul className="item-list small" aria-label="Documentos FACTUSOL del pedido">
                          {preview.factusol_docs.map((d) => (
                            <li key={`${d.doc_type}-${d.numero}`}>
                              <strong>{DOC_LABEL[d.doc_type]} {d.numero}</strong>
                              {d.deletable
                                ? " — se puede borrar"
                                : ` — no se borra${d.reason ? `: ${d.reason}` : ""}`}
                            </li>
                          ))}
                        </ul>
                        {deletable.length > 0 ? (
                          <label className="checkbox">
                            <input
                              type="checkbox"
                              checked={deleteDocs}
                              disabled={busy}
                              onChange={(e) => setDeleteDocs(e.target.checked)}
                            />{" "}
                            Borrar también en FACTUSOL{" "}
                            {deletable.map((d) => `${DOC_LABEL[d.doc_type].toLowerCase()} ${d.numero}`).join(" y ")}
                            {" "}(no se puede deshacer)
                          </label>
                        ) : null}
                      </div>
                    ) : (
                      <p className="muted small">
                        El pedido no tiene albarán ni presupuesto en FACTUSOL: no se borra nada allí.
                      </p>
                    )}
                    <label className="field">
                      <span>Motivo (opcional)</span>
                      <input
                        type="text"
                        value={reason}
                        aria-label="Motivo de la anulación"
                        maxLength={255}
                        disabled={busy}
                        onChange={(e) => setReason(e.target.value)}
                      />
                    </label>
                  </>
                ) : null}
              </>
            ) : null}
            {error ? <p className="form-error">{error}</p> : null}
            <div className="modal-actions">
              <button type="button" className="button secondary" onClick={onClose} disabled={busy}>
                Cancelar
              </button>
              <button type="button" className="button danger" onClick={confirm} disabled={!canCancel}>
                {busy ? "Anulando…" : "Anular pedido"}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
