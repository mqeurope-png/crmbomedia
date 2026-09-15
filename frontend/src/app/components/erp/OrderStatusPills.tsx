/** Lote B7 — las cuatro pastillas de estado de un pedido, de un vistazo:
 *  Pagado · Facturado · Cobro registrado · Completado. Verde cuando se cumple,
 *  gris cuando no; el `aria-label` («Pagado: sí») lo dice sin depender del
 *  color. Las usan la bandeja (tarjetas y vista lista) con el MISMO criterio
 *  que el backend (`workflow.is_invoiced` / `is_cobrada`):
 *
 *  - Pagado: `payment_status === "paid"` (el «Pagado» del CRM).
 *  - Facturado: hay nº de factura FACTUSOL o el estado de factura es emitido
 *    (generated / invoiced_by_erp / already_invoiced_externally).
 *  - Cobro registrado: `factusol_cobro_status === "cobrada"` (contable, en
 *    FACTUSOL; distinto del «Pagado» del CRM).
 *  - Completado: la marca manual de BoHub (`completed`). */

export type OrderStatusPillsInput = {
  payment_status: string;
  invoice_status: string;
  factusol_invoice_number: string | null;
  factusol_cobro_status?: string | null;
  completed?: boolean;
  completed_at?: string | null;
  completed_by_name?: string | null;
};

const INVOICED_STATUSES = new Set(["generated", "invoiced_by_erp", "already_invoiced_externally"]);

/** Mismo criterio de «facturado» que el backend y que la propia bandeja. */
export function isInvoiced(o: { invoice_status: string; factusol_invoice_number: string | null }): boolean {
  return INVOICED_STATUSES.has(o.invoice_status) || !!o.factusol_invoice_number;
}

function fecha(iso: string | null | undefined): string {
  if (!iso) return "";
  const [y, m, day] = iso.slice(0, 10).split("-");
  return `${Number(day)}/${Number(m)}/${y}`;
}

type Pill = { key: string; label: string; on: boolean; title: string };

export function orderStatusPills(o: OrderStatusPillsInput): Pill[] {
  const pagado = o.payment_status === "paid";
  const facturado = isInvoiced(o);
  const cobrado = o.factusol_cobro_status === "cobrada";
  const completado = !!o.completed;
  return [
    {
      key: "pagado", label: "Pagado", on: pagado,
      title: pagado
        ? "El cliente ha pagado (estado del CRM)."
        : "Pago no confirmado en el CRM.",
    },
    {
      key: "facturado", label: "Facturado", on: facturado,
      title: facturado
        ? `Factura emitida${o.factusol_invoice_number ? ` (${o.factusol_invoice_number})` : ""}.`
        : "Sin factura.",
    },
    {
      key: "cobro", label: "Cobro registrado", on: cobrado,
      title: cobrado
        ? "El cobro de la factura consta registrado en FACTUSOL."
        : facturado
          ? "La factura no consta cobrada en FACTUSOL (o aún no se ha comprobado)."
          : "Sin factura: no hay cobro que registrar.",
    },
    {
      key: "completado", label: "Completado", on: completado,
      title: completado
        ? `Completado${o.completed_at ? ` el ${fecha(o.completed_at)}` : ""}${o.completed_by_name ? ` por ${o.completed_by_name}` : ""} (solo BoHub)`
        : "Sin marcar como completado.",
    },
  ];
}

export function OrderStatusPills({
  order, size = "md", className,
}: {
  order: OrderStatusPillsInput;
  /** `md` en tarjetas; `sm` en la vista lista (caben en una celda). */
  size?: "sm" | "md";
  className?: string;
}) {
  return (
    <span
      className={`erp-status-pills${size === "sm" ? " is-sm" : ""}${className ? ` ${className}` : ""}`}
      role="group"
      aria-label="Estado del pedido"
    >
      {orderStatusPills(order).map((p) => (
        <span
          key={p.key}
          className={`erp-status-pill${p.on ? " is-on" : " is-off"}`}
          data-pill={p.key}
          role="img"
          aria-label={`${p.label}: ${p.on ? "sí" : "no"}`}
          title={p.title}
        >
          <span className="erp-status-pill-dot" aria-hidden />
          {p.label}
        </span>
      ))}
    </span>
  );
}
