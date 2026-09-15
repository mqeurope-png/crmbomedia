"use client";

import Link from "next/link";
import { useState } from "react";
import { extractErrorMessage } from "../../lib/errors";
import {
  customerLabel,
  fireTransition,
  listShippingFiles,
  markPickedUp,
  openShippingFile,
  type SatQueueItem,
  type ShipmentFileKind,
} from "../../lib/erpApi";
import { SatAlbaranChip, useSatAlbaranAction } from "./SatPreparingCard";

/** Acciones de un pedido «listo para envío», compartidas por la card y por la
 *  fila de la vista lista (Lote B6): imprimir albarán (mismo chip que en «Por
 *  embalar»: FACTUSOL › fichero › WooCommerce para los pedidos web, Lote 2 A3)
 *  / etiqueta, «Marcar recogido» con confirmación (evita mispulsados en
 *  tablet) y «Reabrir preparación». */
export function useSatReadyActions(order: SatQueueItem, onChanged: () => void) {
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const albaran = useSatAlbaranAction(order, onChanged);

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

  return {
    busy, confirming, setConfirming,
    // Un solo aviso bajo la card: el de recogido/reabrir o el del albarán.
    error: error ?? albaran.error,
    factusolAlbaran: albaran.factusolAlbaran,
    albaran, openDoc, recogido, reabrir,
  };
}

/** Chips de albarán/etiqueta de un pedido listo (card y fila lista). */
export function SatReadyDocChips({
  order,
  actions,
}: {
  order: SatQueueItem;
  actions: ReturnType<typeof useSatReadyActions>;
}) {
  const { albaran, openDoc } = actions;
  return (
    <>
      <SatAlbaranChip order={order} albaran={albaran} />
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
    </>
  );
}

/** Botones «Marcar recogido» (con confirmación) + «Reabrir preparación»
 *  (card y fila lista). */
export function SatReadyButtons({
  actions,
  compact = false,
}: {
  actions: ReturnType<typeof useSatReadyActions>;
  compact?: boolean;
}) {
  const { busy, confirming, setConfirming, recogido, reabrir } = actions;
  const pickCls = compact ? "button small" : "sat-btn pack";
  return (
    <>
      {confirming ? (
        <div className="sat-confirm">
          <span>¿El paquete ha salido?</span>
          <button type="button" className={pickCls} disabled={busy}
                  onClick={recogido}>
            Sí, recogido
          </button>
          <button type="button" className="button secondary small" disabled={busy}
                  onClick={() => setConfirming(false)}>
            No
          </button>
        </div>
      ) : (
        <button type="button" className={pickCls} disabled={busy}
                onClick={() => setConfirming(true)}>
          📤 Marcar recogido
        </button>
      )}
      <button type="button" className="button secondary small" disabled={busy}
              onClick={reabrir}>
        Reabrir preparación
      </button>
    </>
  );
}

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
  const actions = useSatReadyActions(order, onChanged);

  return (
    <div className="sat-card sat-ready-card">
      <div className="sat-card-top">
        <span className="sat-card-num">{order.order_number}</span>
        <span className="muted small">
          {order.total_amount.toFixed(2)} {order.currency}
        </span>
      </div>
      {customerLabel(order) ? (
        <div className="sat-card-customer">{customerLabel(order)}</div>
      ) : null}

      <div className="sat-ready-chips">
        <SatReadyDocChips order={order} actions={actions} />
      </div>

      {actions.error ? <p className="form-error">{actions.error}</p> : null}

      <div className="sat-ready-actions">
        <SatReadyButtons actions={actions} />
      </div>
    </div>
  );
}
