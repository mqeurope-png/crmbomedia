"use client";

import { useState } from "react";
import {
  unlinkOrderDocument,
  type LinkedOrderDocument,
  type OrderDetail,
} from "../../lib/erpApi";
import { extractErrorMessage } from "../../lib/errors";
import { ModalCloseButton } from "../ModalCloseButton";
import { useModalBehaviour } from "../useModalBehaviour";

/** «Desvincular documento» sin anular (vínculos hechos por error): el pedido
 *  deja de apuntar la factura / el albarán / la proforma, que sigue en
 *  FACTUSOL tal cual. Queda en el historial. Una muestra convertida sin más
 *  documentos vuelve a modo muestra (no facturable, 0 €). */
export function DesvincularDocumentoModal({
  orderId,
  orderNumber,
  document,
  bornAsSample,
  otherDocuments,
  onClose,
  onDone,
}: {
  orderId: string;
  orderNumber: string;
  document: LinkedOrderDocument;
  /** El pedido nació como muestra (para avisar de que vuelve a modo muestra). */
  bornAsSample: boolean;
  /** ¿Le quedan otros documentos vinculados? (entonces no vuelve a muestra). */
  otherDocuments: boolean;
  onClose: () => void;
  onDone?: (detail: OrderDetail & { unlinked?: { back_to_sample: boolean } }) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { overlayProps, requestClose } = useModalBehaviour({ onClose, disabled: busy });

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      const detail = await unlinkOrderDocument(orderId, document.kind);
      onDone?.(detail);
      onClose();
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo desvincular el documento."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-overlay" role="dialog" aria-modal="true"
         aria-label={`Desvincular ${document.label} de ${orderNumber}`} {...overlayProps}>
      <div className="modal-dialog erp-modal">
        <h2>Desvincular {document.label}</h2>
        <ModalCloseButton onClose={requestClose} disabled={busy} />
        <p className="form-info" role="note">
          {document.label.charAt(0).toUpperCase() + document.label.slice(1)} seguirá
          existiendo en FACTUSOL tal cual y el pedido {orderNumber} dejará de apuntarla.
          No se escribe nada en FACTUSOL; queda en el historial del pedido.
          {bornAsSample && !otherDocuments
            ? " Como el pedido nació como muestra y no le queda otro documento, vuelve a modo muestra (no facturable, 0 €, sus líneas originales)."
            : ""}
        </p>
        {error ? <p className="form-error" role="alert">{error}</p> : null}
        <div className="modal-actions">
          <button type="button" className="button secondary" onClick={onClose} disabled={busy}>
            Cancelar
          </button>
          <button type="button" className="button danger" disabled={busy}
                  onClick={() => void submit()}>
            {busy ? "Desvinculando…" : "Desvincular"}
          </button>
        </div>
      </div>
    </div>
  );
}
