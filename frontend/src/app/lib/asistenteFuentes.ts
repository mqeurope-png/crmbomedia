/** Qué consulta el asistente SOLO al redactar, además del catálogo: el índice
 *  de nuestras webs («Webs») y el soporte de los fabricantes («Soporte»). Son
 *  las dos casillas de la franja: marcadas de partida y recordadas por usuario
 *  en este navegador, igual que el alcance. Viajan en cada petición. */
export type FuentesAsistente = { webs: boolean; soporte: boolean };

export const FUENTES_DE_PARTIDA: FuentesAsistente = { webs: true, soporte: true };

const CLAVE = "bohub.asistente.fuentes";

/** Las casillas de este usuario en este navegador (por id de usuario, así dos
 *  personas en el mismo navegador no se pisan). Sin nada guardado, o con algo
 *  que no es lo que se guardó, las de partida. */
export function leerFuentes(usuarioId: string | null | undefined): FuentesAsistente {
  if (!usuarioId) return { ...FUENTES_DE_PARTIDA };
  try {
    const v = window.localStorage.getItem(`${CLAVE}.${usuarioId}`);
    const d: unknown = v ? JSON.parse(v) : null;
    if (typeof d !== "object" || d === null || Array.isArray(d)) return { ...FUENTES_DE_PARTIDA };
    const { webs, soporte } = d as Record<string, unknown>;
    return {
      webs: typeof webs === "boolean" ? webs : FUENTES_DE_PARTIDA.webs,
      soporte: typeof soporte === "boolean" ? soporte : FUENTES_DE_PARTIDA.soporte,
    };
  } catch {
    return { ...FUENTES_DE_PARTIDA };
  }
}

export function guardarFuentes(usuarioId: string | null | undefined, fuentes: FuentesAsistente): void {
  if (!usuarioId) return;
  try {
    window.localStorage.setItem(
      `${CLAVE}.${usuarioId}`, JSON.stringify({ webs: fuentes.webs, soporte: fuentes.soporte }),
    );
  } catch {
    /* sin almacenamiento local no pasa nada: se recuerda mientras dure la página */
  }
}
