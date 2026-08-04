"use client";

import { useEffect, useRef, useState } from "react";
import { extractErrorMessage } from "../../lib/errors";
import {
  emitFactusolInvoice,
  getFactusolInvoiceStatus,
} from "../../lib/erpApi";

const POLL_INTERVAL_MS = 2000;
const POLL_MAX_MS = 60_000;

type Phase = "idle" | "confirm" | "working" | "done" | "error";

/** Botón «Emitir factura FACTUSOL» (Fase C · C-2): confirma, encola la
 *  emisión real y hace polling del estado. Si el pedido ya está facturado
 *  muestra el badge en vez del botón. */
export function EmitFactusolButton({
  orderId,
  invoiceStatus,
  factusolInvoiceNumber,
  totalAmount,
  currency,
  companyId,
  onInvoiced,
}: {
  orderId: string;
  invoiceStatus: string;
  factusolInvoiceNumber: string | null;
  totalAmount: number;
  currency: string;
  companyId: string | null;
  onInvoiced?: (codfac: string) => void;
  }) {
  const [phase, setPhase] = useState<Phase>("idle");
  const [codfac, setCodfac] = useState<string | null>(factusolInvoiceNumber);
  const [message, setMessage] = useState<string | null>(null);
  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);

  useEffect(() => () => { timers.current.forEach(clearTimeout); }, []);

  if (codfac || factusolInvoiceNumber) {
    return (
      <span className="badge ok" aria-label="Factura FACTUSOL">
        Facturado FACTUSOL #{codfac || factusolInvoiceNumber}
      </span>
    );
  }
  if (invoiceStatus === "already_invoiced_externally") {
    return null; // marcado como facturado fuera del ERP
  }

  function poll(jobId: string, deadline: number) {
    getFactusolInvoiceStatus(orderId, jobId)
      .then((s) => {
        if (s.status === "invoiced") {
          setCodfac(s.codfac);
          setPhase("done");
          setMessage(`Factura #${s.codfac} creada en FACTUSOL.`);
          onInvoiced?.(s.codfac);
        } else if (s.status === "failed") {
          setPhase("error");
          setMessage(s.error ? `Error: ${s.error}` : "La emisión falló.");
        } else if (Date.now() >= deadline) {
          setPhase("error");
          setMessage("La emisión tarda más de lo esperado; revisa la bandeja.");
        } else {
          timers.current.push(setTimeout(() => poll(jobId, deadline), POLL_INTERVAL_MS));
        }
      })
      .catch(() => {
        if (Date.now() >= deadline) {
          setPhase("error");
          setMessage("No se pudo consultar el estado de la factura.");
        } else {
          timers.current.push(setTimeout(() => poll(jobId, deadline), POLL_INTERVAL_MS));
        }
      });
  }

  async function confirmEmit() {
    setPhase("working");
    setMessage("Factura encolada, generando…");
    try {
      const r = await emitFactusolInvoice(orderId);
      poll(r.job_id, Date.now() + POLL_MAX_MS);
    } catch (e) {
      setPhase("error");
      setMessage(extractErrorMessage(e, "No se pudo emitir la factura."));
    }
  }

  return (
    <>
      <button
        type="button"
        className="button small secondary"
        disabled={phase === "working" || !companyId}
        title={companyId ? undefined : "El pedido no tiene empresa"}
        onClick={() => setPhase("confirm")}
      >
        {phase === "working" ? "Generando…" : "Emitir factura FACTUSOL"}
      </button>
      {message ? (
        <p className={phase === "error" ? "form-error" : "muted small"} role="status">
          {message}
        </p>
      ) : null}

      {phase === "confirm" ? (
        <div className="modal-overlay" role="dialog" aria-modal="true"
             aria-label="Confirmar emisión de factura FACTUSOL">
          <div className="modal-dialog">
            <h2>Emitir factura en FACTUSOL</h2>
            <p>
              Total: <strong>{totalAmount.toFixed(2)} {currency}</strong>
            </p>
            <p className="form-error">
              Se creará una factura <strong>real</strong> en FACTUSOL. Esta acción
              no es reversible desde el CRM.
            </p>
            <div className="modal-actions">
              <button type="button" className="button secondary"
                      onClick={() => setPhase("idle")}>
                Cancelar
              </button>
              <button type="button" className="button" onClick={confirmEmit}>
                Emitir factura
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}
