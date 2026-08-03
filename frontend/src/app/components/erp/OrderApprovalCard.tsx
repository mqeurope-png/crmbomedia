"use client";

import Link from "next/link";
import type { PendingOrder } from "../../lib/erpApi";
import { OrderStatusBadge } from "./OrderStatusBadge";

/** Card de la Cola PEDIDOS: muestra el pedido + sus bloqueos + avisos. El
 *  botón «Aprobar» se OCULTA si hay bloqueos activos (no se puede aprobar
 *  hasta resolverlos) o si el usuario no puede aprobar. Los avisos (B-2-fix4)
 *  son informativos y NO ocultan «Aprobar».
 *
 *  Si se pasan `onToggleSelect`/`onMarkExternal`, la card muestra el checkbox
 *  de selección múltiple y el botón «Procesado externamente» (B-2-fix4). */
export function OrderApprovalCard({
  order,
  canApprove,
  onApprove,
  busy,
  selected,
  onToggleSelect,
  onMarkExternal,
}: {
  order: PendingOrder;
  canApprove: boolean;
  onApprove: (id: string) => void;
  busy?: boolean;
  selected?: boolean;
  onToggleSelect?: (id: string) => void;
  onMarkExternal?: (id: string) => void;
}) {
  const blocked = order.blockers.length > 0;
  const warnings = order.warnings ?? [];
  return (
    <div
      className={`erp-approval-card${blocked ? " is-blocked" : ""}${
        selected ? " is-selected" : ""
      }`}
    >
      <header className="erp-approval-head">
        {onToggleSelect ? (
          <input
            type="checkbox"
            checked={!!selected}
            aria-label={`Seleccionar ${order.order_number}`}
            onChange={() => onToggleSelect(order.id)}
          />
        ) : null}
        <Link href={`/erp/orders/${order.id}`} className="erp-approval-num">
          {order.order_number}
        </Link>
        <span className="muted small">
          {order.total_amount.toFixed(2)} {order.currency}
        </span>
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
      {warnings.length > 0 ? (
        <ul className="erp-warnings" aria-label={`Avisos ${order.order_number}`}>
          {warnings.map((w) => (
            <li key={w.code} className="erp-warning">
              <span className="badge warn">{w.code}</span> {w.detail}
            </li>
          ))}
        </ul>
      ) : null}
      <div className="erp-approval-actions">
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
        {onMarkExternal ? (
          <button
            type="button"
            className="button small ghost"
            disabled={busy}
            onClick={() => onMarkExternal(order.id)}
          >
            Procesado externamente
          </button>
        ) : null}
      </div>
    </div>
  );
}
