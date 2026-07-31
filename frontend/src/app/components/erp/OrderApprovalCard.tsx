"use client";

import Link from "next/link";
import type { PendingOrder } from "../../lib/erpApi";
import { OrderStatusBadge } from "./OrderStatusBadge";

/** Card de la Cola PEDIDOS: muestra el pedido + sus bloqueos. El botón
 *  «Aprobar» se OCULTA si hay bloqueos activos (no se puede aprobar hasta
 *  resolverlos) o si el usuario no puede aprobar. */
export function OrderApprovalCard({
  order,
  canApprove,
  onApprove,
  busy,
}: {
  order: PendingOrder;
  canApprove: boolean;
  onApprove: (id: string) => void;
  busy?: boolean;
}) {
  const blocked = order.blockers.length > 0;
  return (
    <div className={`erp-approval-card${blocked ? " is-blocked" : ""}`}>
      <header className="erp-approval-head">
        <Link href={`/erp/orders/${order.id}`} className="erp-approval-num">
          {order.order_number}
        </Link>
        <span className="muted small">{order.total_amount.toFixed(2)} {order.currency}</span>
      </header>
      <div className="erp-approval-badges">
        <OrderStatusBadge status={order.payment_status} />
        <OrderStatusBadge status={order.preparation_status} />
      </div>
      {blocked ? (
        <ul className="erp-blockers" aria-label={`Bloqueos ${order.order_number}`}>
          {order.blockers.map((b) => (
            <li key={b.code} className="erp-blocker">
              <span className="badge bad">{b.code}</span> {b.detail}
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted small">Sin bloqueos — listo para aprobar.</p>
      )}
      {canApprove && !blocked ? (
        <button
          type="button"
          className="button small"
          disabled={busy}
          onClick={() => onApprove(order.id)}
        >
          Aprobar → Cola SAT
        </button>
      ) : null}
    </div>
  );
}
