"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { extractErrorMessage } from "../../lib/errors";
import {
  getSatQueue,
  listShippingFiles,
  openShippingFile,
  STATUS_LABELS,
  type SatQueueItem,
  type ShipmentFileKind,
} from "../../lib/erpApi";

/** Cola SAT táctil: cards grandes con lo esencial + acceso al modo trabajo.
 *  Los botones de avance de estado (Empezar/Embalado) viven en el detalle
 *  ([id]) para forzar registrar peso/dimensiones al embalar. */
export default function SatQueuePage() {
  const [rows, setRows] = useState<SatQueueItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    getSatQueue()
      .then(setRows)
      .catch((e) => setError(extractErrorMessage(e, "No se pudo cargar la cola.")))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load]);

  async function openDoc(
    e: React.MouseEvent, orderId: string, kind: ShipmentFileKind,
  ) {
    // Chip ✓: abre el PDF sin navegar a la ficha (el card es un Link).
    e.preventDefault();
    e.stopPropagation();
    try {
      const files = await listShippingFiles(orderId, kind);
      if (files[0]) await openShippingFile(files[0]);
    } catch {
      // si falla, el operativo puede abrir la ficha y verlo allí
    }
  }

  return (
    <div className="sat-queue-wrap">
      <div className="sat-queue-head">
        <h1>Cola de trabajo</h1>
        <button type="button" className="button secondary small" onClick={load}>Actualizar</button>
      </div>
      {error ? <p className="form-error">{error}</p> : null}
      {loading ? (
        <p className="muted">Cargando…</p>
      ) : rows.length === 0 ? (
        <p className="sat-empty">🎉 No hay pedidos en cola.</p>
      ) : (
        <div className="sat-cards">
          {rows.map((o) => (
            <Link key={o.id} href={`/erp/sat/${o.id}`} className="sat-card">
              <div className="sat-card-top">
                <span className="sat-card-num">{o.order_number}</span>
                <span className={`badge ${STATUS_LABELS[o.preparation_status]?.tone ?? "muted"}`}>
                  {STATUS_LABELS[o.preparation_status]?.label ?? o.preparation_status}
                </span>
              </div>
              {o.payment_status !== "paid" ? (
                <div className="sat-card-warn">⚠ SIN COBRAR</div>
              ) : null}
              <ul className="sat-card-lines">
                {o.lines.map((l, i) => (
                  <li key={i}>{l.quantity}× {l.description}</li>
                ))}
              </ul>
              {(o.preparation_status === "preparing"
                || o.preparation_status === "packed") ? (
                <div className="sat-card-docs">
                  {o.has_albaran ? (
                    <span role="button" tabIndex={0} className="badge ok sat-chip"
                          onClick={(e) => openDoc(e, o.id, "albaran")}>
                      Albarán ✓
                    </span>
                  ) : (
                    <span className="badge muted sat-chip">Albarán ✗</span>
                  )}
                  {o.has_etiqueta ? (
                    <span role="button" tabIndex={0} className="badge ok sat-chip"
                          onClick={(e) => openDoc(e, o.id, "etiqueta")}>
                      Etiqueta ✓
                    </span>
                  ) : (
                    <span className="badge muted sat-chip">Etiqueta ✗</span>
                  )}
                </div>
              ) : null}
              <span className="sat-card-cta">Abrir →</span>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
