"use client";

import { useEffect, useState } from "react";
import {
  attachDocument,
  fetchShippingThumb,
  listFotos,
  openShippingFile,
  type FotoPerdida,
  type ShipmentFile,
} from "../../lib/erpApi";
import { mensajeSubida, prepararFoto } from "../../lib/fotos";

/** Fotos (y documentos) del embalaje del pedido: miniaturas pulsables (se
 *  abren a tamaño completo en otra pestaña) y, mientras el pedido no se haya
 *  recogido, «📷 Añadir foto». La foto se guarda junto al albarán y la
 *  etiqueta (no se pierde al desplegar). Una subida que falla lo dice siempre;
 *  nunca «adjuntada» si no lo está.
 *
 *  Con `fotos` (la Cola SAT ya las trae en cada pedido) no se piden aparte;
 *  sin ellas (ficha) se cargan aquí, con las que se perdieron en un despliegue
 *  para avisar de volver a subirlas. */
export function FotosEmbalaje({
  orderId,
  fotos,
  canUpload,
  onChanged,
  compact = false,
}: {
  orderId: string;
  fotos?: ShipmentFile[];
  /** Ofrecer «Añadir foto» (permiso de envíos y pedido aún sin recoger). */
  canUpload: boolean;
  /** Tras subir una: la cola / ficha recarga. */
  onChanged?: () => void;
  /** En una tarjeta: sin texto cuando no hay fotos. */
  compact?: boolean;
}) {
  // Las que llegan (cola) o se cargan (ficha), más las subidas aquí mismo.
  const [cargadas, setCargadas] = useState<ShipmentFile[]>([]);
  const [subidas, setSubidas] = useState<ShipmentFile[]>([]);
  const [perdidas, setPerdidas] = useState<FotoPerdida[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const propias = fotos === undefined;
  const base = propias ? cargadas : fotos;
  const items = [...base, ...subidas.filter((s) => !base.some((f) => f.id === s.id))];

  useEffect(() => {
    if (!propias) return;
    let vivo = true;
    // Encadenado: cualquier fallo (red, permiso…) se queda en «sin fotos» y
    // nunca rompe la ficha.
    Promise.resolve()
      .then(() => listFotos(orderId))
      .then((r) => {
        if (!vivo) return;
        setCargadas(r.items);
        setPerdidas(r.fotos_perdidas);
      })
      .catch(() => { /* sin fotos que enseñar */ });
    return () => { vivo = false; };
  }, [orderId, propias]);

  async function subir(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const lista = await prepararFoto(file);
      const r = await attachDocument(orderId, lista);
      setSubidas((prev) => [...prev, r.file]);
      setNotice("Foto adjuntada.");
      onChanged?.();
    } catch (err) {
      setError(mensajeSubida(err));
    } finally {
      setBusy(false);
    }
  }

  if (compact && items.length === 0 && !canUpload) return null;
  return (
    <section className="erp-fotos" aria-label="Fotos del embalaje">
      {items.length > 0 ? (
        <ul className="erp-fotos-lista">
          {items.map((f) => <FotoMiniatura key={f.id} file={f} />)}
        </ul>
      ) : compact ? null : (
        <p className="muted small">Sin fotos del embalaje.</p>
      )}
      {perdidas.length > 0 ? (
        <p className="erp-fotos-perdidas small" role="note">
          {perdidas.length === 1 ? "Una foto se perdió" : `${perdidas.length} fotos se perdieron`}{" "}
          en un despliegue ({perdidas.map((p) => p.filename || "foto").join(", ")}): vuelve a
          subirla si aún la tienes.
        </p>
      ) : null}
      {canUpload ? (
        <label className={`button secondary${compact ? " small" : ""} erp-fotos-subir`}
               aria-disabled={busy || undefined}>
          {busy ? "Subiendo…" : "📷 Añadir foto"}
          <input type="file" accept="image/*,.heic,.heif,application/pdf" hidden
                 aria-label="Añadir foto del embalaje" disabled={busy} onChange={subir} />
        </label>
      ) : null}
      {error ? <p className="form-error small" role="alert">{error}</p> : null}
      {notice ? <p className="form-success small" role="status">{notice}</p> : null}
    </section>
  );
}

/** Miniaturas ya pedidas, por URL: la cola se refresca cada pocos segundos y
 *  no debe volver a pedirlas. Pequeñas (unos KB); se guardan las últimas
 *  `MAX_MINIATURAS` y las que salen se liberan. */
const MAX_MINIATURAS = 200;
const miniaturas = new Map<string, Promise<string>>();
const resueltas = new Map<string, string>();

function miniatura(clave: string): Promise<string> {
  const hay = miniaturas.get(clave);
  if (hay) return hay;
  const p = Promise.resolve()
    .then(() => fetchShippingThumb(clave))
    .then((blob) => {
      const url = URL.createObjectURL(blob);
      resueltas.set(clave, url);
      return url;
    });
  // Un fallo no se queda guardado: la próxima vez se reintenta.
  p.catch(() => { miniaturas.delete(clave); });
  miniaturas.set(clave, p);
  while (miniaturas.size > MAX_MINIATURAS) {
    const vieja = miniaturas.keys().next().value as string;
    miniaturas.delete(vieja);
    const url = resueltas.get(vieja);
    if (url) URL.revokeObjectURL(url);
    resueltas.delete(vieja);
  }
  return p;
}

/** Solo para los tests: vacía la caché de miniaturas. */
export function limpiarMiniaturas(): void {
  miniaturas.clear();
  resueltas.clear();
}

/** Una foto: miniatura pulsable (la foto entera se abre en otra pestaña). Un
 *  PDF, su nombre. Mientras carga la miniatura, el nombre. */
function FotoMiniatura({ file }: { file: ShipmentFile }) {
  const clave = file.download_url;
  const esImagen = file.mime_type.startsWith("image/");
  const [cargada, setCargada] = useState<{ clave: string; src: string } | null>(null);
  // La que ya está en caché se pinta sin esperar.
  const src = cargada?.clave === clave ? cargada.src : (resueltas.get(clave) ?? null);
  // El efecto depende de la URL, no del objeto: un refresco de la cola trae
  // objetos nuevos para las mismas fotos y no debe volver a pedirlas.
  useEffect(() => {
    if (!esImagen) return;
    let vivo = true;
    miniatura(clave)
      .then((url) => { if (vivo) setCargada({ clave, src: url }); })
      .catch(() => { /* se queda el nombre */ });
    return () => { vivo = false; };
  }, [clave, esImagen]);

  return (
    <li>
      <button type="button" className="erp-foto-thumb" title={`Abrir ${file.filename}`}
              aria-label={`Ver ${file.filename}`} onClick={() => void openShippingFile(file)}>
        {/* Blob de una descarga autenticada: `next/image` no la puede optimizar. */}
        {/* eslint-disable-next-line @next/next/no-img-element */}
        {src ? <img src={src} alt={file.filename} /> : (
          <span className="erp-foto-nombre">{esImagen ? "📷" : "📄"} {file.filename}</span>
        )}
      </button>
    </li>
  );
}
