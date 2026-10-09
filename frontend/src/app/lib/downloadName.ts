/** Nombre de fichero de una descarga de la API: el que manda el servidor.
 *
 *  El backend ya nombra bien lo que sirve (`Factura LABORATORIOS PORTA
 *  5-260066.pdf`, traducido según el idioma del cliente, #531), pero hasta
 *  ahora el frontend lo tiraba: `apiDownloadBlob` devolvía el Blob a secas y
 *  cada pantalla se inventaba el nombre al guardarlo. Esto lee
 *  `Content-Disposition` y lo lleva pegado al binario.
 *
 *  El nombre viaja como `File` (un Blob con `name`): así ninguna firma cambia
 *  —`downloadFactusolDocumentPdf` & co. siguen devolviendo `Promise<Blob>`—
 *  y `saveBlob` solo tiene que mirar si lo que le llega trae nombre. Si la
 *  cabecera falta o no se entiende, llega un Blob normal y cada pantalla cae
 *  al nombre que construía hasta hoy: ninguna descarga se queda sin nombre.
 *
 *  Ojo con un espejismo: la cabecera se ve en la pestaña Network, pero si la
 *  API estuviera en OTRO origen el navegador no la deja leer desde JS sin un
 *  `Access-Control-Expose-Headers: Content-Disposition` del backend. En
 *  producción no pasa (nginx sirve `/api/` y la app bajo el mismo host). */

/** Nombre de `Content-Disposition`, o null si no trae ninguno.
 *
 *  Prioriza `filename*=UTF-8''…` (RFC 5987, con el porcentaje decodificado)
 *  sobre `filename="…"` / `filename=token`. Un `filename*` que no se puede
 *  decodificar no rompe nada: se pasa al `filename` normal. Se devuelve solo
 *  el nombre base, sin rutas, por si un servidor raro colara barras. */
export function filenameFromContentDisposition(
  header: string | null | undefined,
): string | null {
  if (!header) return null;
  const extended = /filename\*\s*=\s*([^']*)'[^']*'([^;]*)/i.exec(header);
  if (extended) {
    const charset = (extended[1] || "utf-8").toLowerCase();
    const raw = extended[2].trim();
    if (charset === "utf-8" || charset === "utf8") {
      try {
        const name = basename(decodeURIComponent(raw));
        if (name) return name;
      } catch {
        // porcentaje mal formado: se prueba con el `filename` corriente
      }
    }
  }
  const plain = /(?:^|[;\s])filename\s*=\s*(?:"((?:\\.|[^"\\])*)"|([^;\s]+))/i.exec(header);
  if (!plain) return null;
  const value = plain[1] !== undefined ? plain[1].replace(/\\(.)/g, "$1") : plain[2];
  return basename(value) || null;
}

function basename(value: string): string {
  const sinRuta = value.split(/[\\/]/).pop() ?? "";
  return sinRuta.trim();
}

/** El Blob descargado con el nombre que mandó el servidor pegado (un `File`),
 *  o el mismo Blob si la respuesta no traía nombre. */
export function withServerFilename(
  blob: Blob, header: string | null | undefined,
): Blob {
  const name = filenameFromContentDisposition(header);
  if (!name) return blob;
  return new File([blob], name, { type: blob.type });
}

/** Nombre con el que guardar un binario: el del servidor si viene pegado
 *  (`withServerFilename`), y si no el de respaldo que construye la pantalla. */
export function downloadName(blob: Blob, fallback: string): string {
  const name = blob instanceof File ? blob.name.trim() : "";
  return name || fallback;
}
