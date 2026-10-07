"use client";

import { useState } from "react";
import type { FactusolQuote, PaymentIntentInput } from "../../lib/erpApi";
import { ModalCloseButton } from "../ModalCloseButton";
import { useModalBehaviour } from "../useModalBehaviour";
import { initialPayment, paymentReady, PaymentStep } from "./PaymentStep";

/** «Convertir en pedido» (Fase 2): paso de pago (opción B, sin factura) y
 *  confirmación. Se usa igual desde la pestaña de proformas de la ficha de
 *  empresa y desde la pantalla Proformas (Fase 4); quien lo abre encola la
 *  conversión con el pago elegido. */
export function ConvertQuoteDialog({
  quote, onCancel, onConfirm,
}: {
  quote: FactusolQuote;
  onCancel: () => void;
  onConfirm: (payment: PaymentIntentInput) => void;
}) {
  const [payment, setPayment] = useState<PaymentIntentInput>(initialPayment());
  const { overlayProps, requestClose } = useModalBehaviour({ onClose: onCancel });
  return (
    <div className="modal-overlay" role="dialog" aria-modal="true"
         aria-label="Convertir proforma en pedido" {...overlayProps}>
      <div className="modal-dialog erp-modal">
        <h2>Convertir la proforma {quote.codpre} en pedido</h2>
        <ModalCloseButton onClose={requestClose} />
        <p className="muted small">
          {quote.referencia || "Sin referencia"} · {quote.total.toFixed(2)} €.
          Se creará el pedido en BoHub y su <strong>albarán en FACTUSOL</strong>
          {" "}(sin factura).
        </p>
        <PaymentStep value={payment} onChange={setPayment} />
        <div className="modal-actions">
          <button type="button" className="button secondary" onClick={onCancel}>
            Cancelar
          </button>
          <button type="button" className="button"
                  disabled={!paymentReady(payment)}
                  onClick={() => onConfirm(payment)}>
            Crear pedido y albarán
          </button>
        </div>
      </div>
    </div>
  );
}
