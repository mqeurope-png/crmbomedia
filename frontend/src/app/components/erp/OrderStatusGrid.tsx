/** Lote 2 · E4 — rejilla de estado fija del pedido: cuatro casillas
 *  **Pago · Factura · Cobro · Envío**, siempre en el mismo orden y en la
 *  misma posición, con el mismo color: verde = hecho, ámbar = pendiente,
 *  gris = no aplica (todavía), rojo = bloqueado. Cada casilla lleva su
 *  palabra y la palabra del estado, así que se lee sin distinguir colores.
 *  Las casillas INFORMAN, nunca se pulsan (lo pulsable es un botón o un
 *  chip de filtro y se distingue por el borde).
 *
 *  Criterio (mismo que el backend / la bandeja):
 *  - Pago: `paid` → hecho; `pending` / `partial_paid` / `credit_approved`
 *    → pendiente; `failed` / `refunded` → bloqueado.
 *  - Factura: emitida (nº FACTUSOL o estado emitido) → hecho;
 *    `invoice_status === "error"` → bloqueado; el resto → pendiente.
 *  - Cobro: `factusol_cobro_status === "cobrada"` → hecho; facturada y no
 *    cobrada → pendiente; sin factura → no aplica.
 *  - Envío: `in_transit` / `delivered` / `already_shipped_externally` →
 *    hecho; `incident` / `returned` → bloqueado; el resto → pendiente.
 *
 *  Compatibilidad: el valor de cada casilla conserva el `aria-label` del
 *  contrato anterior («Pagado: sí» / «Facturado: no» / «Cobro registrado:
 *  sí»), que siguen usando la bandeja (#428) y sus tests. Para el lector de
 *  pantalla manda el de la casilla («Pago: hecho», `role="img"`). */

export type StatusCellState = "done" | "pending" | "na" | "blocked";
export type StatusCellKey = "pago" | "factura" | "cobro" | "envio";

export type OrderStatusGridInput = {
  payment_status: string;
  invoice_status: string;
  factusol_invoice_number: string | null;
  factusol_cobro_status?: string | null;
  transport_status?: string | null;
  /** «No requiere envío»: el envío no aplica (gris), no cuenta como pendiente. */
  shipping_not_required?: boolean;
  /** `"sample"` = muestra / envío NO facturable: Factura y Cobro no aplican. */
  order_kind?: string | null;
};

/** ¿Es una muestra / envío no facturable? (no lleva factura ni cobro). */
export function isSampleOrder(o: { order_kind?: string | null }): boolean {
  return (o.order_kind || "") === "sample";
}

export type StatusCell = {
  key: StatusCellKey;
  /** Palabra de la casilla («Pago»). */
  label: string;
  state: StatusCellState;
  /** Palabra del estado que se ve («Pagado», «Pendiente», «—»). */
  value: string;
  /** Detalle para el tooltip. */
  title: string;
  /** `aria-label` del contrato anterior («Pagado: sí»). */
  legacyLabel: string;
};

/** Palabra del estado para el `aria-label` de la casilla. */
export const STATE_WORD: Record<StatusCellState, string> = {
  done: "hecho",
  pending: "pendiente",
  na: "no aplica",
  blocked: "bloqueado",
};

const INVOICED_STATUSES = new Set(["generated", "invoiced_by_erp", "already_invoiced_externally"]);

/** Mismo criterio de «facturado» que el backend y que la propia bandeja. */
export function isInvoiced(o: { invoice_status: string; factusol_invoice_number: string | null }): boolean {
  return INVOICED_STATUSES.has(o.invoice_status) || !!o.factusol_invoice_number;
}

function pago(o: OrderStatusGridInput): StatusCell {
  const base = { key: "pago" as const, label: "Pago" };
  const st = o.payment_status;
  const legacyLabel = `Pagado: ${st === "paid" ? "sí" : "no"}`;
  if (st === "paid") {
    return { ...base, state: "done", value: "Pagado", title: "El cliente ha pagado (estado del CRM).", legacyLabel };
  }
  if (st === "failed") {
    return { ...base, state: "blocked", value: "Fallido", title: "El pago ha fallado.", legacyLabel };
  }
  if (st === "refunded") {
    return { ...base, state: "blocked", value: "Devuelto", title: "El pago se ha devuelto al cliente.", legacyLabel };
  }
  if (st === "partial_paid") {
    return { ...base, state: "pending", value: "Parcial", title: "Pago parcial registrado en el CRM.", legacyLabel };
  }
  if (st === "credit_approved") {
    return { ...base, state: "pending", value: "A crédito", title: "Crédito aprobado: el pago queda pendiente.", legacyLabel };
  }
  return { ...base, state: "pending", value: "Pendiente", title: "Pago no confirmado en el CRM.", legacyLabel };
}

function factura(o: OrderStatusGridInput): StatusCell {
  const base = { key: "factura" as const, label: "Factura" };
  const facturado = isInvoiced(o);
  const legacyLabel = `Facturado: ${facturado ? "sí" : "no"}`;
  if (isSampleOrder(o)) {
    return {
      ...base, state: "na", value: "No aplica",
      title: "Muestra / envío no facturable: no lleva factura.", legacyLabel,
    };
  }
  if (facturado) {
    return {
      ...base, state: "done", value: "Emitida",
      title: `Factura emitida${o.factusol_invoice_number ? ` (${o.factusol_invoice_number})` : ""}.`,
      legacyLabel,
    };
  }
  if (o.invoice_status === "error") {
    return { ...base, state: "blocked", value: "Error", title: "La emisión de la factura ha dado error en FACTUSOL.", legacyLabel };
  }
  if (o.invoice_status === "credit_note") {
    return { ...base, state: "pending", value: "Abonada", title: "Factura abonada (nota de crédito).", legacyLabel };
  }
  return { ...base, state: "pending", value: "Por emitir", title: "Sin factura.", legacyLabel };
}

function cobro(o: OrderStatusGridInput): StatusCell {
  const base = { key: "cobro" as const, label: "Cobro" };
  const cobrado = o.factusol_cobro_status === "cobrada";
  const legacyLabel = `Cobro registrado: ${cobrado ? "sí" : "no"}`;
  if (isSampleOrder(o)) {
    return {
      ...base, state: "na", value: "No aplica",
      title: "Muestra / envío no facturable: no hay cobro que registrar.",
      legacyLabel,
    };
  }
  if (cobrado) {
    return { ...base, state: "done", value: "Cobrado", title: "El cobro de la factura consta registrado en FACTUSOL.", legacyLabel };
  }
  if (isInvoiced(o)) {
    return {
      ...base, state: "pending", value: "Pendiente",
      title: "La factura no consta cobrada en FACTUSOL (o aún no se ha comprobado).",
      legacyLabel,
    };
  }
  return { ...base, state: "na", value: "—", title: "Sin factura: no hay cobro que registrar.", legacyLabel };
}

function envio(o: OrderStatusGridInput): StatusCell {
  const base = { key: "envio" as const, label: "Envío" };
  const st = o.transport_status ?? "not_shipped";
  const enviado = st === "in_transit" || st === "delivered" || st === "already_shipped_externally";
  const legacyLabel = `Enviado: ${enviado ? "sí" : "no"}`;
  // «No requiere envío»: no aplica (gris), no cuenta como pendiente — salvo que
  // ya se hubiera enviado (raro), donde manda el estado real del transporte.
  if (o.shipping_not_required && !enviado && st !== "incident" && st !== "returned") {
    return { ...base, state: "na", value: "—", title: "Este pedido no requiere envío.",
             legacyLabel: "Enviado: no aplica" };
  }
  if (st === "delivered") {
    return { ...base, state: "done", value: "Entregado", title: "El transportista ha entregado el pedido.", legacyLabel };
  }
  if (enviado) {
    return { ...base, state: "done", value: "Enviado", title: "El pedido ha salido.", legacyLabel };
  }
  if (st === "incident") {
    return { ...base, state: "blocked", value: "Incidencia", title: "El envío tiene una incidencia.", legacyLabel };
  }
  if (st === "returned") {
    return { ...base, state: "blocked", value: "Devuelto", title: "El envío se ha devuelto.", legacyLabel };
  }
  if (st === "label_created") {
    return { ...base, state: "pending", value: "Etiqueta lista", title: "Etiqueta creada; pendiente de recogida.", legacyLabel };
  }
  return { ...base, state: "pending", value: "Pendiente", title: "Pendiente de envío.", legacyLabel };
}

/** Las cuatro casillas, siempre en este orden. */
export function orderStatusCells(o: OrderStatusGridInput): StatusCell[] {
  return [pago(o), factura(o), cobro(o), envio(o)];
}

export function OrderStatusGrid({
  order, size = "md", className, label = "Estado del pedido",
}: {
  order: OrderStatusGridInput;
  /** `md` en tarjetas y ficha; `sm` en la celda de la vista lista (2×2). */
  size?: "sm" | "md";
  className?: string;
  /** Nombre accesible del grupo. */
  label?: string;
}) {
  return (
    <div
      className={`erp-status-grid${size === "sm" ? " is-sm" : ""}${className ? ` ${className}` : ""}`}
      role="group"
      aria-label={label}
    >
      {orderStatusCells(order).map((c) => (
        <span
          key={c.key}
          className={`erp-status-cell is-${c.state}`}
          data-cell={c.key}
          data-state={c.state}
          role="img"
          aria-label={`${c.label}: ${STATE_WORD[c.state]}`}
          title={c.title}
        >
          <span className="erp-status-cell-k" aria-hidden="true">{c.label}</span>
          <span
            className={`erp-status-cell-v${c.state === "done" ? " is-on" : " is-off"}`}
            aria-label={c.legacyLabel}
          >
            {c.value}
          </span>
        </span>
      ))}
    </div>
  );
}
