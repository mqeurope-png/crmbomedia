"use client";

import {
  DOMAIN_LABELS,
  type AvailableTransition,
  type OrderDetail,
  type StatusDomain,
} from "../../lib/erpApi";
import { OrderStatusBadge } from "./OrderStatusBadge";

const DOMAINS: StatusDomain[] = ["payment", "preparation", "transport", "invoice"];

/** Los 4 dominios con su estado actual + los botones de transición que el
 *  rol actual puede disparar (derivados de `available_transitions`, no
 *  duplicamos la matriz). onFire recibe la transición elegida. */
export function OrderStatusMachine({
  order,
  onFire,
  busy,
}: {
  order: OrderDetail;
  onFire: (domain: StatusDomain, t: AvailableTransition) => void;
  busy?: boolean;
}) {
  const statusOf: Record<StatusDomain, string> = {
    payment: order.payment_status,
    preparation: order.preparation_status,
    transport: order.transport_status,
    invoice: order.invoice_status,
  };
  return (
    <div className="erp-states">
      {DOMAINS.map((d) => (
        <div className="erp-state-box" key={d}>
          <div className="erp-state-dom">{DOMAIN_LABELS[d]}</div>
          <OrderStatusBadge status={statusOf[d]} />
          <div className="erp-state-actions">
            {order.available_transitions[d]?.map((t) => (
              <button
                key={t.to_status}
                type="button"
                className="button small secondary"
                disabled={busy}
                onClick={() => onFire(d, t)}
              >
                {t.label}
              </button>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
