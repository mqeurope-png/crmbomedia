import type { IdiomaMencion, MencionLegal, TextoMencion } from "./erpApi";

/** Menciones legales por idioma: qué texto lleva la factura de un pedido.
 *  Funciones puras (fuera de `erpApi` para que los tests que lo simulan las
 *  sigan teniendo). */

/** Los idiomas de una mención, en el orden de la pantalla. */
export const IDIOMAS_MENCION = {
  es: "castellano", en: "inglés", de: "alemán", fr: "francés", nl: "neerlandés",
  pt: "portugués",
} as const satisfies Record<IdiomaMencion, string>;

/** «en», «EN», «en_GB» → «en»; lo que no está en la lista, castellano. */
export function idiomaMencion(lang: string | null | undefined): IdiomaMencion {
  const corto = (lang ?? "").trim().toLowerCase().slice(0, 2);
  return corto in IDIOMAS_MENCION ? (corto as IdiomaMencion) : "es";
}

/** El texto de una mención en un idioma (lo guardado antes de haber idiomas
 *  es el castellano). */
export function textoMencion(m: MencionLegal, idioma: IdiomaMencion): TextoMencion {
  const t = m.textos?.[idioma];
  if (t) return t;
  if (idioma === "es" && !m.textos) return { texto: m.texto, revisado: m.revisado };
  return { texto: "", revisado: false };
}

/** Qué texto llevará la factura de un pedido en `idioma` y, si no va en su
 *  idioma, el aviso (como lo decide el backend). `null`: no se puede aplicar. */
export function mencionParaIdioma(
  m: MencionLegal, idioma: IdiomaMencion,
): { texto: string; aviso: string | null } | null {
  const propio = textoMencion(m, idioma);
  if (propio.texto && propio.revisado) return { texto: propio.texto, aviso: null };
  const base = textoMencion(m, "es");
  if (!base.texto || !base.revisado) return null;
  if (idioma === "es") return { texto: base.texto, aviso: null };
  const falta = propio.texto ? "lo tiene sin revisar" : "no lo tiene";
  return {
    texto: base.texto,
    aviso: `El pedido está en ${IDIOMAS_MENCION[idioma]} y la mención «${m.nombre}» ${falta} `
      + `en ese idioma: va en castellano. El texto en ${IDIOMAS_MENCION[idioma]} se añade en `
      + "Configuración ERP.",
  };
}
