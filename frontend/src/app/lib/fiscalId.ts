/** Prefijos de NIF-IVA intracomunitario (espejo de `_VAT_PREFIX_TO_ISO2` del
 *  backend: los 27 de la UE + `EL` Grecia y `XI` Irlanda del Norte). */
const VAT_PREFIXES = new Set([
  "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR",
  "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK",
  "SI", "ES", "SE", "EL", "XI",
]);

/** Quita separadores y pasa a mayúsculas (`fr 91.523.447-399` → `FR91523447399`). */
export function normalizeFiscalId(value: string): string {
  return (value || "").replace(/[\s.\-/]/g, "").toUpperCase();
}

/** ¿El texto tiene pinta de NIF / CIF / NIF-IVA (y no de un nombre)?
 *
 *  Acepta el NIF español con o sin letra (`B64113590`, `12345678Z`,
 *  `X1234567L`) y el NIF-IVA con prefijo de país (`FR91523447399`,
 *  `ESB64113590`). El regex anterior solo cubría el español, así que un
 *  NIF-IVA extranjero se buscaba «por nombre» y no encontraba nada. */
export function looksLikeFiscalId(value: string): boolean {
  const raw = normalizeFiscalId(value);
  if (!raw) return false;
  // NIF-IVA: prefijo de país + 2-12 alfanuméricos con al menos un dígito.
  if (VAT_PREFIXES.has(raw.slice(0, 2))) {
    const rest = raw.slice(2);
    if (/^[A-Z0-9]{2,12}$/.test(rest) && /\d/.test(rest)) return true;
  }
  // NIF/CIF español desnudo: letra opcional + 7-8 dígitos + letra opcional.
  return /^[A-Z]?\d{7,8}[A-Z]?$/.test(raw);
}
