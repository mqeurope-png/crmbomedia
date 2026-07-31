"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { DOMAIN_LABEL, ORDERS, STATUS_META, type Domain } from "../../mocks";

const DOMAINS: Domain[] = ["payment", "preparation", "shipping", "invoicing"];

/** Pantalla 2 — Ficha de pedido: 4 estados independientes + historial. */
export default function OrderDetailPage() {
  const params = useParams<{ id: string }>();
  const order = ORDERS.find((o) => o.id === params.id);
  if (!order) {
    return <main><p>Pedido no encontrado. <Link href="/erp-prototype/orders">Volver</Link></p></main>;
  }
  const domainStatus: Record<Domain, string> = {
    payment: order.payment, preparation: order.preparation,
    shipping: order.shipping, invoicing: order.invoicing,
  };
  return (
    <main>
      <h1 className="erp-h1">Pedido {order.number}</h1>
      <p className="erp-sub">{order.store} · {order.customer}{order.company ? ` · ${order.company}` : ""} · {order.total}</p>

      <div className="erp-states">
        {DOMAINS.map((d) => {
          const st = domainStatus[d];
          const meta = STATUS_META[st] ?? { label: st, tone: "muted" as const };
          return (
            <div className="erp-state-box" key={d}>
              <div className="dom">{DOMAIN_LABEL[d]}</div>
              <span className={`erp-chip ${meta.tone}`}>{meta.label}</span>
            </div>
          );
        })}
      </div>

      <div className="erp-grid2">
        <div>
          <div className="erp-card">
            <h3>Líneas</h3>
            <table className="erp-table">
              <thead><tr><th>SKU</th><th>Artículo</th><th>Cant.</th><th>Total</th><th>Mapping</th></tr></thead>
              <tbody>
                {order.lines.map((l) => (
                  <tr key={l.sku}>
                    <td><code>{l.sku}</code></td><td>{l.name}</td><td>{l.qty}</td><td>{l.total}</td>
                    <td>{l.mapped
                      ? <span className="erp-chip ok">CODART ✓</span>
                      : <span className="erp-chip bad">sin mapear</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="erp-card">
            <h3>Historial (order_state_history)</h3>
            <ul className="erp-hist">
              {order.history.map((h, i) => (
                <li key={i}>
                  <span className="when">{h.at}</span>
                  <span><strong>{DOMAIN_LABEL[h.domain]}</strong>: {h.from} → {h.to} · {h.actor} ({h.via})
                    {h.evidence ? <span className="ev"> — {h.evidence}</span> : null}</span>
                </li>
              ))}
            </ul>
          </div>
        </div>
        <div>
          <div className="erp-card">
            <h3>Acciones (según rol)</h3>
            <div className="erp-actions">
              <button className="erp-btn primary" type="button">Crear envío</button>
              <button className="erp-btn" type="button">Solicitar factura</button>
              <button className="erp-btn danger" type="button">Reembolsar (admin)</button>
            </div>
            <p className="erp-sub" style={{ marginTop: 10 }}>
              Los botones se habilitan según la matriz de permisos + guards
              (p.ej. «Crear envío» exige preparación = Embalado).
            </p>
          </div>
        </div>
      </div>
    </main>
  );
}
