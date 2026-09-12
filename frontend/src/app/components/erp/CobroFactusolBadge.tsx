import type { FactusolCobroBlock, FactusolCobroStatus } from "../../lib/erpApi";

function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString("es-ES");
}

/** Estado de cobro EN FACTUSOL de la factura del pedido (estado CONTABLE:
 *  ESTFAC / saldo en F_LCO), al estilo del badge de facturación. Es distinto
 *  del «Pagado» de la columna PAGO, que es el estado del CRM (el cliente
 *  pagó): aquí se ve si el cobro está REGISTRADO en FACTUSOL. Sin factura no
 *  se pinta nada; con factura pero sin comprobar, un badge gris. */
export function CobroFactusolBadge({
  hasInvoice,
  status,
  cobro,
}: {
  hasInvoice: boolean;
  status?: FactusolCobroStatus | null;
  cobro?: FactusolCobroBlock | null;
}) {
  if (!hasInvoice) return null;
  const base = "Estado contable en FACTUSOL (ESTFAC / cobros en F_LCO); no es el «Pagado» del CRM.";
  const checked = cobro?.checked_at ? ` Comprobado ${fmtDate(cobro.checked_at)}.` : "";
  if (status === "cobrada") {
    return (
      <span
        className="badge ok"
        title={`${base} Factura ${cobro?.numero ?? ""} cobrada${cobro?.total != null ? ` (${cobro.total.toFixed(2)} €)` : ""}.${checked}`}
      >
        Cobrado FACTUSOL
      </span>
    );
  }
  if (status === "pendiente") {
    const saldo = cobro?.saldo_pendiente != null ? ` Saldo ${cobro.saldo_pendiente.toFixed(2)} €.` : "";
    return (
      <span className="badge warn" title={`${base} Factura ${cobro?.numero ?? ""} sin cobro registrado.${saldo}${checked}`}>
        Pendiente de cobro FACTUSOL
      </span>
    );
  }
  return (
    <span
      className="badge muted"
      title={`${base} Aún no se ha comprobado: usa «Actualizar cobros FACTUSOL» o abre el pedido.`}
    >
      Cobro FACTUSOL sin comprobar
    </span>
  );
}
