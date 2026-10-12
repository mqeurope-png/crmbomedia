"use client";

import { useState } from "react";
import {
  createKbVideo,
  getCatalogoProductos,
  getKbImagenes,
  getKbVideos,
  sincronizarKbImagenes,
  sincronizarKbVideos,
  updateKbImagen,
  updateKbVideo,
  type CatalogoSincronizacion,
  type Conocimiento,
  type KbImagen,
  type KbVideo,
  type KbVideoCambios,
  type MediosEstado,
} from "../../lib/erpApi";
import { extractErrorMessage } from "../../lib/errors";

type Props = {
  datos: Conocimiento;
  canEdit: boolean;
  onDatos: (d: Conocimiento) => void;
};

const IDIOMAS = ["es", "en", "de", "fr", "nl", "pt", "it"];
const NOMBRE_IDIOMA: Record<string, string> = {
  es: "castellano", en: "inglés", de: "alemán", fr: "francés", nl: "neerlandés", pt: "portugués",
  it: "italiano",
};
const FUENTE_IDIOMA: Record<string, string> = {
  manual: "a mano", titulo: "por el título", metadatos: "de YouTube", audio: "del audio en YouTube",
};
const ORIGEN: Record<string, string> = { canal: "canal", web: "enlazado en una web", manual: "pegado a mano" };

function fecha(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("es-ES", { day: "2-digit", month: "2-digit", year: "numeric",
    hour: "2-digit", minute: "2-digit" });
}

function duracion(segundos: number | null): string {
  if (!segundos) return "—";
  const m = Math.floor(segundos / 60);
  const s = segundos % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

function ultima(s: CatalogoSincronizacion | null): string {
  return s ? `${fecha(s.inicio)} · ${s.origen} · ${s.estado}` : "ninguna";
}

function resumenPasada(s: CatalogoSincronizacion, cosa: string): string {
  if (s.estado === "error") return `Sincronización en error: ${s.detalle ?? "sin detalle"}. Lo que había sigue intacto.`;
  const base = `Sincronizado: ${s.productos} ${cosa} (${s.nuevos} nuevas, ${s.actualizados} cambiadas, ${s.idos} desaparecidas).`;
  return s.estado === "parcial" ? `${base} Parcial: ${s.detalle ?? "alguna web no respondió"}.` : base;
}

function conMedios(datos: Conocimiento, cambio: Partial<MediosEstado>): Conocimiento {
  return datos.medios ? { ...datos, medios: { ...datos.medios, ...cambio } } : datos;
}

// --- imágenes ------------------------------------------------------------------------

/** El índice de imágenes de las webs: lo que la IA puede poner en un hero o en
 *  una galería de muestras. Se cura a mano: favoritas (van primero) y ocultas
 *  (no se le ofrecen nunca). */
export function ImagenesBloque({ datos, canEdit, onDatos }: Props) {
  const estado = datos.medios?.imagenes;
  const [sincronizando, setSincronizando] = useState(false);
  const [aviso, setAviso] = useState<string | null>(null);
  const [filtros, setFiltros] = useState({ sitio: "", q: "", favoritas: false });
  const [lista, setLista] = useState<KbImagen[] | null>(null);
  const [cargando, setCargando] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!estado) return null;
  const webs = datos.sitios.filter((s) => s.tipo === "web");

  async function cargar() {
    setCargando(true);
    setError(null);
    try {
      const r = await getKbImagenes({ sitio: filtros.sitio || undefined,
        q: filtros.q.trim() || undefined, favoritas: filtros.favoritas || undefined });
      setLista(r.imagenes);
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudieron cargar las imágenes."));
    } finally {
      setCargando(false);
    }
  }

  async function sincronizar() {
    setSincronizando(true);
    setAviso(null);
    try {
      const r = await sincronizarKbImagenes();
      onDatos(conMedios(datos, { imagenes: r.estado }));
      setAviso(resumenPasada(r.sincronizacion, "imágenes indexadas"));
      if (lista) await cargar();
    } catch (e) {
      setAviso(extractErrorMessage(e, "No se pudo sincronizar."));
    } finally {
      setSincronizando(false);
    }
  }

  async function marcar(img: KbImagen, cambios: { favorita?: boolean; oculta?: boolean }) {
    setError(null);
    try {
      const r = await updateKbImagen(img.id, cambios);
      setLista((l) => (l ?? []).map((x) => (x.id === img.id ? r.imagen : x)));
      onDatos(conMedios(datos, { imagenes: r.estado }));
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo guardar."));
    }
  }

  return (
    <section className="kb-bloque" aria-labelledby="kb-imagenes-t">
      <h3 id="kb-imagenes-t">Imágenes de las webs (hero y muestras)</h3>
      <p className="muted small">
        La biblioteca de medios de cada web, solo lo que vale para la cabecera de un correo o una
        galería de trabajos: imágenes de 1.200 px de ancho o más, apaisadas, en JPEG, PNG o GIF
        (Outlook no enseña WebP), sin logos, iconos ni códigos de artículo, y no adjuntas a un
        producto de la tienda (esas ya están en el catálogo). Las webs no tienen texto
        alternativo: se busca por el título. Las favoritas se le ofrecen a la IA primero; las
        ocultas, nunca.
      </p>
      <div className="kb-fuentes">
        <div className="kb-fuente" aria-label="Estado del índice de imágenes">
          <div>
            <strong>{estado.total}</strong> imágenes
            {Object.keys(estado.por_sitio).length > 0 ? (
              <span className="muted small"> · {Object.entries(estado.por_sitio).map(([s, n]) => `${s} ${n}`).join(" · ")}</span>
            ) : null}
          </div>
          <div className="muted small">{estado.favoritas} favoritas · {estado.ocultas} ocultas</div>
          <div className="muted small">Última sincronización: {ultima(estado.ultima_sincronizacion)}</div>
          <div className="kb-acciones">
            <button type="button" className="button small" onClick={sincronizar} disabled={!canEdit || sincronizando}>
              {sincronizando ? "Sincronizando…" : "Sincronizar imágenes"}
            </button>
          </div>
        </div>
      </div>
      {aviso ? <p className="muted small" role="status">{aviso}</p> : null}
      <div className="kb-alta">
        <label className="field"><span>Web</span>
          <select aria-label="Web de las imágenes" value={filtros.sitio}
            onChange={(e) => setFiltros({ ...filtros, sitio: e.target.value })}>
            <option value="">todas</option>
            {webs.map((w) => <option key={w.clave} value={w.clave}>{w.host}</option>)}
          </select>
        </label>
        <label className="field kb-campo-url"><span>Buscar por el título</span>
          <input type="text" aria-label="Buscar imágenes" maxLength={120} value={filtros.q}
            onChange={(e) => setFiltros({ ...filtros, q: e.target.value })} />
        </label>
        <label className="field kb-check">
          <input type="checkbox" aria-label="Solo favoritas" checked={filtros.favoritas}
            onChange={(e) => setFiltros({ ...filtros, favoritas: e.target.checked })} />
          <span>Solo favoritas</span>
        </label>
        <button type="button" className="button small" onClick={cargar} disabled={cargando}>
          {cargando ? "Cargando…" : (lista ? "Volver a cargar" : "Ver las imágenes")}
        </button>
      </div>
      {error ? <p className="form-error" role="alert">{error}</p> : null}
      {lista ? (
        lista.length === 0 ? <p className="muted small" role="status">Ninguna imagen con esos filtros.</p> : (
          <ul className="kb-imagenes" aria-label="Imágenes indexadas">
            {lista.map((img) => (
              <li key={img.id} className={img.oculta ? "is-inactivo" : undefined}>
                <a href={img.pagina ?? img.url} target="_blank" rel="noreferrer">
                  {/* eslint-disable-next-line @next/next/no-img-element -- imagen externa de las webs */}
                  <img src={img.miniatura} alt={img.titulo} loading="lazy" />
                </a>
                <span className="kb-imagen-titulo">{img.titulo || "sin título"}</span>
                <span className="muted small">{img.sitio} · {img.ancho}×{img.alto}</span>
                <span className="kb-acciones">
                  <button type="button" className={img.favorita ? "button small" : "button small secondary"}
                    aria-pressed={img.favorita} disabled={!canEdit}
                    onClick={() => marcar(img, { favorita: !img.favorita })}>
                    {img.favorita ? "★ Favorita" : "☆ Favorita"}
                  </button>
                  <button type="button" className="button small ghost" aria-pressed={img.oculta}
                    disabled={!canEdit} onClick={() => marcar(img, { oculta: !img.oculta })}>
                    {img.oculta ? "Mostrar" : "Ocultar"}
                  </button>
                </span>
              </li>
            ))}
          </ul>
        )
      ) : null}
    </section>
  );
}

// --- vídeos --------------------------------------------------------------------------

type Maquina = { origen_id: string; nombre: string; marca: string };

/** La tabla de vídeos: los de los canales de YouTube del grupo, los enlazados
 *  en las webs y los pegados a mano. El idioma, el tema (el mismo vídeo en
 *  varios idiomas), la marca y las máquinas se deducen y se corrigen aquí; lo
 *  corregido no lo pisa la sincronización. */
export function VideosBloque({ datos, canEdit, onDatos }: Props) {
  const medios = datos.medios;
  const [sincronizando, setSincronizando] = useState(false);
  const [aviso, setAviso] = useState<string | null>(null);
  const [filtros, setFiltros] = useState({ q: "", idioma: "", tema: "", cortos: false });
  const [lista, setLista] = useState<KbVideo[] | null>(null);
  const [cargando, setCargando] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [url, setUrl] = useState("");
  const [anadiendo, setAnadiendo] = useState(false);
  const [maquinas, setMaquinas] = useState<Maquina[] | null>(null);
  if (!medios) return null;
  const estado = medios.videos;
  const temas = Array.from(new Set((lista ?? []).map((v) => v.tema).filter((t): t is string => Boolean(t)))).sort();

  async function cargarMaquinas() {
    if (maquinas) return;
    try {
      const r = await getCatalogoProductos({ fuente: "compositor", tipo: "maquina" });
      const vistas = new Map<string, Maquina>();
      for (const p of r.productos) {
        if (!vistas.has(p.origen_id)) vistas.set(p.origen_id, { origen_id: p.origen_id, nombre: p.nombre, marca: p.marca });
      }
      setMaquinas(Array.from(vistas.values()).sort((a, b) => a.nombre.localeCompare(b.nombre)));
    } catch {
      setMaquinas([]);
    }
  }

  async function cargar() {
    setCargando(true);
    setError(null);
    try {
      const r = await getKbVideos({ q: filtros.q.trim() || undefined, idioma: filtros.idioma || undefined,
        tema: filtros.tema || undefined, con_cortos: filtros.cortos });
      setLista(r.videos);
      void cargarMaquinas();
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudieron cargar los vídeos."));
    } finally {
      setCargando(false);
    }
  }

  async function sincronizar() {
    setSincronizando(true);
    setAviso(null);
    try {
      const r = await sincronizarKbVideos();
      onDatos(conMedios(datos, { videos: r.estado }));
      setAviso(resumenPasada(r.sincronizacion, "vídeos"));
      if (lista) await cargar();
    } catch (e) {
      setAviso(extractErrorMessage(e, "No se pudo sincronizar."));
    } finally {
      setSincronizando(false);
    }
  }

  async function anadir() {
    if (!url.trim()) return;
    setAnadiendo(true);
    setError(null);
    try {
      const r = await createKbVideo(url.trim());
      setLista((l) => [r.video, ...(l ?? []).filter((v) => v.id !== r.video.id)]);
      onDatos(conMedios(datos, { videos: r.estado }));
      setUrl("");
      void cargarMaquinas();
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo añadir el vídeo."));
    } finally {
      setAnadiendo(false);
    }
  }

  async function guardar(video: KbVideo, cambios: KbVideoCambios): Promise<boolean> {
    setError(null);
    try {
      const r = await updateKbVideo(video.id, cambios);
      setLista((l) => (l ?? []).map((v) => (v.id === video.id ? r.video : v)));
      onDatos(conMedios(datos, { videos: r.estado }));
      return true;
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo guardar el vídeo."));
      return false;
    }
  }

  return (
    <section className="kb-bloque" aria-labelledby="kb-videos-t">
      <h3 id="kb-videos-t">Vídeos (YouTube)</h3>
      <p className="muted small">
        Los vídeos que un correo compuesto puede llevar: los de los canales{" "}
        {medios.canales.map((c) => c.handle).join(", ") || "—"}, los enlazados en las webs y los
        que se peguen aquí. Del mismo vídeo en varios idiomas (su <em>tema</em>) sale la versión
        del idioma del cliente, o la inglesa si no la hay. La miniatura es la de 1280×720 si el
        vídeo se subió en HD; si no, la de 320×180, y se vuelve a probar en cada sincronización.
        Los «shorts» no se le ofrecen a la IA.
      </p>
      {!estado.clave_configurada ? (
        <div className="kb-estado is-error">
          Sin clave de YouTube en el servidor (YOUTUBE_API_KEY en .env.production): los canales no se
          pueden listar. Los vídeos pegados a mano entran igual, sin título ni duración.
        </div>
      ) : null}
      <div className="kb-fuentes">
        <div className="kb-fuente" aria-label="Estado de la tabla de vídeos">
          <div>
            <strong>{estado.total}</strong> vídeos
            {Object.keys(estado.por_idioma).length > 0 ? (
              <span className="muted small"> · {Object.entries(estado.por_idioma).map(([i, n]) => `${NOMBRE_IDIOMA[i] ?? i} ${n}`).join(" · ")}</span>
            ) : null}
          </div>
          <div className="muted small">{estado.temas} temas · {estado.cortos} shorts · {estado.ocultos} ocultos</div>
          <div className="muted small">Última sincronización: {ultima(estado.ultima_sincronizacion)}</div>
          <div className="kb-acciones">
            <button type="button" className="button small" onClick={sincronizar} disabled={!canEdit || sincronizando}>
              {sincronizando ? "Sincronizando…" : "Sincronizar vídeos"}
            </button>
          </div>
        </div>
      </div>
      {aviso ? <p className="muted small" role="status">{aviso}</p> : null}
      <div className="kb-alta">
        <label className="field kb-campo-url"><span>Añadir un vídeo de YouTube (URL)</span>
          <input type="text" inputMode="url" aria-label="URL del vídeo" maxLength={500} value={url}
            placeholder="https://www.youtube.com/watch?v=…" onChange={(e) => setUrl(e.target.value)} />
        </label>
        <button type="button" className="button small" onClick={anadir} disabled={!canEdit || anadiendo || !url.trim()}>
          {anadiendo ? "Añadiendo…" : "Añadir"}
        </button>
      </div>
      <div className="kb-alta">
        <label className="field kb-campo-url"><span>Buscar (título, tema)</span>
          <input type="text" aria-label="Buscar vídeos" maxLength={120} value={filtros.q}
            onChange={(e) => setFiltros({ ...filtros, q: e.target.value })} />
        </label>
        <label className="field"><span>Idioma</span>
          <select aria-label="Idioma de los vídeos" value={filtros.idioma}
            onChange={(e) => setFiltros({ ...filtros, idioma: e.target.value })}>
            <option value="">todos</option>
            {IDIOMAS.map((i) => <option key={i} value={i}>{NOMBRE_IDIOMA[i]}</option>)}
          </select>
        </label>
        {temas.length > 0 ? (
          <label className="field"><span>Tema</span>
            <select aria-label="Tema de los vídeos" value={filtros.tema}
              onChange={(e) => setFiltros({ ...filtros, tema: e.target.value })}>
              <option value="">todos</option>
              {temas.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </label>
        ) : null}
        <label className="field kb-check">
          <input type="checkbox" aria-label="Ver los shorts" checked={filtros.cortos}
            onChange={(e) => setFiltros({ ...filtros, cortos: e.target.checked })} />
          <span>Ver los shorts</span>
        </label>
        <button type="button" className="button small" onClick={cargar} disabled={cargando}>
          {cargando ? "Cargando…" : (lista ? "Volver a cargar" : "Ver los vídeos")}
        </button>
      </div>
      {error ? <p className="form-error" role="alert">{error}</p> : null}
      {lista ? (
        lista.length === 0 ? <p className="muted small" role="status">Ningún vídeo con esos filtros.</p> : (
          <div className="kb-tabla-scroll">
            <table className="data-table erp-settings-table kb-tabla" aria-label="Vídeos">
              <thead>
                <tr><th>Vídeo</th><th>Idioma</th><th>Tema</th><th>Marca y máquinas</th><th>Visible</th></tr>
              </thead>
              <tbody>
                {lista.map((v) => (
                  // La clave cambia con lo guardado: la fila vuelve a leer tema y marca.
                  <FilaVideo key={`${v.id}:${v.tema ?? ""}:${v.marca}`} video={v} canEdit={canEdit}
                    maquinas={maquinas} onGuardar={guardar} />
                ))}
              </tbody>
            </table>
          </div>
        )
      ) : null}
    </section>
  );
}

function FilaVideo({
  video, canEdit, maquinas, onGuardar,
}: {
  video: KbVideo;
  canEdit: boolean;
  maquinas: Maquina[] | null;
  onGuardar: (v: KbVideo, cambios: KbVideoCambios) => Promise<boolean>;
}) {
  const [tema, setTema] = useState(video.tema ?? "");
  const [marca, setMarca] = useState(video.marca);
  const [ocupada, setOcupada] = useState(false);
  const manual = new Set(video.manual);

  async function guardar(cambios: KbVideoCambios) {
    setOcupada(true);
    await onGuardar(video, cambios);
    setOcupada(false);
  }

  const productos = video.productos;
  const sinPoner = (maquinas ?? []).filter((m) => !productos.some((p) => p.origen_id === m.origen_id));

  return (
    <tr className={video.visible ? undefined : "is-inactivo"}>
      <td>
        <div className="kb-video">
          {video.miniatura ? (
            <a href={video.url} target="_blank" rel="noreferrer">
              {/* eslint-disable-next-line @next/next/no-img-element -- miniatura de YouTube */}
              <img src={video.miniatura} alt={video.titulo || video.video_id} loading="lazy" />
            </a>
          ) : null}
          <div>
            <a href={video.url} target="_blank" rel="noreferrer"><strong>{video.titulo || video.video_id}</strong></a>
            <div className="muted small">
              {video.canal ?? "—"} · {ORIGEN[video.origen] ?? video.origen}{video.webs.length > 0 ? ` (${video.webs.join(", ")})` : ""}
              {" · "}{duracion(video.duracion)}
              {" · "}{video.miniatura_calidad === "maxresdefault" ? "miniatura HD" : "miniatura 320 px"}
              {video.corto ? " · short" : ""}
            </div>
          </div>
        </div>
      </td>
      <td>
        <select aria-label={`Idioma de ${video.video_id}`} value={video.idioma ?? ""} disabled={!canEdit || ocupada}
          onChange={(e) => guardar({ idioma: e.target.value })}>
          <option value="">—</option>
          {IDIOMAS.map((i) => <option key={i} value={i}>{NOMBRE_IDIOMA[i]}</option>)}
        </select>
        <div className="muted small">{video.idioma_fuente ? FUENTE_IDIOMA[video.idioma_fuente] ?? video.idioma_fuente : ""}</div>
        {manual.has("idioma") ? (
          <button type="button" className="link-button small" disabled={!canEdit || ocupada}
            onClick={() => guardar({ automatico: ["idioma"] })}>↺ automático</button>
        ) : null}
      </td>
      <td>
        <span className="kb-inline">
          <input type="text" className="kb-tema" aria-label={`Tema de ${video.video_id}`} maxLength={80} value={tema}
            disabled={!canEdit || ocupada} onChange={(e) => setTema(e.target.value)} />
          <button type="button" className="button small" disabled={!canEdit || ocupada || tema === (video.tema ?? "")}
            onClick={() => guardar({ tema })}>Guardar</button>
        </span>
        {manual.has("tema") ? (
          <button type="button" className="link-button small" disabled={!canEdit || ocupada}
            onClick={() => guardar({ automatico: ["tema"] })}>↺ automático</button>
        ) : null}
      </td>
      <td>
        <span className="kb-inline">
          <input type="text" aria-label={`Marca de ${video.video_id}`} maxLength={80} value={marca}
            disabled={!canEdit || ocupada} onChange={(e) => setMarca(e.target.value)} />
          <button type="button" className="button small" disabled={!canEdit || ocupada || marca === video.marca}
            onClick={() => guardar({ marca })}>Guardar</button>
        </span>
        <span className="kb-coincide">
          {productos.length === 0 ? <span className="muted small">sin máquinas</span> : null}
          {productos.map((p) => (
            <span key={p.origen_id}>
              {p.nombre}
              {canEdit ? (
                <button type="button" className="link-button small" aria-label={`Quitar ${p.nombre}`} disabled={ocupada}
                  onClick={() => guardar({ productos: productos.filter((x) => x.origen_id !== p.origen_id).map((x) => ({ origen_id: x.origen_id })) })}>×</button>
              ) : null}
            </span>
          ))}
        </span>
        {canEdit && sinPoner.length > 0 ? (
          <select aria-label={`Añadir máquina a ${video.video_id}`} value="" disabled={ocupada}
            onChange={(e) => e.target.value && guardar({ productos: [...productos.map((x) => ({ origen_id: x.origen_id })), { origen_id: e.target.value }] })}>
            <option value="">+ máquina…</option>
            {sinPoner.map((m) => <option key={m.origen_id} value={m.origen_id}>{m.nombre} ({m.marca})</option>)}
          </select>
        ) : null}
        {manual.has("productos") || manual.has("marca") ? (
          <button type="button" className="link-button small" disabled={!canEdit || ocupada}
            onClick={() => guardar({ automatico: ["marca", "productos"] })}>↺ automático</button>
        ) : null}
      </td>
      <td>
        <input type="checkbox" aria-label={`Visible ${video.video_id}`} checked={video.visible}
          disabled={!canEdit || ocupada} onChange={(e) => guardar({ visible: e.target.checked })} />
      </td>
    </tr>
  );
}
