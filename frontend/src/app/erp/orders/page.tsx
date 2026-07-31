"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { PageHeader } from "../../components/PageHeader";
import { OrderStatusBadge } from "../../components/erp/OrderStatusBadge";
import { extractErrorMessage } from "../../lib/errors";
import { listOrders, type OrderSummary } from "../../lib/erpApi";

const STORES = [
  { value: "", label: "Todas las tiendas" },
];

export default function ErpOrdersPage() {
  const [rows, setRows] = useState<OrderSummary[]>([]);
  const [prep, setPrep] = useState("");
  const [payment, setPayment] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    listOrders({ preparation: prep || undefined, payment: payment || undefined })
      .then(setRows)
      .catch((e) => setError(extractErrorMessage(e, "No se pudieron cargar los pedidos.")))
      .finally(() => setLoading(false));
  }, [prep, payment]);

  const stores = useMemo(() => STORES, []);

  return (
    <main className="shell shell-wide">
      <PageHeader
        title="Pedidos"
        eyebrow="ERP"
        description="Bandeja principal — los 4 estados de cada pedido."
        crumbs={[{ label: "ERP" }, { label: "Pedidos" }]}
        actions={
          <Link href="/erp/orders/pending-approval" className="button secondary small">
            Cola PEDIDOS
          </Link>
        }
      />
      <div className="wf-list-filters" style={{ marginBottom: 12, display: "flex", gap: 8 }}>
        <select value={prep} onChange={(e) => setPrep(e.target.value)} aria-label="Filtro preparación">
          <option value="">Preparación: todas</option>
          <option value="pending_review">Pend. revisión</option>
          <option value="in_queue">En cola</option>
          <option value="preparing">Preparando</option>
          <option value="packed">Embalado</option>
          <option value="blocked">Bloqueado</option>
        </select>
        <select value={payment} onChange={(e) => setPayment(e.target.value)} aria-label="Filtro pago">
          <option value="">Pago: todos</option>
          <option value="pending">Pendiente</option>
          <option value="paid">Pagado</option>
          <option value="failed">Fallido</option>
          <option value="refunded">Reembolsado</option>
        </select>
        {stores.length > 1 ? <span /> : null}
      </div>
      {error ? <p className="form-error">{error}</p> : null}
      {loading ? (
        <p className="muted">Cargando…</p>
      ) : rows.length === 0 ? (
        <p className="muted">No hay pedidos con este filtro.</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>Pedido</th>
              <th>Total</th>
              <th>Pago</th>
              <th>Preparación</th>
              <th>Transporte</th>
              <th>Facturación</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((o) => (
              <tr key={o.id}>
                <td>
                  <Link href={`/erp/orders/${o.id}`}><strong>{o.order_number}</strong></Link>
                  <div className="muted small">{o.external_source} · {o.placed_at?.slice(0, 10) ?? "—"}</div>
                </td>
                <td>{o.total_amount.toFixed(2)} {o.currency}</td>
                <td><OrderStatusBadge status={o.payment_status} /></td>
                <td><OrderStatusBadge status={o.preparation_status} /></td>
                <td><OrderStatusBadge status={o.transport_status} /></td>
                <td><OrderStatusBadge status={o.invoice_status} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </main>
  );
}
