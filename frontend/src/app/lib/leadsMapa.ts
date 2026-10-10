/** El mapa de plantillas de la respuesta a leads: una fila por conjunto de
 *  intereses e idioma. Funciones puras (sin red), para que las pantallas y
 *  sus tests las compartan. */

/** La clave del mapa para un conjunto de intereses y un idioma: códigos sin
 *  repetir y ordenados, unidos con `+` (la misma que compone el servidor,
 *  `plantillas.clave_mapa`). Un solo interés: `vending:es`. */
export function claveMapaLeads(intereses: readonly string[], idioma: string): string {
  const codigos = Array.from(
    new Set(intereses.map((i) => i.trim().toLowerCase()).filter(Boolean)),
  );
  codigos.sort();
  return `${codigos.join("+")}:${idioma.trim().toLowerCase()}`;
}

/** `(códigos, idioma)` de una clave del mapa. */
export function partesClaveMapaLeads(clave: string): { intereses: string[]; idioma: string } {
  const [intereses = "", idioma = ""] = clave.split(":");
  return { intereses: intereses.split("+").filter(Boolean), idioma };
}

/** Dos listas de códigos iguales, en el mismo orden. */
export function listasIguales(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((v, i) => v === b[i]);
}

/** Los intereses que mandan en una clasificación: la lista si viene; si no
 *  (una respuesta anterior a la lista), el principal solo. */
export function listaIntereses(
  intereses: readonly string[] | undefined | null, interes: string | null | undefined,
): string[] {
  if (intereses && intereses.length > 0) return [...intereses];
  return interes ? [interes] : [];
}
