/** Envíos con OTRO courier (no Genei): lista habitual y sugerencia por el
 *  formato del nº de seguimiento. Espejo de `backend/app/erp/shipping_courier.py`
 *  (el enlace de seguimiento lo calcula el backend). */

export const COURIERS: readonly string[] = [
  "UPS", "CTT Express", "MRW", "GLS", "DSV", "FedEx", "DHL", "Correos Express",
  "Seitrans", "TNT", "DB Schenker", "MBE",
];

/** Texto cuando el envío externo no tiene courier informado. */
export const OTHER_COURIER_LABEL = "otro courier";

/** Courier por el formato del tracking (solo los seguros): `1Z…` → UPS;
 *  `0033` + 18 dígitos → CTT Express. */
export function suggestCourier(tracking: string | null | undefined): string | null {
  const t = (tracking ?? "").replace(/\s+/g, "").toUpperCase();
  if (/^1Z[0-9A-Z]{16}$/.test(t)) return "UPS";
  if (/^0033\d{18}$/.test(t)) return "CTT Express";
  return null;
}
