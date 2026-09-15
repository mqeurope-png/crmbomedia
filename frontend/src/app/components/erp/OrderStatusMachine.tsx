"use client";

import {
  DOMAIN_LABELS,
  type AvailableTransition,
  type OrderDetail,
  type StatusDomain,
} from "../../lib/erpApi";
import { OrderStatusBadge } from "./OrderStatusBadge";

const DOMAINS: StatusDomain[] = ["payment", "preparation", "transport", "invoice"];

/** «Otras acciones de estado» (rediseño de flujo, Fase 1 remate): la fila
 *  compacta bajo «Siguiente paso» con, por dominio, su estado actual y los
 *  botones de transición que el rol puede disparar (derivados de
 *  `available_transitions`, no duplicamos la matriz). Sustituye a las cuatro
 *  tarjetas PAGO / PREPARACIÓN / TRANSPORTE / FACTURACIÓN: el ciclo ya lo
 *  cuenta el stepper, aquí solo quedan el sub-estado y las transiciones
 *  (Reembolso, Empezar preparación, Bloquear, Crear envío, Solicitar
 *  factura…) para no perder ninguna. onFire recibe la transición elegida. */
export function OrderStatusMachine({
  order,
  onFire,
  busy,
  omit = null,
  hide = [],
}: {
  order: OrderDetail;
  onFire: (domain: StatusDomain, t: AvailableTransition) => void;
  busy?: boolean;
  /** Transición que ya está como botón principal en «Siguiente paso»: aquí
   *  no se repite. */
  omit?: { domain: StatusDomain; to_status: string } | null;
  /** Transiciones que la ficha NO ofrece como botón (p. ej. «Solicitar
   *  factura», que era un alias de «Emitir factura FACTUSOL»). El arco sigue
   *  existiendo en el backend; solo no se pinta. */
  hide?: { domain: StatusDomain; to_status: string }[];
}) {
  const statusOf: Record<StatusDomain, string> = {
    payment: order.payment_status,
    preparation: order.preparation_status,
    transport: order.transport_status,
    invoice: order.invoice_status,
  };
  return (
    <section className="erp-flow-states" aria-labelledby="erp-flow-states-title">
      <span className="erp-flow-states-title" id="erp-flow-states-title">
        Otras acciones de estado
      </span>
      {DOMAINS.map((d) => (
        <div className="erp-flow-state" key={d}>
          <span className="erp-flow-state-dom">{DOMAIN_LABELS[d]}</span>
          <OrderStatusBadge status={statusOf[d]} />
          {order.available_transitions[d]?.filter(
            (t) => !(omit && omit.domain === d && omit.to_status === t.to_status)
              && !hide.some((h) => h.domain === d && h.to_status === t.to_status),
          ).map((t) => (
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
      ))}
    </section>
  );
}
