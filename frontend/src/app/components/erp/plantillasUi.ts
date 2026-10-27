import type {
  OfertaIdioma,
  PlantillaCarpetaNode,
  PlantillaDestino,
  PlantillaDocumento,
  PlantillaOrigenTipo,
} from "../../lib/erpApi";

/** Textos y utilidades comunes de las plantillas de documento (pantalla de
 *  gestión, selector en el punto de uso y «Guardar como plantilla»). */

export const IDIOMAS_PLANTILLA: { value: OfertaIdioma; label: string }[] = [
  { value: "es", label: "ES · Español" },
  { value: "en", label: "EN · English" },
  { value: "de", label: "DE · Deutsch" },
  { value: "fr", label: "FR · Français" },
  { value: "nl", label: "NL · Nederlands" },
];

/** El destino con su artículo: «la oferta», «la proforma», «el pedido». */
export const DESTINO_LABEL: Record<PlantillaDestino, string> = {
  oferta: "la oferta",
  proforma: "la proforma",
  pedido: "el pedido",
};

const ORIGEN_LABEL: Record<PlantillaOrigenTipo, string> = {
  presupuesto: "la proforma",
  albaran: "el albarán",
  pedido: "el pedido",
  manual: "",
};

/** «Guardada desde la proforma 2-000040» (null si se hizo a mano). */
export function origenTexto(p: Pick<PlantillaDocumento, "origen_tipo" | "origen_numero">): string | null {
  if (!p.origen_tipo || p.origen_tipo === "manual") return null;
  const doc = ORIGEN_LABEL[p.origen_tipo] ?? "un documento";
  return `Guardada desde ${doc}${p.origen_numero ? ` ${p.origen_numero}` : ""}`;
}

export const CARPETA_TODAS = "__todas__";
export const CARPETA_SIN = "__sin__";

export type CarpetaPlana = { id: string; nombre: string; depth: number; ruta: string };

/** El árbol aplanado (con su profundidad y su ruta) para los `<select>`. */
export function flattenCarpetas(
  nodes: PlantillaCarpetaNode[], depth = 0, prefijo = "",
): CarpetaPlana[] {
  const out: CarpetaPlana[] = [];
  for (const n of nodes) {
    const ruta = prefijo ? `${prefijo} / ${n.nombre}` : n.nombre;
    out.push({ id: n.id, nombre: n.nombre, depth, ruta });
    out.push(...flattenCarpetas(n.children, depth + 1, ruta));
  }
  return out;
}

/** Busca un nodo por id en el árbol. */
export function findCarpeta(
  nodes: PlantillaCarpetaNode[], id: string | null | undefined,
): PlantillaCarpetaNode | null {
  if (!id) return null;
  for (const n of nodes) {
    if (n.id === id) return n;
    const hit = findCarpeta(n.children, id);
    if (hit) return hit;
  }
  return null;
}

/** Filtros de la lista según la carpeta elegida en el árbol. */
export function filtroCarpeta(sel: string): { carpeta_id?: string; sin_carpeta?: boolean } {
  if (sel === CARPETA_TODAS) return {};
  if (sel === CARPETA_SIN) return { sin_carpeta: true };
  return { carpeta_id: sel };
}
