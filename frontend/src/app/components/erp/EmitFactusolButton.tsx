"use client";

import { useEffect, useRef, useState } from "react";
import { extractErrorMessage } from "../../lib/errors";
import {
  emitFactusolInvoice,
  getFactusolInvoiceStatus,
  type EmitFactusolOptions,
  type FactusolStatus,
} from "../../lib/erpApi";
import { EmitFactusolModal } from "./EmitFactusolModal";

const POLL_INTERVAL_MS = 2000;
const POLL_MAX_MS = 60_000;

type Phase = "idle" | "confirm" | "working" | "done" | "error";

/** Botón «Emitir factura FACTUSOL» (Fase C · C-2 / C-2-fix2).
 *
 *  - Si el pedido ya tiene factura (por props o por `factusolStatus` en vivo)
 *    muestra el badge verde en vez del botón.
 *  - Si solo hay albarán, muestra un badge amarillo Y permite emitir.
 *  - `enableOptions` (ficha del pedido) abre el modal de 5 campos; sin él
 *    (Cola PEDIDOS) usa el modal de confirmación simple. */
export function EmitFactusolButton({
  orderId,
  invoiceStatus,
  factusolInvoiceNumber,
  totalAmount,
  currency,
  factusolStatus = null,
  enableOptions = false,
  onInvoiced,
}: {
  orderId: string;
  invoiceStatus: string;
  factusolInvoiceNumber: string | null;
  totalAmount: number;
  currency: string;
  /** El pedido no necesita empresa CRM: la factura se copia del F_PCL, que ya
   *  lleva el cliente. Se acepta la prop por compatibilidad con la Cola. */
  companyId?: string | null;
  /** Estado en vivo pre-cargado por la ficha (Promise.all). */
  factusolStatus?: FactusolStatus | null;
  enableOptions?: boolean;
  onInvoiced?: (codfac: string) => void;
}) {
  const preInvoiced =
    factusolStatus?.status === "invoiced" ? factusolStatus.codfac : null;
  const [phase, setPhase] = useState<Phase>("idle");
  const [codfac, setCodfac] = useState<string | null>(
    factusolInvoiceNumber || preInvoiced,
  );
  const [message, setMessage] = useState<string | null>(null);
  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);

  useEffect(() => () => { timers.current.forEach(clearTimeout); }, []);

  const effectiveCodfac = codfac || factusolInvoiceNumber || preInvoiced;
  if (effectiveCodfac) {
    return (
      <span className="badge ok" aria-label="Factura FACTUSOL">
        Facturado FACTUSOL #{effectiveCodfac}
      </span>
    );
  }
  if (
    invoiceStatus === "already_invoiced_externally" ||
    factusolStatus?.status === "already_invoiced_externally"
  ) {
    return null; // marcado como facturado fuera del ERP
  }

  const albaran =
    factusolStatus?.status === "albaran" ? factusolStatus : null;

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

  async function doEmit(options?: EmitFactusolOptions) {
    setPhase("working");
    setMessage("Factura encolada, generando…");
    try {
      const r = await emitFactusolInvoice(orderId, options);
      poll(r.job_id, Date.now() + POLL_MAX_MS);
    } catch (e) {
      setPhase("error");
      setMessage(extractErrorMessage(e, "No se pudo emitir la factura."));
    }
  }

  return (
    <>
      {albaran ? (
        <span className="badge warn" aria-label="Albarán FACTUSOL">
          Albarán en FACTUSOL
          {albaran.albaran_codigo ? ` #${albaran.albaran_codigo}` : ""} — sin factura
        </span>
      ) : null}
      <button
        type="button"
        /* D-2: emitir factura es acción principal → botón primario. */
        className="button small"
        disabled={phase === "working"}
        onClick={() => setPhase("confirm")}
      >
        {phase === "working" ? "Generando…" : "Emitir factura FACTUSOL"}
      </button>
      {message ? (
        <p className={phase === "error" ? "form-error" : "muted small"} role="status">
          {message}
        </p>
      ) : null}

      {phase === "confirm" && enableOptions ? (
        <EmitFactusolModal
          totalAmount={totalAmount}
          currency={currency}
          onCancel={() => setPhase("idle")}
          onSubmit={(opts) => doEmit(opts)}
        />
      ) : null}

      {phase === "confirm" && !enableOptions ? (
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
              <button type="button" className="button" onClick={() => doEmit()}>
                Emitir factura
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}
