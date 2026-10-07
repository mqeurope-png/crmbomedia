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
import { OTHER_COURIER_LABEL, suggestCourier } from "../../lib/couriers";
import {
  carrierDate, carrierStepTone, geneiFetchLabel, geneiLabelStatus, sendCustomerEmail,
} from "../../lib/geneiApi";
import { CourierSelect, CourierTrackingEditor, withSuggestion } from "./CourierFields";
import { FileUploadButton } from "./FileUploadButton";
import { GeneiShipmentSection } from "./GeneiShipmentSection";
import { SatAlbaranChip, useSatAlbaranAction } from "./SatPreparingCard";
import { satShortDate } from "./SatQueueTable";
import { SatObservaciones, SatTechData, type SatTechEdit } from "./SatTechData";

/** Acciones de un pedido «listo para envío», compartidas por la card y por la
 *  fila de la vista lista (Lote B6): imprimir albarán (mismo chip que en «Por
 *  embalar»: FACTUSOL › fichero › WooCommerce para los pedidos web, Lote 2 A3)
 *  / etiqueta, «Marcar recogido» con confirmación (evita mispulsados en
 *  tablet) y «Reabrir preparación». El nº de seguimiento y el COURIER (envío
 *  que no es de Genei) viven aquí: «Marcar recogido» los manda aunque no se
 *  hayan guardado aparte. */
export function useSatReadyActions(order: SatQueueItem, onChanged: () => void) {
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const albaran = useSatAlbaranAction(order, onChanged);
  const isGenei = !!order.genei?.shipment_code;
  const [tracking, setTracking] = useState(order.tracking_number ?? "");
  const [courier, setCourier] = useState(
    isGenei ? "" : (order.courier ?? suggestCourier(order.tracking_number) ?? ""),
  );

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
      // Genei trae su agencia y su tracking: ahí no se manda nada (como antes).
      await markPickedUp(order.id, isGenei ? {} : { tracking, courier });
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

  /** Traer la etiqueta de Genei desde la propia Cola SAT (sin ir a la ficha):
   *  queda adjunta en «Documentos de envío» y, al refrescar la cola, el chip
   *  pasa a «Imprimir etiqueta». Normalmente llega sola al tramitarse; esto es
   *  para cuando Genei tardó más de la cuenta. */
  const [fetchingLabel, setFetchingLabel] = useState(false);
  async function traerEtiqueta() {
    setFetchingLabel(true);
    setError(null);
    try {
      await geneiFetchLabel(order.id);
      onChanged();
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo traer la etiqueta de Genei."));
    } finally {
      setFetchingLabel(false);
    }
  }

  return {
    busy, confirming, setConfirming,
    isGenei, tracking, setTracking, courier, setCourier,
    // Un solo aviso bajo la card: el de recogido/reabrir o el del albarán.
    error: error ?? albaran.error,
    factusolAlbaran: albaran.factusolAlbaran,
    albaran, openDoc, recogido, reabrir, uploadEtiqueta, traerEtiqueta, fetchingLabel,
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
  const { albaran, openDoc, uploadEtiqueta, traerEtiqueta, fetchingLabel } = actions;
  const lg = size === "lg" ? " lg" : "";
  // Envío Genei tramitado sin etiqueta adjunta: se puede traer de Genei aquí.
  const geneiLabel = !order.has_etiqueta && !!order.genei?.label_available;
  const labelStatus = geneiLabel ? geneiLabelStatus({
    attached: false, available: true, auto: order.genei?.label_auto,
  }) : null;
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
        <>
          {geneiLabel ? (
            <button type="button" className={`sat-chip-btn warn${lg}`}
                    disabled={fetchingLabel}
                    title={labelStatus?.text ?? "Trae la etiqueta de Genei y la deja adjunta"}
                    onClick={() => void traerEtiqueta()}>
              {fetchingLabel ? "Trayendo etiqueta…" : "📥 Traer etiqueta de Genei"}
            </button>
          ) : null}
          {/* Lote 4 · #5 — el pedido está embalado/listo pero falta la etiqueta:
              se sube aquí mismo (mismo flujo que la ficha) en vez de mandar al
              operario a la ficha del pedido. */}
          <FileUploadButton
            label="🏷️ Subir etiqueta"
            className={`sat-chip-btn warn${lg}`}
            onFile={uploadEtiqueta}
          />
          {labelStatus ? (
            <span className={`badge ${labelStatus.tone} small`} aria-label="Estado de la etiqueta">
              {labelStatus.text}
            </span>
          ) : null}
        </>
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
 *  la card y en la fila de la vista lista.
 *
 *  Envío con OTRO courier (sin Genei): al lado va el desplegable «Courier»
 *  (UPS, CTT Express… u «Otro»), que se propone solo por el formato del
 *  tracking (`1Z…` → UPS, `0033…` → CTT Express) y se puede cambiar. El valor
 *  vive en `actions`, así «Marcar recogido» lo lleva aunque no se guarde. */
export function SatTrackingField({
  order,
  actions,
  onChanged,
  compact = false,
}: {
  order: SatQueueItem;
  actions: ReturnType<typeof useSatReadyActions>;
  onChanged: () => void;
  compact?: boolean;
}) {
  const { isGenei, tracking: value, setTracking: setValue, courier, setCourier } = actions;
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const r = await setOrderTracking(
        order.id, value.trim() || null, isGenei ? undefined : courier.trim(),
      );
      setValue(r.tracking_number ?? "");
      if (!isGenei && r.courier !== undefined) setCourier(r.courier ?? "");
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
          onChange={(e) => {
            const t = e.target.value;
            setValue(t);
            if (!isGenei) setCourier((c) => withSuggestion(t, c));
            setSaved(false);
          }}
          onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); void save(); } }}
        />
        {isGenei ? null : (
          <CourierSelect value={courier} disabled={busy} compact={compact}
                         onChange={(c) => { setCourier(c); setSaved(false); }} />
        )}
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
        {pendienteRecogida && geneiPendienteDeEntrada(order) ? (
          /* Genei sin escaneo del transportista: ámbar, con Genei detrás. */
          <span className="badge warn"
                title="Etiqueta lista: el transportista aún no ha escaneado el paquete">
            {PENDIENTE_ENTRADA_RED}
          </span>
        ) : pendienteRecogida ? (
          /* Mismo criterio de color que «Enviados»: Genei azul, otro courier
             verde azulado (con su nombre si ya se sabe). */
          <span className={`badge ${hasGenei ? "info" : "courier-ext"}`}
                title="Etiqueta lista: falta que pase el transportista">
            Pendiente de recogida{!hasGenei && order.courier ? ` · ${order.courier}` : ""}
          </span>
        ) : (
          <span className="badge ok">Embalado</span>
        )}
        {hasGenei && order.genei?.state_label ? (
          <span className="badge muted">Genei: {order.genei.state_label}</span>
        ) : null}
      </div>
      {/* Aún sin recoger, el aviso solo se puede mandar con un envío Genei (el
          de OTRO courier sale al «📤 Marcar recogido»). */}
      {pendienteRecogida && hasGenei && (order.tracking_number || order.genei?.tracking) ? (
        <SatAvisoCliente order={order} canShip={canShip} onChanged={onChanged} />
      ) : null}
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
        <SatTrackingField order={order} actions={actions} onChanged={onChanged} />
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
 *  «Enviados» y «Sin envío». Con OTRO courier, courier y tracking se corrigen
 *  aquí mismo (✎), sin volver a «Marcar recogido». */
export function SatShippedCard({
  order, onChanged,
}: {
  order: SatQueueItem;
  onChanged?: () => void;
}) {
  const when = carrierDate(order.genei?.carrier_status_at);
  return (
    <article className="sat-card sat-shipped-card" aria-label={`Pedido ${order.order_number}`}>
      <div className="sat-card-top">
        <span className="sat-card-num">{order.order_number}</span>
        <span className="sat-card-date mono">{satShortDate(order.placed_at)}</span>
      </div>
      <div className="sat-card-meta">
        <SatShipmentBadge order={order} />
        {when ? <span className="muted small"> · {when}</span> : null}
      </div>
      {customerLabel(order) ? (
        <div className="sat-card-customer">{customerLabel(order)}</div>
      ) : null}
      {order.sin_envio ? null : (
        <dl className="sat-shipped-kv">
          <dt>Seguimiento</dt>
          <dd className="mono"><SatTrackingLink order={order} /></dd>
          {satCourier(order) ? (<><dt>Agencia</dt><dd>{satCourier(order)}</dd></>) : null}
        </dl>
      )}
      {onChanged ? <SatExternalShipmentEdit order={order} onChanged={onChanged} /> : null}
      <div className="sat-card-actions-secondary">
        <Link href={`/erp/orders/${order.id}`} className="button secondary lg">Ficha</Link>
      </div>
    </article>
  );
}

/** El estado mientras el transportista no ha escaneado el paquete. */
export const PENDIENTE_ENTRADA_RED = "Pendiente de entrada en red";

/** Estados de Genei de ANTES de la red del transportista: 7 pendiente de
 *  pago, 6 pendiente de tramitar, 1 tramitado, 2 pendiente de depositar y 5
 *  «recogida efectuada / en tránsito» (lo dice al recoger, antes del primer
 *  escaneo de la agencia). Igual que la hoja (`GENEI_ANTES_DE_LA_RED`). */
const GENEI_ANTES_DE_LA_RED = [7, 6, 1, 2, 5];

/** ¿Envío de Genei SIN ningún escaneo del transportista (`estadosAgencia`
 *  vacío), sin entregar ni incidencia, y con Genei aún en un estado de antes
 *  de la red? Entonces el paquete no ha entrado en la red de la agencia,
 *  aunque Genei diga «recogida efectuada» o ya se haya marcado recogido:
 *  «Pendiente de entrada en red» (ámbar), nunca «en tránsito». En cuanto hay
 *  escaneo, manda el escaneo; si Genei ya dice «en reparto»…, lo de Genei. */
export function geneiPendienteDeEntrada(order: SatQueueItem): boolean {
  if (order.sin_envio || order.shipment_kind === "externo") return false;
  const g = order.genei;
  if (!g?.shipment_code || g.carrier_status) return false;
  if (["delivered", "incident", "returned"].includes(order.transport_status)) return false;
  // Sin etiqueta ni recogida, solo si el envío ya está tramitado (como la hoja).
  if (order.transport_status === "not_shipped"
      && !["ready", "in_transit"].includes(g.state_bucket ?? "")) return false;
  if (g.state_code != null) return GENEI_ANTES_DE_LA_RED.includes(Number(g.state_code));
  return ["", "created", "processing", "ready"].includes(g.state_bucket ?? "");
}

/** ¿El aviso de envío al cliente ya salió alguna vez? */
export function avisoEnviado(order: SatQueueItem): boolean {
  const ce = order.customer_email;
  return ce?.status === "sent" || !!ce?.sent_at;
}

/** Columna «Aviso» de «Enviados» (y de «Pendiente de recogida» con tracking):
 *  «✉ Enviado 01/10, 12:03» (verde, con el destinatario en el tooltip) o
 *  «✉ Sin enviar» (gris). Con nº de seguimiento y permiso de envíos, «Enviar
 *  aviso» / «Reenviar» con una confirmación de una línea: el MISMO endpoint,
 *  plantilla e idioma que el botón de la ficha (queda en la auditoría del
 *  pedido, con fecha y usuario). Sin tracking no hay botón. */
export function SatAvisoCliente({
  order, canShip, onChanged,
}: {
  order: SatQueueItem;
  canShip: boolean;
  onChanged?: () => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ce = order.customer_email;
  const enviado = avisoEnviado(order);
  const tracking = order.tracking_number || order.genei?.tracking;
  const accion = enviado ? "Reenviar" : "Enviar aviso";

  async function enviar() {
    setBusy(true);
    setError(null);
    try {
      await sendCustomerEmail(order.id);
      setConfirming(false);
      onChanged?.();
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo enviar el aviso."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="sat-aviso" aria-label={`Aviso al cliente de ${order.order_number}`}>
      <span
        className={`badge ${enviado ? "ok" : "muted"}`}
        title={enviado
          ? `Aviso enviado${ce?.to ? ` a ${ce.to}` : ""}${(ce?.sends ?? 0) > 1 ? ` · ${ce?.sends} envíos` : ""}`
          : "Aún no se ha mandado el aviso de envío al cliente"}
      >
        ✉ {enviado ? `Enviado ${carrierDate(ce?.sent_at)}`.trim() : "Sin enviar"}
      </span>
      {canShip && tracking ? (
        confirming ? (
          <span className="sat-aviso-confirm" role="group" aria-label={`Confirmar ${accion}`}>
            <span className="small">
              ¿{enviado ? "Reenviar" : "Enviar"} el aviso al cliente{ce?.to ? ` (${ce.to})` : ""}?
            </span>{" "}
            <button type="button" className="button small" disabled={busy}
                    onClick={() => void enviar()}>
              {busy ? "Enviando…" : "Sí, enviar"}
            </button>{" "}
            <button type="button" className="button small secondary" disabled={busy}
                    onClick={() => setConfirming(false)}>
              No
            </button>
          </span>
        ) : (
          <button type="button" className="button small secondary"
                  aria-label={`${accion} al cliente de ${order.order_number}`}
                  onClick={() => { setError(null); setConfirming(true); }}>
            {accion}
          </button>
        )
      ) : null}
      {error ? <p className="form-error small" role="alert">{error}</p> : null}
    </div>
  );
}

/** Estado de envío legible de un pedido enviado (o que no se envía). Manda el
 *  ÚLTIMO ESCANEO REAL del transportista, tal cual lo da la agencia (Genei
 *  `/tracking`); si aún no hay, el estado de Genei; y si tampoco, el del
 *  transporte. Antes salía siempre el genérico «Recogido · en tránsito», que
 *  podía ser falso (la agencia aún no lo había escaneado). */
export function satShippedLabel(order: SatQueueItem): string {
  if (order.sin_envio) return "No requiere envío";
  // Envío con OTRO courier (sin Genei): «Enviado · UPS» / «Enviado · otro
  // courier» — nunca «Recogido · en tránsito» (BoHub no sigue su tracking).
  if (order.shipment_kind === "externo") {
    const courier = order.courier || OTHER_COURIER_LABEL;
    if (order.transport_status === "delivered") return `Entregado · ${courier}`;
    if (order.transport_status === "already_shipped_externally" && !order.courier) {
      return "Enviado (externo)";
    }
    return `Enviado · ${courier}`;
  }
  if (order.genei?.carrier_status) return order.genei.carrier_status;
  if (geneiPendienteDeEntrada(order)) return PENDIENTE_ENTRADA_RED;
  if (order.genei?.state_label) return order.genei.state_label;
  switch (order.transport_status) {
    case "delivered": return "Entregado";
    case "already_shipped_externally": return "Enviado (externo)";
    case "in_transit": return "Recogido · en tránsito";
    default: return "Enviado";
  }
}

/** Tono de la pastilla del estado de envío (por el paso real si lo hay). Los
 *  envíos con OTRO courier van en su propio color (verde azulado) para
 *  distinguirlos a simple vista de los de Genei (azul). */
export function satShippedTone(order: SatQueueItem): string {
  if (order.sin_envio) return "muted";
  if (order.shipment_kind === "externo") return "courier-ext";
  if (order.genei?.carrier_step) return carrierStepTone(order.genei.carrier_step);
  if (geneiPendienteDeEntrada(order)) return "warn";
  return order.transport_status === "delivered" ? "ok" : "info";
}

/** Courier del envío (agencia de Genei o el apuntado a mano), o null. */
export function satCourier(order: SatQueueItem): string | null {
  return order.courier || order.genei?.courier || null;
}

/** Pastilla del estado de envío + la etiqueta «Genei» si va por Genei. */
export function SatShipmentBadge({ order }: { order: SatQueueItem }) {
  const genei = !order.sin_envio && order.shipment_kind === "genei";
  return (
    <>
      <span className={`badge ${satShippedTone(order)}`}
            title={order.genei?.carrier_status ? "Estado según el transportista" : undefined}>
        {satShippedLabel(order)}
      </span>
      {genei ? (
        <span className="sat-genei-tag" title="Envío tramitado con Genei">Genei</span>
      ) : null}
      <SatGeneiSecundario order={order} />
    </>
  );
}

/** Lo que dice Genei, en segundo plano, mientras el transportista no ha
 *  escaneado el paquete («Genei: Recogida efectuada / en tránsito»). */
export function SatGeneiSecundario({ order }: { order: SatQueueItem }) {
  if (!geneiPendienteDeEntrada(order) || !order.genei?.state_label) return null;
  return <span className="badge muted small">Genei: {order.genei.state_label}</span>;
}

/** Nº de seguimiento, enlazado a la web del courier si se conoce (la de Genei
 *  o la de la tabla por courier); «—» si no hay. */
export function SatTrackingLink({ order }: { order: SatQueueItem }) {
  const tracking = order.tracking_number || order.genei?.tracking || null;
  const url = order.tracking_url || order.genei?.tracking_url || null;
  if (!tracking) return <>—</>;
  return url ? (
    <a href={url} target="_blank" rel="noopener noreferrer">{tracking}</a>
  ) : <>{tracking}</>;
}

/** ✎ Courier y nº de seguimiento de un envío con OTRO courier ya recogido,
 *  editables en línea («Enviados»): al guardar se refresca la lista (la hoja
 *  y el aviso al cliente se actualizan en el backend). Nada para Genei. */
export function SatExternalShipmentEdit({
  order, onChanged,
}: {
  order: SatQueueItem;
  onChanged: () => void;
}) {
  const [editing, setEditing] = useState(false);
  if (order.sin_envio || order.shipment_kind !== "externo") return null;
  if (!editing) {
    return (
      <button type="button" className="button small secondary sat-ext-edit"
              aria-label={`Editar courier y seguimiento de ${order.order_number}`}
              onClick={() => setEditing(true)}>
        ✎ Courier / seguimiento
      </button>
    );
  }
  return (
    <CourierTrackingEditor
      orderId={order.id}
      tracking={order.tracking_number ?? null}
      courier={order.courier ?? null}
      onSaved={() => { setEditing(false); onChanged(); }}
      onCancel={() => setEditing(false)}
    />
  );
}
