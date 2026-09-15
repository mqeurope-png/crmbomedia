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
  type SatQueueItem,
} from "../../lib/erpApi";

/** Acción «albarán» de un pedido por embalar, compartida por la card y por la
 *  fila de la vista lista (Lote B6): el albarán que BoHub creó en FACTUSOL
 *  manda sobre el fichero subido a mano (es el documento real del pedido, el
 *  mismo PDF que la ficha y que el email al SAT); el fichero subido queda para
 *  los pedidos del flujo antiguo; y sin ninguno de los dos, se descarga de Woo. */
export function useSatAlbaranAction(order: SatQueueItem, onChanged: () => void) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const factusolAlbaran = order.factusol_albaran_number ?? null;
  const hasAlbaran = Boolean(factusolAlbaran || order.has_albaran);

  async function albaranClick(e: React.SyntheticEvent) {
    e.preventDefault();
    e.stopPropagation();
    setError(null);
    if (factusolAlbaran) {
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
    if (order.has_albaran) {
      try {
        const files = await listShippingFiles(order.id, "albaran");
        if (files[0]) await openShippingFile(files[0]);
      } catch {
        setError("No se pudo abrir el albarán. Revisa la ficha del pedido.");
      }
      return;
    }
    // Sin albarán: descarga automática (mu-plugin → reportlab) y auto-abre.
    setBusy(true);
    try {
      const r = await fetchAlbaranFromWoo(order.id);
      onChanged();
      await openShippingFile(r.file);
    } catch {
      setError(
        "No se pudo descargar automáticamente. Crea el albarán en FACTUSOL o "
        + "súbelo a mano desde la ficha.",
      );
    } finally {
      setBusy(false);
    }
  }

  const label = busy
    ? "Descargando…"
    : hasAlbaran
      ? "📄 Imprimir albarán"
      : "📄 Descargar albarán";
  const title = factusolAlbaran
    ? `Descarga el albarán ${factusolAlbaran} de FACTUSOL en PDF`
    : undefined;

  return { busy, error, albaranClick, hasAlbaran, factusolAlbaran, label, title };
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
  const { busy, error, albaranClick, hasAlbaran, label, title } =
    useSatAlbaranAction(order, onChanged);

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
          <span
            role="button"
            tabIndex={0}
            className={`sat-chip-btn ${hasAlbaran ? "ok" : "info"}`}
            aria-disabled={busy}
            title={title}
            onClick={albaranClick}
          >
            {label}
          </span>
        </div>
        <span className="sat-card-cta">Abrir →</span>
      </Link>
      {error ? (
        <p className="form-error small" role="status">
          {error}{" "}
          <Link href={`/erp/orders/${order.id}`}>Ir a la ficha</Link>
        </p>
      ) : null}
    </div>
  );
}
