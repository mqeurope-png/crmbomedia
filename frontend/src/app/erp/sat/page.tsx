"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { SatReadyCard } from "../../components/erp/SatReadyCard";
import { extractErrorMessage } from "../../lib/errors";
import { getSatQueue, STATUS_LABELS, type SatQueue } from "../../lib/erpApi";

/** Cola SAT táctil (D-1-fix1): 2 secciones — «Por embalar» (con acceso al modo
 *  trabajo) y «Listos para envío» (imprimir albarán/etiqueta + marcar recogido).
 *  En móvil/tablet las secciones se apilan; en escritorio pueden ir en columnas. */
export default function SatQueuePage() {
  const [queue, setQueue] = useState<SatQueue>({ preparing: [], ready_for_pickup: [] });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    getSatQueue()
      .then(setQueue)
      .catch((e) => setError(extractErrorMessage(e, "No se pudo cargar la cola.")))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load]);

  const { preparing, ready_for_pickup: ready } = queue;

  return (
    <div className="sat-queue-wrap">
      <div className="sat-queue-head">
        <h1>Cola SAT</h1>
        <span className="muted small">
          Por embalar: {preparing.length} · Listos: {ready.length}
        </span>
        <button type="button" className="button secondary small" onClick={load}>
          Actualizar
        </button>
      </div>
      {error ? <p className="form-error">{error}</p> : null}
      {loading ? <p className="muted">Cargando…</p> : null}

      <div className="sat-sections">
        <section className="sat-section" aria-label="Por embalar">
          <h2>📦 Por embalar</h2>
          {preparing.length === 0 ? (
            <p className="sat-empty">Nada por embalar.</p>
          ) : (
            <div className="sat-cards">
              {preparing.map((o) => (
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
                  <span className="sat-card-cta">Abrir →</span>
                </Link>
              ))}
            </div>
          )}
        </section>

        <section className="sat-section" aria-label="Listos para envío">
          <h2>🚚 Listos para envío</h2>
          {ready.length === 0 ? (
            <p className="sat-empty">Nada listo para enviar.</p>
          ) : (
            <div className="sat-cards">
              {ready.map((o) => (
                <SatReadyCard key={o.id} order={o} onChanged={load} />
              ))}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
