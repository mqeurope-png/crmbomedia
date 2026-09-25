/** Cola SAT — un color por pestaña, para distinguirlas de un vistazo.
 *
 *  Fondo PASTEL + texto OSCURO del mismo tono: el texto se lee bien sobre el
 *  color (contraste ≥ 7:1, AAA, comprobado en el test con la fórmula WCAG).
 *  La pestaña activa va un punto más saturada de su propio color y lleva un
 *  subrayado del color del texto. */

export type SatTabKey =
  | "pendientes" | "por_embalar" | "en_preparacion" | "embalados"
  | "pendiente_recogida" | "enviados" | "sin_envio" | "incidencias";

export type SatTabColor = {
  /** Fondo de la pestaña (pastel). */
  bg: string;
  /** Fondo de la pestaña activa (su mismo color, más saturado). */
  bgActive: string;
  /** Texto (oscuro, del mismo tono). */
  fg: string;
};

export const SAT_TAB_COLORS: Record<SatTabKey, SatTabColor> = {
  pendientes: { bg: "#DBEAFE", bgActive: "#BFDBFE", fg: "#1E3A8A" },         // azul
  por_embalar: { bg: "#FED7AA", bgActive: "#FDBA74", fg: "#431407" },        // naranja
  en_preparacion: { bg: "#FEF3C7", bgActive: "#FDE68A", fg: "#78350F" },     // amarillo
  embalados: { bg: "#DCFCE7", bgActive: "#BBF7D0", fg: "#14532D" },          // verde
  pendiente_recogida: { bg: "#EDE9FE", bgActive: "#DDD6FE", fg: "#4C1D95" }, // lila
  enviados: { bg: "#E5E7EB", bgActive: "#D1D5DB", fg: "#1F2937" },           // gris
  sin_envio: { bg: "#EAE0D5", bgActive: "#D9C7B3", fg: "#44291A" },          // marrón
  incidencias: { bg: "#FEE2E2", bgActive: "#FECACA", fg: "#701A1A" },        // rojo
};

function channel(c: number): number {
  const v = c / 255;
  return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
}

/** Luminancia relativa (WCAG 2.x) de un color `#RRGGBB`. */
export function luminance(hex: string): number {
  const n = hex.replace("#", "");
  const r = parseInt(n.slice(0, 2), 16);
  const g = parseInt(n.slice(2, 4), 16);
  const b = parseInt(n.slice(4, 6), 16);
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

/** Contraste WCAG entre dos colores (1–21). */
export function contrastRatio(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}
