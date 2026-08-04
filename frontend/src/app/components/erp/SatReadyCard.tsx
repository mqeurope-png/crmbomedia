"use client";

import Link from "next/link";
import { useState } from "react";
import { extractErrorMessage } from "../../lib/errors";
import {
  fireTransition,
  listShippingFiles,
  markPickedUp,
  openShippingFile,
  type SatQueueItem,
  type ShipmentFileKind,
} from "../../lib/erpApi";

/** Card de «🚚 Listos para envío» (Fase D-1-fix1): pedido embalado pendiente de
 *  imprimir albarán/etiqueta y marcar recogido. Chips grandes táctiles + botón
 *  «Marcar recogido» con confirmación (evita mispulsados en tablet). */
export function SatReadyCard({
  order,
  onChanged,
}: {
  order: SatQueueItem;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function openDoc(kind: ShipmentFileKind) {
    try {
      const files = await listShippingFiles(order.id, kind);
      if (files[0]) await openShippingFile(files[0]);
    } catch {
      // si falla, el chip «Falta …» lleva a la ficha para subirlo
    }
  }

  async function recogido() {
    setBusy(true);
    setError(null);
    try {
      await markPickedUp(order.id);
      onChanged();
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo marcar recogido."));
      setBusy(false);
      setConfirming(false);
    }
  }

  async function reabrir() {
    setBusy(true);
    setError(null);
    try {
      await fireTransition(order.id, {
        domain: "preparation", to_status: "in_queue",
        reason: "Reapertura desde el taller",
        evidence: { reason: "Reapertura desde el taller" },
      });
      onChanged();
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo reabrir la preparación."));
      setBusy(false);
    }
  }

  return (
    <div className="sat-card sat-ready-card">
      <div className="sat-card-top">
        <span className="sat-card-num">{order.order_number}</span>
        <span className="muted small">
          {order.total_amount.toFixed(2)} {order.currency}
        </span>
      </div>

      <div className="sat-ready-chips">
        {order.has_albaran ? (
          <button type="button" className="sat-chip-btn ok"
                  onClick={() => openDoc("albaran")}>
            📄 Imprimir albarán
          </button>
        ) : (
          <Link href={`/erp/orders/${order.id}`} className="sat-chip-btn warn">
            📄 Falta albarán
          </Link>
        )}
        {order.has_etiqueta ? (
          <button type="button" className="sat-chip-btn ok"
                  onClick={() => openDoc("etiqueta")}>
            🏷️ Imprimir etiqueta
          </button>
        ) : (
          <Link href={`/erp/orders/${order.id}`} className="sat-chip-btn warn">
            🏷️ Falta etiqueta
          </Link>
        )}
      </div>

      {error ? <p className="form-error">{error}</p> : null}

      <div className="sat-ready-actions">
        {confirming ? (
          <div className="sat-confirm">
            <span>¿El paquete ha salido?</span>
            <button type="button" className="sat-btn pack" disabled={busy}
                    onClick={recogido}>
              Sí, recogido
            </button>
            <button type="button" className="button secondary small" disabled={busy}
                    onClick={() => setConfirming(false)}>
              No
            </button>
          </div>
        ) : (
          <button type="button" className="sat-btn pack" disabled={busy}
                  onClick={() => setConfirming(true)}>
            📤 Marcar recogido
          </button>
        )}
        <button type="button" className="button secondary small" disabled={busy}
                onClick={reabrir}>
          Reabrir preparación
        </button>
      </div>
    </div>
  );
}
