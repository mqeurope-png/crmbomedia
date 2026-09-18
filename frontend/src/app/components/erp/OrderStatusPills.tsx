/** Lote B7 → Lote 2 (E4) — el estado del pedido de un vistazo, como lo pinta
 *  la bandeja (tarjetas y vista lista): la rejilla fija de cuatro casillas
 *  Pago · Factura · Cobro · Envío (`OrderStatusGrid`) más la pastilla
 *  «Completado», que es la marca manual de BoHub (no un paso del proceso) y
 *  por eso va aparte, como pastilla y no como casilla.
 *
 *  Mantiene el nombre exportado y el contrato de #428: `isInvoiced`, el prop
 *  `size` («sm» en la celda de la tabla) y los `aria-label` «Pagado: sí» /
 *  «Facturado: no» / «Cobro registrado: sí» / «Completado: no» con las clases
 *  `is-on` / `is-off`, que la bandeja y sus tests siguen usando. */

import { isInvoiced, OrderStatusGrid, type OrderStatusGridInput } from "./OrderStatusGrid";

export { isInvoiced };

export type OrderStatusPillsInput = OrderStatusGridInput & {
  completed?: boolean;
  completed_at?: string | null;
  completed_by_name?: string | null;
  /** Fecha (ISO) del último envío de la factura por email, o null si nunca. */
  invoice_emailed_at?: string | null;
};

function fecha(iso: string | null | undefined): string {
  if (!iso) return "";
  const [y, m, day] = iso.slice(0, 10).split("-");
  return `${Number(day)}/${Number(m)}/${y}`;
}

/** Detalle de la pastilla «Completado» (solo BoHub). */
export function completadoTitle(o: OrderStatusPillsInput): string {
  if (!o.completed) return "Sin marcar como completado.";
  return `Completado${o.completed_at ? ` el ${fecha(o.completed_at)}` : ""}${o.completed_by_name ? ` por ${o.completed_by_name}` : ""} (solo BoHub)`;
}

/** Detalle de la pastilla «Factura enviada» (envío por email al cliente). */
export function facturaEnviadaTitle(o: OrderStatusPillsInput): string {
  if (o.invoice_emailed_at) {
    return `Factura enviada al cliente el ${fecha(o.invoice_emailed_at)}.`;
  }
  return "Factura emitida pero sin enviar al cliente por email.";
}

export function OrderStatusPills({
  order, size = "md", className,
}: {
  order: OrderStatusPillsInput;
  /** `md` en tarjetas; `sm` en la vista lista (cabe en una celda). */
  size?: "sm" | "md";
  className?: string;
}) {
  const completado = !!order.completed;
  // «Factura enviada» al cliente por email: solo tiene sentido si hay factura
  // (verde = enviada, ámbar = emitida pero sin enviar); sin factura, nada.
  const facturada = isInvoiced(order);
  const facturaEnviada = !!order.invoice_emailed_at;
  return (
    <div className={`erp-status-pills${size === "sm" ? " is-sm" : ""}${className ? ` ${className}` : ""}`}>
      <OrderStatusGrid order={order} size={size} />
      {facturada ? (
        <span
          className={`erp-status-pill${facturaEnviada ? " is-on" : " is-warn"}`}
          data-pill="factura_enviada"
          role="img"
          aria-label={`Factura enviada: ${facturaEnviada ? "sí" : "no"}`}
          title={facturaEnviadaTitle(order)}
        >
          <span className="erp-status-pill-dot" aria-hidden />
          {facturaEnviada ? "Factura enviada" : "Factura sin enviar"}
        </span>
      ) : null}
      <span
        className={`erp-status-pill${completado ? " is-on" : " is-off"}`}
        data-pill="completado"
        role="img"
        aria-label={`Completado: ${completado ? "sí" : "no"}`}
        title={completadoTitle(order)}
      >
        <span className="erp-status-pill-dot" aria-hidden />
        Completado
      </span>
    </div>
  );
}
