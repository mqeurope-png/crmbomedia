"use client";

import { useState } from "react";
import type { PaymentIntentInput } from "../../lib/erpApi";
import { initialPayment, paymentReady, PaymentStep } from "./PaymentStep";

/** C-bis — «Pagado» desde «Otras acciones de estado» de la ficha: al marcar el
 *  pedido como pagado se pide la FORMA de pago (transferencia / PayPal /
 *  contado / crédito…); la cuenta y la fecha son opcionales. Es un apunte del
 *  pedido, no un cobro en FACTUSOL. */
export function MarkPaidDialog({
  onCancel, onConfirm, busy = false,
}: {
  onCancel: () => void;
  onConfirm: (payment: PaymentIntentInput) => void;
  busy?: boolean;
}) {
  const [payment, setPayment] = useState<PaymentIntentInput>(
    { ...initialPayment(), paid: true },
  );
  return (
    <div className="modal-overlay" role="dialog" aria-modal="true"
         aria-label="Marcar el pedido como pagado">
      <div className="modal-dialog erp-modal">
        <h2>Marcar como pagado</h2>
        <p className="muted small">
          Elige la <strong>forma de pago</strong>. La cuenta y la fecha son
          opcionales: esto solo apunta el pago en el pedido, no escribe el cobro
          en FACTUSOL (eso se hace a mano con «Registrar cobro», cuando exista la
          factura).
        </p>
        <PaymentStep value={payment} onChange={setPayment} disabled={busy} />
        <div className="modal-actions">
          <button type="button" className="button secondary" onClick={onCancel} disabled={busy}>
            Cancelar
          </button>
          <button
            type="button" className="button" disabled={busy || !paymentReady(payment)}
            onClick={() => onConfirm({ ...payment, paid: true })}
          >
            Guardar pago
          </button>
        </div>
      </div>
    </div>
  );
}
