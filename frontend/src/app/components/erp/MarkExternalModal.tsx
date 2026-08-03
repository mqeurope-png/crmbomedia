"use client";

import { useState } from "react";

/** B-2-fix4: confirmación de «Marcar como procesado externamente» — sirve
 *  para un pedido (count=1) o para una selección en bloque. Pide una nota
 *  opcional (motivo/referencia) que se guarda en el historial del pedido. */
export function MarkExternalModal({
  count,
  onConfirm,
  onCancel,
  busy,
}: {
  count: number;
  onConfirm: (note: string) => void;
  onCancel: () => void;
  busy?: boolean;
}) {
  const [note, setNote] = useState("");
  const title =
    count === 1
      ? "Marcar pedido como procesado externamente"
      : `Marcar ${count} pedidos como procesados externamente`;
  return (
    <div className="modal-overlay" role="dialog" aria-modal="true" aria-label={title}>
      <div className="modal-dialog">
        <h2>{title}</h2>
        <p className="muted small">
          Saldrá de las colas activas y sus 4 estados (pago, preparación,
          transporte y facturación) pasarán a «externalizado». Úsalo para
          pedidos gestionados fuera del ERP (Excel o proceso anterior).
        </p>
        <label className="field">
          <span>Nota (opcional)</span>
          <textarea
            value={note}
            rows={3}
            aria-label="Nota"
            placeholder="Motivo o referencia (Excel, proceso anterior…)"
            onChange={(e) => setNote(e.target.value)}
          />
        </label>
        <div className="modal-actions">
          <button
            type="button"
            className="button secondary"
            onClick={onCancel}
            disabled={busy}
          >
            Cancelar
          </button>
          <button
            type="button"
            className="button"
            onClick={() => onConfirm(note.trim())}
            disabled={busy}
          >
            {busy ? "Marcando…" : "Marcar como externalizado"}
          </button>
        </div>
      </div>
    </div>
  );
}
