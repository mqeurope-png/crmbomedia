import { ApiError } from "./api";
import { extractErrorMessage } from "./errors";

/** Fotos del embalaje: preparar la foto en el navegador antes de subirla. */

/** Tope del servidor por fichero. */
export const FOTO_MAX_BYTES = 15 * 1024 * 1024;
/** Lado mayor de una foto reducida: de sobra para ver el embalaje. */
export const FOTO_LADO_MAX = 2560;
/** A partir de aquí se reduce (una foto de móvil moderno pasa de esto). */
const UMBRAL_REDUCIR = 2.5 * 1024 * 1024;

function esImagen(file: File): boolean {
  if (file.type.startsWith("image/")) return true;
  // Algunos Android dejan el tipo vacío con HEIC/HEIF: se mira la extensión.
  return /\.(heic|heif|jpe?g|png|webp)$/i.test(file.name);
}

function nombreJpg(name: string): string {
  const base = name.replace(/\.[^.]+$/, "") || "foto";
  return `${base}.jpg`;
}

/** Si la foto pesa mucho, la reduce en el navegador (lado mayor 2560 px, JPEG
 *  al 85 %) en vez de dejar que el servidor la rechace. Si el navegador no
 *  sabe leerla (HEIC fuera de Safari) se manda tal cual y el servidor la
 *  convierte; solo si además pasa de 15 MB se avisa aquí. PDF, tal cual. */
export async function prepararFoto(file: File): Promise<File> {
  if (!esImagen(file) || file.size <= UMBRAL_REDUCIR) return file;
  try {
    const bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
    const escala = Math.min(1, FOTO_LADO_MAX / Math.max(bitmap.width, bitmap.height));
    const w = Math.max(1, Math.round(bitmap.width * escala));
    const h = Math.max(1, Math.round(bitmap.height * escala));
    const canvas = document.createElement("canvas");
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("sin canvas");
    ctx.drawImage(bitmap, 0, 0, w, h);
    bitmap.close?.();
    const blob = await new Promise<Blob | null>((resolve) => {
      canvas.toBlob(resolve, "image/jpeg", 0.85);
    });
    if (blob && blob.size > 0 && blob.size < file.size) {
      return new File([blob], nombreJpg(file.name), { type: "image/jpeg" });
    }
  } catch {
    // El navegador no sabe leerla: se manda tal cual (el servidor convierte).
  }
  if (file.size > FOTO_MAX_BYTES) {
    const mb = (file.size / 1024 / 1024).toFixed(1);
    throw new Error(
      `La foto pesa ${mb} MB y este navegador no puede reducirla. Hazla con menos `
      + "resolución o súbela como JPG (máximo 15 MB).",
    );
  }
  return file;
}

/** El motivo de un fallo de subida, siempre legible (también cuando el
 *  rechazo viene del proxy, sin el detalle de la API). */
export function mensajeSubida(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 413 && !err.detail) {
      return "El archivo es demasiado grande (máximo 15 MB).";
    }
    if (err.status === 415 && !err.detail) {
      return "Formato no admitido. Sube una foto (JPG, PNG, HEIC o WebP) o un PDF.";
    }
  }
  return extractErrorMessage(err, "No se pudo subir la foto.");
}
