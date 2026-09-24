"use client";

import { useState } from "react";
import type { PaymentIntentInput } from "../../lib/erpApi";
import { initialPayment, paymentReady, PaymentStep } from "./PaymentStep";

/** C1 — antes de generar el albarán hay que decidir el pago, no dejarlo «en el
 *  aire». Dos caminos:
 *   - confirmar el pago (opción B: se apunta en el pedido, NUNCA se escribe el
 *     cobro en FACTUSOL) o dejarlo «sin pago» (pendiente, se cobra luego);
 *   - «Sin cobro» (cortesía): de un solo clic, el pedido no se factura ni se
 *     cobra.
 *  El que abre el diálogo genera el albarán con la decisión elegida. */
export function AlbaranPagoDialog({
  onCancel, onConfirm, busy = false,
}: {
  onCancel: () => void;
  onConfirm: (payment: PaymentIntentInput) => void;
  busy?: boolean;
}) {
  const [payment, setPayment] = useState<PaymentIntentInput>(initialPayment());
  return (
    <div className="modal-overlay" role="dialog" aria-modal="true"
         aria-label="Decidir el pago antes del albarán">
      <div className="modal-dialog erp-modal">
        <h2>Antes de generar el albarán</h2>
        <p className="muted small">
          Decide el pago: confírmalo o déjalo pendiente (se cobra luego), o
          márcalo <strong>«sin cobro»</strong> si es un envío de cortesía. El
          pago queda apuntado en el pedido; nunca se escribe el cobro en
          FACTUSOL desde aquí.
        </p>
        <PaymentStep value={payment} onChange={setPayment} disabled={busy} />
        <div className="modal-actions">
          <button type="button" className="button secondary" onClick={onCancel} disabled={busy}>
            Cancelar
          </button>
          <button
            type="button" className="button secondary" disabled={busy}
            title="Envío de cortesía: no se factura ni se cobra"
            onClick={() => onConfirm({ ...initialPayment(), paid: false, no_charge: true })}
          >
            Sin cobro (cortesía)
          </button>
          <button
            type="button" className="button" disabled={busy || !paymentReady(payment)}
            onClick={() => onConfirm(payment)}
          >
            Generar albarán
          </button>
        </div>
      </div>
    </div>
  );
}
