"use client";

import Link from "next/link";
import { useState } from "react";
import {
  fetchAlbaranFromWoo,
  listShippingFiles,
  openShippingFile,
  STATUS_LABELS,
  type SatQueueItem,
} from "../../lib/erpApi";

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
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function albaranClick(e: React.SyntheticEvent) {
    e.preventDefault();
    e.stopPropagation();
    setError(null);
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
      setError("No se pudo descargar automáticamente. Sube el albarán a mano desde la ficha.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="sat-card-wrap">
      <Link href={`/erp/sat/${order.id}`} className="sat-card">
        <div className="sat-card-top">
          <span className="sat-card-num">{order.order_number}</span>
          <span className={`badge ${STATUS_LABELS[order.preparation_status]?.tone ?? "muted"}`}>
            {STATUS_LABELS[order.preparation_status]?.label ?? order.preparation_status}
          </span>
        </div>
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
            className={`sat-chip-btn ${order.has_albaran ? "ok" : "info"}`}
            aria-disabled={busy}
            onClick={albaranClick}
          >
            {order.has_albaran
              ? "📄 Imprimir albarán"
              : busy ? "Descargando…" : "📄 Descargar albarán"}
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
