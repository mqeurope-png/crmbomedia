"use client";

import { useState } from "react";
import { extractErrorMessage } from "../../lib/errors";
import {
  customerLabel,
  fireTransition,
  listShippingFiles,
  markPickedUp,
  openShippingFile,
  uploadShippingFile,
  type SatQueueItem,
  type SeguimientoFieldsPatch,
  type ShipmentFileKind,
} from "../../lib/erpApi";
import { FileUploadButton } from "./FileUploadButton";
import { SatAlbaranChip, useSatAlbaranAction } from "./SatPreparingCard";
import { SatObservaciones, SatTechData, type SatTechEdit } from "./SatTechData";

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

  /** Lote 4 · #5 — subir la etiqueta desde la propia Cola SAT (card y fila),
   *  reutilizando el mismo helper que «Documentos de envío» de la ficha: el
   *  backend guarda el fichero y, con el pedido ya embalado, aplica el arco
   *  `not_shipped → label_created` (el antiguo «Crear envío»). Al terminar se
   *  refresca la cola, así el chip deja de decir «Falta etiqueta» y pasa a
   *  «Imprimir etiqueta» (has_etiqueta true). El error lo enseña el propio
   *  `FileUploadButton`, por eso aquí no se captura. */
  async function uploadEtiqueta(file: File) {
    await uploadShippingFile(order.id, "etiqueta", file);
    onChanged();
  }

  return {
    busy, confirming, setConfirming,
    // Un solo aviso bajo la card: el de recogido/reabrir o el del albarán.
    error: error ?? albaran.error,
    factusolAlbaran: albaran.factusolAlbaran,
    albaran, openDoc, recogido, reabrir, uploadEtiqueta,
  };
}

/** Chips de albarán/etiqueta de un pedido listo (card y fila lista).
 *  `size="lg"`: 48 px, para la fila de secundarios de la card. */
export function SatReadyDocChips({
  order,
  actions,
  size,
}: {
  order: SatQueueItem;
  actions: ReturnType<typeof useSatReadyActions>;
  size?: "lg";
}) {
  const { albaran, openDoc, uploadEtiqueta } = actions;
  const lg = size === "lg" ? " lg" : "";
  return (
    <>
      <SatAlbaranChip order={order} albaran={albaran} size={size} />
      {order.has_etiqueta ? (
        <button type="button" className={`sat-chip-btn ok${lg}`}
                onClick={() => openDoc("etiqueta")}>
          🏷️ Imprimir etiqueta
        </button>
      ) : (
        /* Lote 4 · #5 — el pedido está embalado/listo pero falta la etiqueta:
           se sube aquí mismo (mismo flujo que la ficha) en vez de mandar al
           operario a la ficha del pedido. */
        <FileUploadButton
          label="🏷️ Subir etiqueta"
          className={`sat-chip-btn warn${lg}`}
          onFile={uploadEtiqueta}
        />
      )}
    </>
  );
}

/** «Marcar recogido» con confirmación (evita mispulsados en tablet). En la
 *  card es el primario de 48 px a todo el ancho; `compact` = fila de tabla. */
export function SatPickupButton({
  actions,
  compact = false,
}: {
  actions: ReturnType<typeof useSatReadyActions>;
  compact?: boolean;
}) {
  const { busy, confirming, setConfirming, recogido } = actions;
  const pickCls = compact ? "button small" : "button lg sat-card-primary";
  const noCls = compact ? "button secondary small" : "button secondary lg";
  if (confirming) {
    return (
      <div className="sat-confirm">
        <span>¿El paquete ha salido?</span>
        <button type="button" className={pickCls} disabled={busy} onClick={recogido}>
          Sí, recogido
        </button>
        <button type="button" className={noCls} disabled={busy}
                onClick={() => setConfirming(false)}>
          No
        </button>
      </div>
    );
  }
  return (
    <button type="button" className={pickCls} disabled={busy}
            onClick={() => setConfirming(true)}>
      📤 Marcar recogido
    </button>
  );
}

/** «Reabrir preparación»: acción correctiva, poco frecuente. En la card va
 *  aparte y sin caja (terciario), pero igual de alta (48 px). */
export function SatReopenButton({
  actions,
  compact = false,
}: {
  actions: ReturnType<typeof useSatReadyActions>;
  compact?: boolean;
}) {
  const { busy, reabrir } = actions;
  return (
    <button type="button" className={compact ? "button secondary small" : "button tertiary lg"}
            disabled={busy} onClick={reabrir}>
      Reabrir preparación
    </button>
  );
}

/** Botones «Marcar recogido» (con confirmación) + «Reabrir preparación»
 *  seguidos (fila de la vista lista). */
export function SatReadyButtons({
  actions,
  compact = false,
}: {
  actions: ReturnType<typeof useSatReadyActions>;
  compact?: boolean;
}) {
  return (
    <>
      <SatPickupButton actions={actions} compact={compact} />
      <SatReopenButton actions={actions} compact={compact} />
    </>
  );
}

/** Card de «🚚 Listos para envío» (Lote 2 · PR-2, revisión de diseño §8):
 *  pedido embalado pendiente de imprimir albarán/etiqueta y marcar recogido.
 *  Mismo orden de lectura que «Por embalar» (observaciones del comercial en
 *  ámbar arriba, datos técnicos grandes con «copiar») y tres acciones de 48 px
 *  en dos filas: «Marcar recogido» (primario, con confirmación) y debajo,
 *  separados, imprimir albarán y etiqueta. «Reabrir preparación» queda aparte
 *  como terciario: es correctiva y no debe pulsarse por error. */
export function SatReadyCard({
  order,
  onChanged,
  canEdit = false,
  origins,
}: {
  order: SatQueueItem;
  onChanged: () => void;
  /** Lote 3: habilita la edición inline de los datos técnicos (admin/pedidos). */
  canEdit?: boolean;
  /** Catálogo de orígenes del envío para el desplegable (mismo que la ficha). */
  origins?: string[];
}) {
  const actions = useSatReadyActions(order, onChanged);
  // Lote 3: actualización optimista de los campos de seguimiento tras editar.
  const [seg, setSeg] = useState<SeguimientoFieldsPatch | null>(null);
  const serial = seg ? seg.serial_number : order.serial_number;
  const license = seg ? seg.whiterip_license : order.whiterip_license;
  const origin = seg ? seg.shipping_origin : order.shipping_origin;
  const edit: SatTechEdit | undefined = canEdit
    ? { orderId: order.id, origins, onSaved: setSeg }
    : undefined;

  return (
    <article className="sat-card sat-ready-card" aria-label={`Pedido ${order.order_number}`}>
      <div className="sat-card-top">
        <span className="sat-card-num">{order.order_number}</span>
        <span className="muted small mono">
          {order.total_amount.toFixed(2)} {order.currency}
        </span>
      </div>
      {customerLabel(order) ? (
        <div className="sat-card-customer">{customerLabel(order)}</div>
      ) : null}
      <SatObservaciones notes={order.notes} />
      <SatTechData serial={serial} license={license} origin={origin} edit={edit} />

      {actions.error ? <p className="form-error">{actions.error}</p> : null}

      <div className="sat-card-actions">
        <div className="sat-card-actions-primary">
          <SatPickupButton actions={actions} />
        </div>
        <div className="sat-card-actions-secondary">
          <SatReadyDocChips order={order} actions={actions} size="lg" />
        </div>
        <div className="sat-card-actions-tertiary">
          <SatReopenButton actions={actions} />
        </div>
      </div>
    </article>
  );
}
