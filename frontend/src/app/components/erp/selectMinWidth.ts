/** Ancho mínimo de un `<select>` del pie de un modal, a partir de sus
 *  opciones: el pie es una fila de controles y, cuando se llena, el navegador
 *  comprime los `<select>` por debajo de su contenido — se queda el recuadro
 *  con la flechita y sin texto.
 *
 *  El caso que lo destapó (producción, 09/10/2026): el detalle del presupuesto
 *  2-004364 metía ocho cosas en el pie (tipo de documento, divisa, idioma, la
 *  nota del idioma y cuatro botones) y el primer selector —el que decide si
 *  sale «Presupuesto» o «Factura proforma», o sea el que más importa— se
 *  quedaba sin ancho útil. Los de divisa e idioma aguantaban porque su
 *  contenido es «EUR» y «ES».
 *
 *  El `flex-shrink: 0` del CSS (`.modal-actions`) ya evita que se compriman;
 *  esto pone además un suelo explícito, que es lo que se puede comprobar en un
 *  test (jsdom no calcula el ancho intrínseco de las opciones).
 *
 *  `ch` es el ancho del «0» de la fuente, así que la cuenta va en caracteres
 *  de la opción más larga; el sumando cubre el padding del control y la
 *  flechita. Sin opciones no se fuerza nada (`undefined`): un selector sin
 *  contenido no tiene ancho que defender. */
export const SELECT_CHROME = "2.75rem";

export function selectMinWidth(labels: readonly string[]): string | undefined {
  const longest = Math.max(0, ...labels.map((l) => (l ?? "").trim().length));
  if (!longest) return undefined;
  return `calc(${longest}ch + ${SELECT_CHROME})`;
}
