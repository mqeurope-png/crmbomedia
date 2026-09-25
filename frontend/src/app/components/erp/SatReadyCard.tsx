"use client";

import Link from "next/link";
import { useState } from "react";
import { extractErrorMessage } from "../../lib/errors";
import {
  customerLabel,
  fireTransition,
  listShippingFiles,
  markPickedUp,
  printShippingFile,
  setOrderTracking,
  uploadShippingFile,
  type SatQueueItem,
  type SeguimientoFieldsPatch,
  type ShipmentFileKind,
} from "../../lib/erpApi";
import { carrierDate, carrierStepTone } from "../../lib/geneiApi";
import { FileUploadButton } from "./FileUploadButton";
import { GeneiShipmentSection } from "./GeneiShipmentSection";
import { SatAlbaranChip, useSatAlbaranAction } from "./SatPreparingCard";
import { satShortDate } from "./SatQueueTable";
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

  /** Descarga el documento y lanza la impresión en el MISMO clic. */
  async function openDoc(kind: ShipmentFileKind) {
    setError(null);
    try {
      const files = await listShippingFiles(order.id, kind);
      if (files[0]) await printShippingFile(files[0]);
      else setError("No hay documento que imprimir todavía.");
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo abrir el documento."));
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
                title="Descarga la etiqueta y abre el diálogo de imprimir"
                onClick={() => openDoc("etiqueta")}>
          🖨 Imprimir etiqueta
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

/** Lote 5 · #3 — Nº de SEGUIMIENTO (tracking) de un pedido «listo»: se rellena
 *  y se GUARDA sin marcar el pedido recogido ni enviado (solo persiste el
 *  tracking para más tarde). Se siembra del pedido (`tracking_number`), se
 *  edita, se guarda con `setOrderTracking` (muestra «✓ Guardado») y al terminar
 *  refresca la cola (`onChanged`). Va junto a los chips de albarán/etiqueta en
 *  la card y en la fila de la vista lista. */
export function SatTrackingField({
  order,
  onChanged,
  compact = false,
}: {
  order: SatQueueItem;
  onChanged: () => void;
  compact?: boolean;
}) {
  const [value, setValue] = useState(order.tracking_number ?? "");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const r = await setOrderTracking(order.id, value.trim() || null);
      setValue(r.tracking_number ?? "");
      setSaved(true);
      onChanged();
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo guardar el nº de seguimiento."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={`sat-tracking${compact ? " is-compact" : ""}`}>
      <span className="sat-tech-label">Nº de seguimiento</span>
      <div className="sat-tracking-row">
        <input
          type="text"
          className="sat-tracking-input"
          aria-label="Nº de seguimiento"
          placeholder="Nº de seguimiento"
          value={value}
          maxLength={64}
          disabled={busy}
          onChange={(e) => { setValue(e.target.value); setSaved(false); }}
          onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); void save(); } }}
        />
        <button
          type="button"
          className={`button small${saved ? " secondary" : ""}`}
          aria-label="Guardar nº de seguimiento"
          disabled={busy}
          onClick={save}
        >
          {busy ? "Guardando…" : saved ? "✓ Guardado" : "Guardar"}
        </button>
      </div>
      {error ? <span className="form-error small" role="alert">{error}</span> : null}
    </div>
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
  canShip = false,
}: {
  order: SatQueueItem;
  onChanged: () => void;
  /** Lote 3: habilita la edición inline de los datos técnicos (admin/pedidos). */
  canEdit?: boolean;
  /** Genei (envíos): `Cap.SAT_SHIPPING`. Muestra «Crear envío con Genei» en la
   *  propia card (el pedido «Listo» ya está embalado, así que puede crearse). */
  canShip?: boolean;
}) {
  const actions = useSatReadyActions(order, onChanged);
  // Lote 3: actualización optimista de los campos de seguimiento tras editar.
  const [seg, setSeg] = useState<SeguimientoFieldsPatch | null>(null);
  const [showGenei, setShowGenei] = useState(false);
  // Con envío Genei ya creado no se ofrece «Crear»: se ofrece VERLO.
  const hasGenei = !!order.genei?.shipment_code;
  const pendienteRecogida = order.sat_tab === "pendiente_recogida";
  const serial = seg ? seg.serial_number : order.serial_number;
  const license = seg ? seg.whiterip_license : order.whiterip_license;
  const edit: SatTechEdit | undefined = canEdit
    ? { orderId: order.id, onSaved: setSeg }
    : undefined;

  return (
    <article className="sat-card sat-ready-card" aria-label={`Pedido ${order.order_number}`}>
      <div className="sat-card-top">
        <span className="sat-card-num">{order.order_number}</span>
        {/* Lote 5 · #4 — fecha del pedido bien visible en la cabecera. */}
        <span className="sat-card-date mono">{satShortDate(order.placed_at)}</span>
      </div>
      {/* Lote 5 · #4 — importe (se mantiene). */}
      <div className="sat-card-meta">
        <span className="sat-card-amount mono">
          {order.total_amount.toFixed(2)} {order.currency}
        </span>
        {pendienteRecogida ? (
          <span className="badge info" title="Etiqueta lista: falta que pase el transportista">
            Pendiente de recogida
          </span>
        ) : (
          <span className="badge ok">Embalado</span>
        )}
        {hasGenei && order.genei?.state_label ? (
          <span className="badge muted">Genei: {order.genei.state_label}</span>
        ) : null}
      </div>
      {customerLabel(order) ? (
        <div className="sat-card-customer">{customerLabel(order)}</div>
      ) : null}
      <SatObservaciones notes={order.notes} />
      <SatTechData serial={serial} license={license} edit={edit} />

      {actions.error ? <p className="form-error">{actions.error}</p> : null}

      <div className="sat-card-actions">
        <div className="sat-card-actions-primary">
          <SatPickupButton actions={actions} />
        </div>
        <div className="sat-card-actions-secondary">
          <SatReadyDocChips order={order} actions={actions} size="lg" />
        </div>
        {/* Lote 5 · #3 — nº de seguimiento, junto a la etiqueta. */}
        <SatTrackingField order={order} onChanged={onChanged} />
        {/* Esperando al transportista: su último escaneo REAL (vía Genei). */}
        <SatCarrierStatus order={order} />
        {/* Genei (PR-1 follow-up): crear el envío desde la propia Cola SAT, sin
            ir a la ficha. El pedido «Listo» ya está embalado, así que se puede
            crear. Reutiliza GeneiShipmentSection (comparador, etiqueta, estado). */}
        {canShip ? (
          <div className="sat-card-genei">
            <button type="button" className="button small" aria-expanded={showGenei}
                    onClick={() => setShowGenei((v) => !v)}>
              🚚 {showGenei ? "Ocultar envío Genei"
                : hasGenei ? "Ver envío Genei" : "Crear envío con Genei"}
            </button>
            {showGenei ? (
              <GeneiShipmentSection orderId={order.id} canManage={canShip} onChanged={onChanged} />
            ) : null}
          </div>
        ) : null}
        <div className="sat-card-actions-tertiary">
          <SatReopenButton actions={actions} />
        </div>
      </div>
    </article>
  );
}

/** Último escaneo REAL del transportista (Genei `/tracking`): el texto tal
 *  cual lo da la agencia, su fecha y el enlace a su web de seguimiento. Nada
 *  si aún no hay escaneos. */
export function SatCarrierStatus({ order }: { order: SatQueueItem }) {
  const g = order.genei;
  if (!g?.carrier_status) return null;
  const when = carrierDate(g.carrier_status_at);
  return (
    <p className="sat-carrier-status small" aria-label="Estado según el transportista">
      <span className={`badge ${carrierStepTone(g.carrier_step)}`}>{g.carrier_status}</span>
      {when ? <span className="muted"> · {when}</span> : null}
      {g.courier ? <span className="muted"> · {g.courier}</span> : null}
      {g.tracking_url ? (
        <>
          {" · "}
          <a href={g.tracking_url} target="_blank" rel="noopener noreferrer">
            Ver en la web de la agencia
          </a>
        </>
      ) : null}
    </p>
  );
}

/** Card de un pedido que YA SALIÓ (recogido / en tránsito / entregado) o que
 *  NO se envía («No requiere envío», pestaña «Sin envío»). Es lo que pintan
 *  «Enviados» y «Sin envío». */
export function SatShippedCard({ order }: { order: SatQueueItem }) {
  const tracking = order.tracking_number || order.genei?.tracking || null;
  const when = carrierDate(order.genei?.carrier_status_at);
  return (
    <article className="sat-card sat-shipped-card" aria-label={`Pedido ${order.order_number}`}>
      <div className="sat-card-top">
        <span className="sat-card-num">{order.order_number}</span>
        <span className="sat-card-date mono">{satShortDate(order.placed_at)}</span>
      </div>
      <div className="sat-card-meta">
        <span className={`badge ${satShippedTone(order)}`}
              title={order.genei?.carrier_status ? "Estado según el transportista" : undefined}>
          {satShippedLabel(order)}
        </span>
        {when ? <span className="muted small"> · {when}</span> : null}
      </div>
      {customerLabel(order) ? (
        <div className="sat-card-customer">{customerLabel(order)}</div>
      ) : null}
      {order.sin_envio ? null : (
        <dl className="sat-shipped-kv">
          <dt>Seguimiento</dt>
          <dd className="mono">
            {tracking && order.genei?.tracking_url ? (
              <a href={order.genei.tracking_url} target="_blank" rel="noopener noreferrer">
                {tracking}
              </a>
            ) : (tracking ?? "—")}
          </dd>
          {order.genei?.courier ? (<><dt>Agencia</dt><dd>{order.genei.courier}</dd></>) : null}
        </dl>
      )}
      <div className="sat-card-actions-secondary">
        <Link href={`/erp/orders/${order.id}`} className="button secondary lg">Ficha</Link>
      </div>
    </article>
  );
}

/** Estado de envío legible de un pedido enviado (o que no se envía). Manda el
 *  ÚLTIMO ESCANEO REAL del transportista, tal cual lo da la agencia (Genei
 *  `/tracking`); si aún no hay, el estado de Genei; y si tampoco, el del
 *  transporte. Antes salía siempre el genérico «Recogido · en tránsito», que
 *  podía ser falso (la agencia aún no lo había escaneado). */
export function satShippedLabel(order: SatQueueItem): string {
  if (order.sin_envio) return "No requiere envío";
  if (order.genei?.carrier_status) return order.genei.carrier_status;
  if (order.genei?.state_label) return order.genei.state_label;
  switch (order.transport_status) {
    case "delivered": return "Entregado";
    case "already_shipped_externally": return "Enviado (externo)";
    case "in_transit": return "Recogido · en tránsito";
    default: return "Enviado";
  }
}

/** Tono de la pastilla del estado de envío (por el paso real si lo hay). */
export function satShippedTone(order: SatQueueItem): string {
  if (order.sin_envio) return "muted";
  if (order.genei?.carrier_step) return carrierStepTone(order.genei.carrier_step);
  return order.transport_status === "delivered" ? "ok" : "info";
}
