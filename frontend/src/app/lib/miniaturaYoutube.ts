/** La miniatura de un vídeo de YouTube para un correo, sin tocar su API.
 *
 *  Valen las dos que son 16:9: `hqdefault` (480×360) y `sddefault` (640×480)
 *  son 4:3 y a un vídeo apaisado le ponen bandas negras arriba y abajo.
 *  `maxresdefault` (1280×720) solo existe si el vídeo se subió en HD; si no,
 *  YouTube sirve en su lugar un marcador de 120×90. `mqdefault` (320×180)
 *  existe siempre: se pone al ancho que le toque y se deja escalar, antes que
 *  meter bandas negras en un correo comercial. */

export const MINIATURAS_YOUTUBE = ["maxresdefault", "mqdefault"] as const;
export type CalidadMiniatura = (typeof MINIATURAS_YOUTUBE)[number];

/** El marcador que sirve YouTube cuando no hay `maxresdefault` mide 120 px. */
const ANCHO_MARCADOR = 120;

export function urlMiniaturaYoutube(id: string, calidad: CalidadMiniatura): string {
  return `https://img.youtube.com/vi/${id}/${calidad}.jpg`;
}

/** El ancho real de una imagen, cargándola en el navegador (0 si no carga a
 *  tiempo). No necesita CORS: solo se mira su tamaño. */
export function anchoDeImagen(src: string, esperaMs = 4000): Promise<number> {
  return new Promise((resolve) => {
    const img = new Image();
    const corte = window.setTimeout(() => resolve(0), esperaMs);
    img.onload = () => {
      window.clearTimeout(corte);
      resolve(img.naturalWidth || 0);
    };
    img.onerror = () => {
      window.clearTimeout(corte);
      resolve(0);
    };
    img.src = src;
  });
}

/** La mejor miniatura 16:9 del vídeo: `maxresdefault` si existe de verdad
 *  (no el marcador), y si no, `mqdefault`. */
export async function miniaturaYoutube(
  id: string,
  medir: (src: string) => Promise<number> = anchoDeImagen,
): Promise<string> {
  const grande = urlMiniaturaYoutube(id, "maxresdefault");
  return (await medir(grande)) > ANCHO_MARCADOR ? grande : urlMiniaturaYoutube(id, "mqdefault");
}
