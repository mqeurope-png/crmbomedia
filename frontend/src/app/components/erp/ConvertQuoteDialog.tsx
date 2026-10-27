"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  getQuoteConvertPreview,
  type FactusolQuote,
  type PaymentIntentInput,
  type QuoteConvertPreview,
} from "../../lib/erpApi";
import { extractErrorMessage } from "../../lib/errors";
import { ModalCloseButton } from "../ModalCloseButton";
import { useModalBehaviour } from "../useModalBehaviour";
import { initialPayment, paymentReady, PaymentStep } from "./PaymentStep";
import { ResumenPedidoView } from "./ResumenPedidoView";

/** «Convertir en pedido» (Fase 2): primero el RESUMEN de lo que se va a crear
 *  (empresa, fecha, conceptos con cantidades, importe y la proforma de
 *  origen, leído del servidor con el mismo cálculo que la conversión), luego
 *  el paso de pago (opción B, sin factura) y la confirmación. Nada se crea
 *  hasta «Crear pedido y albarán». Se usa igual desde la pestaña de
 *  proformas de la ficha de empresa y desde la pantalla Proformas (Fase 4);
 *  quien lo abre encola la conversión con el pago elegido. */
export function ConvertQuoteDialog({
  quote, onCancel, onConfirm,
}: {
  quote: FactusolQuote;
  onCancel: () => void;
  onConfirm: (payment: PaymentIntentInput) => void;
}) {
  const [payment, setPayment] = useState<PaymentIntentInput>(initialPayment());
  const [preview, setPreview] = useState<QuoteConvertPreview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const { overlayProps, requestClose } = useModalBehaviour({ onClose: onCancel });
  const codpre = quote.codpre ?? "";
  const serie = quote.serie ?? (Number(quote.tippre) || null);

  useEffect(() => {
    if (!codpre) return;
    let alive = true;
    getQuoteConvertPreview(codpre, serie)
      .then((p) => { if (alive) setPreview(p); })
      .catch((e) => {
        if (alive) setError(extractErrorMessage(e, "No se pudo leer la proforma para el resumen."));
      })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [codpre, serie]);

  const yaExiste = preview?.already_existed ?? null;

  return (
    <div className="modal-overlay" role="dialog" aria-modal="true"
         aria-label="Convertir proforma en pedido" {...overlayProps}>
      <div className="modal-dialog erp-modal modal-wide">
        <h2>Convertir la proforma {quote.codpre} en pedido</h2>
        <ModalCloseButton onClose={requestClose} />
        <p className="muted small">
          {quote.referencia || "Sin referencia"} · {quote.total.toFixed(2)} €.
          Se creará el pedido en BoHub y su <strong>albarán en FACTUSOL</strong>
          {" "}(sin factura).
        </p>
        {loading ? <p className="muted">Leyendo la proforma…</p> : null}
        {error ? <p className="form-error" role="alert">{error}</p> : null}
        {yaExiste ? (
          <p className="form-info" role="status">
            Esta proforma ya se convirtió en el pedido{" "}
            <Link href={`/erp/orders/${yaExiste.order_id}`}>{yaExiste.order_number}</Link>.
            No se crea otro.
          </p>
        ) : null}
        {preview ? (
          <>
            <ResumenPedidoView resumen={preview} />
            {!preview.company_linked && !yaExiste ? (
              <p className="form-warning" role="alert">
                El cliente FACTUSOL {preview.cliente_codigo ?? ""} no tiene empresa en el CRM.
              </p>
            ) : null}
          </>
        ) : null}
        {!yaExiste ? <PaymentStep value={payment} onChange={setPayment} /> : null}
        <div className="modal-actions">
          <button type="button" className="button secondary" onClick={onCancel}>
            {yaExiste ? "Cerrar" : "Cancelar"}
          </button>
          {!yaExiste ? (
            <button type="button" className="button"
                    disabled={!preview || !paymentReady(payment)}
                    onClick={() => onConfirm(payment)}>
              Crear pedido y albarán
            </button>
          ) : null}
        </div>
      </div>
    </div>
  );
}
