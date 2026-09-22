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
 *  Es SIMÉTRICO respecto al prefijo de país: tanto `FR91523447399` como el
 *  número desnudo `91523447399` se reconocen como identificador fiscal. Antes
 *  el desnudo se colaba al `else` español (letra opcional + 7-8 dígitos), no
 *  casaba, y la búsqueda se hacía «por nombre» — por eso `FR91523447399`
 *  encontraba la empresa y `91523447399` no. */
export function looksLikeFiscalId(value: string): boolean {
  const raw = normalizeFiscalId(value);
  if (!raw) return false;
  // NIF-IVA: prefijo de país + 2-12 alfanuméricos con al menos un dígito.
  if (VAT_PREFIXES.has(raw.slice(0, 2))) {
    const rest = raw.slice(2);
    if (/^[A-Z0-9]{2,12}$/.test(rest) && /\d/.test(rest)) return true;
  }
  // NIF/CIF español: letra opcional + 7-8 dígitos + letra opcional.
  if (/^[A-Z]?\d{7,8}[A-Z]?$/.test(raw)) return true;
  // NIF-IVA extranjero DESNUDO (sin prefijo de país): 8-12 alfanuméricos que
  // empiezan por dígito y son casi todo dígitos (`91523447399`, `123456789B01`).
  // El límite de letras evita confundirlo con un nombre («3M ESPAÑA»).
  if (!/^\d[A-Z0-9]{7,11}$/.test(raw)) return false;
  return raw.replace(/\d/g, "").length <= 2;
}
