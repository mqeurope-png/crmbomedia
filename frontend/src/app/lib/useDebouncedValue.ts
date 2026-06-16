/**
 * Debounce hook compartido por los pickers server-side
 * (TagMultiSelectFilter, UserPicker, SegmentPicker, BrevoListPicker,
 * CompanyPicker). PR-Cg los movió de "fetch lista completa + filter
 * client" a "fetch debounced server-side" → 300ms es el punto donde
 * el operador deja de teclear pero sigue percibiendo respuesta.
 *
 * Devuelve el valor sólo tras `ms` ms estable. Si `value` cambia
 * antes, reinicia el timer.
 */
import { useEffect, useState } from "react";

export function useDebouncedValue<T>(value: T, ms = 300): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const handle = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(handle);
  }, [value, ms]);
  return debounced;
}
