"use client";

import { useState } from "react";
import { deshacerEnvioSat, type OrderDetail } from "../../lib/erpApi";
import { extractErrorMessage } from "../../lib/errors";

/** «Deshacer envío al SAT» (31/10/2026): saca el pedido de la Cola SAT y lo
 *  devuelve a «Por revisar», solo mientras el taller no ha empezado. Si ya lo
 *  prepara o lo ha embalado, se dice y no se puede. Vale para todos los
 *  pedidos. No toca FACTUSOL ni el cobro. */

const NO_SE_PUEDE: Record<string, string> = {
  preparing: "El taller ya está preparándolo: no se puede deshacer el envío al SAT.",
  packed: "El taller ya lo ha embalado: no se puede deshacer el envío al SAT.",
  blocked: "Está bloqueado en el taller: resuélvelo allí antes.",
};

export function DeshacerSat({
  order, onDone,
}: {
  order: OrderDetail;
  onDone: (o: OrderDetail, aviso: string | null) => void;
}) {
  const [abierto, setAbierto] = useState(false);
  const [motivo, setMotivo] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const prep = order.preparation_status;
  // Solo mientras el pedido está en el taller y sin envío: un pedido ya
  // enviado o completado no enseña nada.
  const enElTaller = ["in_queue", "preparing", "packed", "blocked"].includes(prep)
    && order.transport_status === "not_shipped" && !order.completed;
  if (!enElTaller) return null;
  const sePuede = prep === "in_queue";

  async function deshacer() {
    setBusy(true);
    setError(null);
    try {
      const r = await deshacerEnvioSat(order.id, motivo.trim() || undefined);
      setAbierto(false);
      onDone(r.order, r.aviso_correo);
    } catch (e) {
      setError(extractErrorMessage(e, "No se ha podido deshacer el envío al SAT."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="erp-deshacer-sat" aria-label="Deshacer envío al SAT">
      {!sePuede ? (
        <span className="muted small" role="note">{NO_SE_PUEDE[prep]}</span>
      ) : !abierto ? (
        <button type="button" className="button small secondary" onClick={() => setAbierto(true)}>
          Deshacer envío al SAT
        </button>
      ) : (
        <span className="erp-historico-acciones">
          <span className="small">Sale de la Cola SAT y vuelve a «Por revisar».</span>
          <input aria-label="Motivo" placeholder="Motivo (opcional)" value={motivo}
                 maxLength={255} onChange={(e) => setMotivo(e.target.value)} />
          <button type="button" className="button small" disabled={busy}
                  onClick={() => void deshacer()}>
            {busy ? "Deshaciendo…" : "Confirmar"}
          </button>
          <button type="button" className="button small secondary" disabled={busy}
                  onClick={() => setAbierto(false)}>
            Cancelar
          </button>
        </span>
      )}
      {error ? <p className="form-error" role="alert">{error}</p> : null}
    </div>
  );
}
