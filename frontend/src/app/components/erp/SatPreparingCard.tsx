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
} from "../../lib/erpApi";

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
 *  `inLink`: la card de «Por embalar» es entera un <Link>, así que el chip es
 *  un span con rol botón (un <a> anidado es HTML inválido) y los estados sin
 *  albarán tampoco navegan: al pulsar explican qué hacer. Fuera de un link,
 *  «Falta albarán» / «no disponible» enlazan a la ficha, como siempre. Con un
 *  pedido web sin descarga posible se enseña además el motivo. */
export function SatAlbaranChip({
  order,
  albaran,
  inLink = false,
}: {
  order: SatQueueItem;
  albaran: ReturnType<typeof useSatAlbaranAction>;
  inLink?: boolean;
}) {
  const { busy, albaranClick, hasAlbaran, missing, reason, label, title, tone } = albaran;
  const cls = `sat-chip-btn ${tone}`;
  let chip: React.ReactNode;
  if (inLink) {
    chip = (
      <span
        role="button"
        tabIndex={0}
        className={cls}
        aria-disabled={busy}
        title={title}
        onClick={albaranClick}
      >
        {label}
      </span>
    );
  } else if (hasAlbaran) {
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

/** Card de «📦 Por embalar» (D-1-fix2): la card entera enlaza al modo trabajo,
 *  con un chip de albarán que NO navega (descarga/abre el PDF sin salir del
 *  táctil — el operativo lo necesita para cotejar líneas antes de embalar). */
export function SatPreparingCard({
  order,
  onChanged,
}: {
  order: SatQueueItem;
  onChanged: () => void;
}) {
  const albaran = useSatAlbaranAction(order, onChanged);

  return (
    <div className="sat-card-wrap">
      <Link href={`/erp/sat/${order.id}`} className="sat-card">
        <div className="sat-card-top">
          <span className="sat-card-num">{order.order_number}</span>
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
        <ul className="sat-card-lines">
          {order.lines.map((l, i) => (
            <li key={i}>{l.quantity}× {l.description}</li>
          ))}
        </ul>
        <div className="sat-card-docs">
          <SatAlbaranChip order={order} albaran={albaran} inLink />
        </div>
        <span className="sat-card-cta">Abrir →</span>
      </Link>
      {albaran.error ? (
        <p className="form-error small" role="status">
          {albaran.error}{" "}
          <Link href={`/erp/orders/${order.id}`}>Ir a la ficha</Link>
        </p>
      ) : null}
    </div>
  );
}
