/** CRM-COMPOSITOR-V2.2 — configuración de seguridad del editor.
 *
 *  Tags que TinyMCE rechaza en paste/edición (cinturón cliente). La
 *  barrera REAL es el sanitizador bleach del backend, que se aplica
 *  siempre en el envío — esto solo evita que el editor los muestre. */
export const EDITOR_INVALID_ELEMENTS =
  "script,iframe,object,embed,form,input,button";
