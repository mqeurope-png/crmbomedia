"use client";

import Link from "next/link";
import { useState } from "react";
import {
  customerLabel,
  downloadOrderFactusolAlbaranPdf,
  fetchAlbaranFromWoo,
  listShippingFiles,
  openShippingFile,
  saveBlob,
  STATUS_LABELS,
  type SatAlbaranSource,
  type SatQueueItem,
  type SeguimientoFieldsPatch,
} from "../../lib/erpApi";
import { satShortDate } from "./SatQueueTable";
import { SatObservaciones, SatTechData, type SatTechEdit } from "./SatTechData";

/** Estado del chip de albarán de un pedido de la cola (Lote 2 A3), derivado
 *  del contrato del backend (`albaran_source`, prioridad factusol › file › woo):
 *  - `factusol`: PDF del albarán que BoHub creó en FACTUSOL (pedidos manuales /
 *    de FACTUSOL) — el mismo que la ficha y que el email al SAT;
 *  - `file`: fichero vigente (subido a mano o ya descargado de Woo);
 *  - `woo`: pedido WEB sin fichero aún — se descarga de WooCommerce (mu-plugin
 *    de la tienda) y se abre. El albarán de un pedido web NUNCA lo crea
 *    FACTUSOL, así que aquí jamás se dice «Falta albarán»;
 *  - sin fuente: manual → «Falta albarán» (crear en FACTUSOL o subir desde la
 *    ficha); web → «Albarán de WooCommerce no disponible» + motivo del backend. */
export type SatAlbaranState = {
  source: SatAlbaranSource | null;
  /** Hay albarán que imprimir o descargar. */
  hasAlbaran: boolean;
  /** Por qué no lo hay: pedido manual sin albarán, o web sin descarga posible. */
  missing: "manual" | "woo_unavailable" | null;
  /** Motivo del backend cuando es web y la descarga de Woo no es posible. */
  reason: string | null;
  label: string;
  title: string | undefined;
  tone: "ok" | "info" | "warn";
};

const FILE_TITLES: Record<string, string> = {
  manual_upload: "Abre el albarán subido a mano",
  woo_pdf_plugin: "Abre el albarán de WooCommerce (PDF de la tienda)",
  crm_generated_pdf: "Abre el albarán generado por BoHub con los datos de WooCommerce",
};

export function satAlbaranState(order: SatQueueItem): SatAlbaranState {
  const source = order.albaran_source;
  const tienda = order.store_slug ? ` (tienda «${order.store_slug}»)` : "";
  if (source === "factusol") {
    return {
      source, hasAlbaran: true, missing: null, reason: null, tone: "ok",
      label: "📄 Imprimir albarán",
      title: `Descarga el albarán ${order.factusol_albaran_number} de FACTUSOL en PDF`,
    };
  }
  if (source === "file") {
    return {
      source, hasAlbaran: true, missing: null, reason: null, tone: "ok",
      label: "📄 Imprimir albarán",
      title: FILE_TITLES[order.albaran_file_source ?? ""] ?? "Abre el albarán guardado",
    };
  }
  if (source === "woo") {
    return {
      source, hasAlbaran: true, missing: null, reason: null, tone: "info",
      label: "📄 Descargar albarán",
      title: `Descarga el albarán generado por WooCommerce${tienda} y lo abre`,
    };
  }
  if (order.is_web_order) {
    const reason = order.woo_albaran_unavailable_reason
      ?? "No se puede descargar de la tienda.";
    return {
      source: null, hasAlbaran: false, missing: "woo_unavailable", reason,
      tone: "warn", label: "📄 Albarán de WooCommerce no disponible", title: reason,
    };
  }
  return {
    source: null, hasAlbaran: false, missing: "manual", reason: null, tone: "warn",
    label: "📄 Falta albarán",
    title: "Crea el albarán en FACTUSOL o súbelo a mano desde la ficha",
  };
}

/** Acción «albarán» de un pedido de la cola, compartida por las cards («Por
 *  embalar» y «Listos») y por las filas de la vista lista (Lote B6): la ruta la
 *  decide `satAlbaranState` (FACTUSOL manda sobre el fichero; un pedido web sin
 *  fichero se descarga de Woo) y, cuando no hay albarán, la pulsación explica
 *  qué hacer — en la tablet del taller no hay tooltip que valga. */
export function useSatAlbaranAction(order: SatQueueItem, onChanged: () => void) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const state = satAlbaranState(order);
  const factusolAlbaran = order.factusol_albaran_number ?? null;

  async function albaranClick(e: React.SyntheticEvent) {
    e.preventDefault();
    e.stopPropagation();
    setError(null);
    if (state.source === "factusol") {
      setBusy(true);
      try {
        const blob = await downloadOrderFactusolAlbaranPdf(order.id);
        saveBlob(blob, `Albaran_${factusolAlbaran}.pdf`);
      } catch {
        setError(
          "No se pudo generar el PDF del albarán de FACTUSOL. Revisa la ficha del pedido.",
        );
      } finally {
        setBusy(false);
      }
      return;
    }
    if (state.source === "file") {
      try {
        const files = await listShippingFiles(order.id, "albaran");
        if (files[0]) await openShippingFile(files[0]);
        else setError("No se encontró el albarán guardado. Revisa la ficha del pedido.");
      } catch {
        setError("No se pudo abrir el albarán. Revisa la ficha del pedido.");
      }
      return;
    }
    if (state.source === "woo") {
      // Pedido web: descarga (mu-plugin → reportlab), refresca y auto-abre.
      setBusy(true);
      try {
        const r = await fetchAlbaranFromWoo(order.id);
        onChanged();
        await openShippingFile(r.file);
      } catch {
        setError(
          "No se pudo descargar el albarán de WooCommerce. Reintenta o súbelo a "
          + "mano desde la ficha.",
        );
      } finally {
        setBusy(false);
      }
      return;
    }
    // Sin albarán: la pulsación no navega (la card entera ya es un enlace),
    // explica qué hacer y el aviso enlaza a la ficha.
    setError(
      state.missing === "woo_unavailable"
        ? `Albarán de WooCommerce no disponible: ${state.reason}`
        : "Este pedido no tiene albarán: créalo en FACTUSOL o súbelo a mano desde la ficha.",
    );
  }

  return {
    ...state,
    busy,
    error,
    albaranClick,
    factusolAlbaran,
    label: busy ? "Descargando…" : state.label,
  };
}

/** Chip de albarán (card «Por embalar», card «Listos» y filas de la lista).
 *  `explainInPlace`: en la card de «Por embalar» los estados sin albarán no
 *  navegan a la ficha (el taller no la edita): al pulsar explican qué hacer y
 *  el aviso enlaza a la ficha. Fuera de ahí, «Falta albarán» / «no disponible»
 *  enlazan a la ficha, como siempre. Con un pedido web sin descarga posible
 *  se enseña además el motivo. `size="lg"`: 48 px (fila de acciones de la
 *  card, Lote 2 · PR-2). */
export function SatAlbaranChip({
  order,
  albaran,
  explainInPlace = false,
  size,
}: {
  order: SatQueueItem;
  albaran: ReturnType<typeof useSatAlbaranAction>;
  explainInPlace?: boolean;
  size?: "lg";
}) {
  const { busy, albaranClick, hasAlbaran, missing, reason, label, title, tone } = albaran;
  const cls = `sat-chip-btn ${tone}${size === "lg" ? " lg" : ""}`;
  let chip: React.ReactNode;
  if (hasAlbaran || explainInPlace) {
    chip = (
      <button type="button" className={cls} disabled={busy} title={title}
              onClick={albaranClick}>
        {label}
      </button>
    );
  } else {
    chip = (
      <Link href={`/erp/orders/${order.id}`} className={cls} title={title}>
        {label}
      </Link>
    );
  }
  return (
    <>
      {chip}
      {missing === "woo_unavailable" && reason ? (
        <span className="sat-chip-reason">{reason}</span>
      ) : null}
    </>
  );
}

/** Card de «📦 Por embalar» y «En preparación» (Lote 2 · PR-2, revisión de
 *  diseño §8): se usa de pie y a veces con guantes. Orden de lectura: nº y
 *  estado → cliente → observaciones del comercial (ámbar, solo si hay) → datos
 *  técnicos grandes con «copiar» → líneas → acciones de 48 px.
 *
 *  «▶ Empezar preparación» / «📦 Embalar» abren el MODAL de preparar sobre la
 *  propia cola (bultos + «Embalar»): no se cambia de pantalla. Debajo,
 *  separados, el albarán (lo necesita para cotejar líneas antes de embalar) y
 *  la ficha. La card no es un enlace entero: con botones dentro, era la forma
 *  de pulsar el equivocado. */
export function SatPreparingCard({
  order,
  onChanged,
  canEdit = false,
  onPrepare,
}: {
  order: SatQueueItem;
  onChanged: () => void;
  /** Lote 3: habilita la edición inline de los datos técnicos (admin/pedidos). */
  canEdit?: boolean;
  /** Abre el modal de preparar / embalar (`start` = empezar la preparación). */
  onPrepare?: (order: SatQueueItem, start: boolean) => void;
}) {
  const albaran = useSatAlbaranAction(order, onChanged);
  // Lote 3: actualización optimista de los campos de seguimiento tras editar.
  const [seg, setSeg] = useState<SeguimientoFieldsPatch | null>(null);
  const prep = order.preparation_status;
  const puede = canEdit && !!onPrepare;

  const serial = seg ? seg.serial_number : order.serial_number;
  const license = seg ? seg.whiterip_license : order.whiterip_license;
  const edit: SatTechEdit | undefined = canEdit
    ? { orderId: order.id, onSaved: setSeg }
    : undefined;

  return (
    <div className="sat-card-wrap">
      <article className="sat-card sat-preparing-card" aria-label={`Pedido ${order.order_number}`}>
        <div className="sat-card-top">
          <span className="sat-card-num">{order.order_number}</span>
          {/* Lote 5 · #4 — fecha del pedido bien visible en la cabecera. */}
          <span className="sat-card-date mono">{satShortDate(order.placed_at)}</span>
        </div>
        {/* Lote 5 · #4 — importe (se mantiene) + estado. */}
        <div className="sat-card-meta">
          <span className="sat-card-amount mono">
            {order.total_amount.toFixed(2)} {order.currency}
          </span>
          <span className={`badge ${STATUS_LABELS[order.preparation_status]?.tone ?? "muted"}`}>
            {STATUS_LABELS[order.preparation_status]?.label ?? order.preparation_status}
          </span>
        </div>
        {customerLabel(order) ? (
          <div className="sat-card-customer">{customerLabel(order)}</div>
        ) : null}
        {order.payment_status !== "paid" ? (
          <div className="sat-card-warn">⚠ SIN COBRAR</div>
        ) : null}
        <SatObservaciones notes={order.notes} />
        <SatTechData serial={serial} license={license} edit={edit} />
        <ul className="sat-card-lines">
          {order.lines.map((l, i) => (
            <li key={i}>{l.quantity}× {l.description}</li>
          ))}
        </ul>
        <div className="sat-card-actions">
          {puede && (prep === "in_queue" || prep === "preparing") ? (
            <div className="sat-card-actions-primary">
              <button type="button" className="button lg sat-card-primary"
                      onClick={() => onPrepare?.(order, prep === "in_queue")}>
                {prep === "in_queue" ? "▶ Empezar preparación" : "📦 Embalar"}
              </button>
            </div>
          ) : null}
          <div className="sat-card-actions-secondary">
            <SatAlbaranChip order={order} albaran={albaran} explainInPlace size="lg" />
            <Link href={`/erp/orders/${order.id}`} className="button secondary lg">
              Ficha
            </Link>
          </div>
        </div>
      </article>
      {albaran.error ? (
        <p className="form-error small" role="status">
          {albaran.error}{" "}
          <Link href={`/erp/orders/${order.id}`}>Ir a la ficha</Link>
        </p>
      ) : null}
    </div>
  );
}
