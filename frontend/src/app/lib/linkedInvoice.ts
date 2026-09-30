/** Factura de FACTUSOL vinculada a un pedido. UNA sola definición (espejo de
 *  `backend/app/erp/linked_invoice.py`): estado facturado + nº + serie, sea
 *  cual sea el origen del pedido. El backend la manda ya resuelta en
 *  `factusol_invoice` / `factusol_invoice_problem`. */

/** Clave compuesta de la factura; `numero` = «2-526107». */
export type LinkedInvoice = { serie: number; codigo: number; numero: string };

/** Texto de la factura de un pedido: «2-526107»; con nº pero sin serie, el nº
 *  y «falta la serie»; «» si no tiene. */
export function invoiceLabel(o: {
  factusol_invoice?: LinkedInvoice | null;
  factusol_invoice_problem?: string | null;
  factusol_invoice_number: string | null;
}): string {
  if (o.factusol_invoice) return o.factusol_invoice.numero;
  if (o.factusol_invoice_problem === "sin_serie" && o.factusol_invoice_number) {
    return `${o.factusol_invoice_number} · falta la serie`;
  }
  return o.factusol_invoice_number ?? "";
}
