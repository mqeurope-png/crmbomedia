"use client";

import Link from "next/link";
import { useState } from "react";
import { DOMAIN_LABEL, ORDERS, STATUS_META, type Domain } from "../mocks";

const DOMAINS: Domain[] = ["payment", "preparation", "shipping", "invoicing"];

function Chip({ status }: { status: string }) {
  const meta = STATUS_META[status] ?? { label: status, tone: "muted" as const };
  return <span className={`erp-chip ${meta.tone}`}>{meta.label}</span>;
}

/** Pantalla 1 — Bandeja principal de pedidos (admin). */
export default function OrdersListPage() {
  const [store, setStore] = useState("all");
  const rows = ORDERS.filter((o) => store === "all" || o.store === store);
  return (
    <main>
      <h1 className="erp-h1">Pedidos</h1>
      <p className="erp-sub">Bandeja principal — los 4 dominios de estado por pedido, filtrable.</p>
      <p>
        <select value={store} onChange={(e) => setStore(e.target.value)} aria-label="Filtrar tienda">
          <option value="all">Todas las tiendas</option>
          <option value="mbolasers.com">mbolasers.com</option>
          <option value="artisjet-europe.com">artisjet-europe.com</option>
          <option value="fluxlasers.es">fluxlasers.es</option>
        </select>
      </p>
      <table className="erp-table">
        <thead>
          <tr>
            <th>Pedido</th><th>Cliente</th><th>Total</th>
            {DOMAINS.map((d) => <th key={d}>{DOMAIN_LABEL[d]}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map((o) => (
            <tr key={o.id}>
              <td>
                <Link href={`/erp-prototype/orders/${o.id}`}><strong>{o.number}</strong></Link>
                <div className="erp-domchip">{o.store} · {o.placedAt}</div>
              </td>
              <td>{o.customer}{o.company ? <div className="erp-domchip">{o.company}</div> : null}</td>
              <td>{o.total}</td>
              <td><Chip status={o.payment} /></td>
              <td><Chip status={o.preparation} /></td>
              <td><Chip status={o.shipping} /></td>
              <td><Chip status={o.invoicing} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </main>
  );
}
