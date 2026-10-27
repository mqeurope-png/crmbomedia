"use client";

import { useEffect, useState } from "react";
import { extractErrorMessage } from "../../lib/errors";
import {
  actualizarFuenteSoporte,
  comprobarFuenteSoporte,
  crearFuenteSoporte,
  listarFuentesSoporte,
  type SoporteFuente,
  type SoporteFuenteIn,
} from "../../lib/soporteApi";

/** Configuración ERP → Base de conocimiento → «Soporte de terceros (lista
 *  blanca)»: los centros de soporte de los fabricantes en los que se puede
 *  buscar un procedimiento (el redactor y la Cola SAT). Solo se descarga de
 *  estos dominios; «Comprobar» dice si de verdad sirven contenido o si la
 *  página se pinta con JavaScript y lo que llega es una cáscara vacía. Cada
 *  cambio se guarda al momento por su API (`/api/erp/soporte/fuentes`). */

type Tipo = SoporteFuente["tipo"];

const TIPOS: { id: Tipo; label: string }[] = [
  { id: "zendesk", label: "Zendesk (Help Center: busca por su API pública)" },
  { id: "wordpress", label: "WordPress (busca por su REST pública)" },
  { id: "html", label: "HTML (sin búsqueda: solo se leen sus artículos)" },
];

const TIPO_CORTO: Record<Tipo, string> = { zendesk: "Zendesk", wordpress: "WordPress", html: "HTML" };

function fechaCorta(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("es-ES", { day: "2-digit", month: "2-digit", year: "numeric",
    hour: "2-digit", minute: "2-digit" });
}

function esWeb(url: string | null | undefined): url is string {
  return typeof url === "string" && /^https?:\/\//i.test(url);
}

/** Lo que ha dicho la última comprobación, para la tabla. */
export function estadoFuente(f: Pick<SoporteFuente, "comprobado_en" | "comprobado_estado">):
  { texto: string; clase: string } {
  if (!f.comprobado_en || !f.comprobado_estado) return { texto: "sin comprobar", clase: "" };
  const cuando = fechaCorta(f.comprobado_en);
  if (f.comprobado_estado === "ok") return { texto: `sirve contenido · ${cuando}`, clase: "is-ok" };
  if (f.comprobado_estado === "vacio") return { texto: `cáscara vacía · ${cuando}`, clase: "is-error" };
  return { texto: `error · ${cuando}`, clase: "is-error" };
}

/** El resumen que se enseña al pulsar «Comprobar». */
function resumenComprobacion(f: SoporteFuente): string {
  const detalle = f.comprobado_detalle ? ` (${f.comprobado_detalle})` : "";
  if (f.comprobado_estado === "ok") return `${f.nombre}: sirve contenido, se puede buscar y leer${detalle}.`;
  if (f.comprobado_estado === "vacio") {
    return `${f.nombre}: cáscara vacía. La página se pinta con JavaScript (o no devuelve texto) y así no `
      + `sirve: no encontraría nada y el redactor diría que no hay procedimiento${detalle}.`;
  }
  return `${f.nombre}: no responde bien${detalle}.`;
}

/** «es=es-419, en=en-us» ⇄ `{es: "es-419", en: "en-us"}`. */
export function leerLocales(texto: string): { locales: Record<string, string>; malos: string[] } {
  const locales: Record<string, string> = {};
  const malos: string[] = [];
  for (const parte of texto.split(/[,;\s]+/).filter(Boolean)) {
    const m = parte.match(/^([a-z]{2})=([a-z0-9_-]{2,20})$/i);
    if (m) locales[m[1].toLowerCase()] = m[2];
    else malos.push(parte);
  }
  return { locales, malos };
}

export function escribirLocales(locales: Record<string, string>): string {
  return Object.entries(locales).map(([i, l]) => `${i}=${l}`).join(", ");
}

type Borrador = {
  clave: string;
  nombre: string;
  dominio: string;
  tipo: Tipo;
  base_url: string;
  marca: string;
  locales: string;
  activo: boolean;
  notas: string;
};

/** Una fuente nueva arranca DESACTIVADA: se comprueba antes de activarla. */
const NUEVA: Borrador = {
  clave: "", nombre: "", dominio: "", tipo: "zendesk", base_url: "", marca: "", locales: "",
  activo: false, notas: "",
};

function borradorDe(f: SoporteFuente): Borrador {
  return {
    clave: f.clave, nombre: f.nombre, dominio: f.dominio, tipo: f.tipo, base_url: f.base_url,
    marca: f.marca ?? "", locales: escribirLocales(f.locales), activo: f.activo, notas: f.notas ?? "",
  };
}

export function SoporteFuentesBloque({ canEdit }: { canEdit: boolean }) {
  const [fuentes, setFuentes] = useState<SoporteFuente[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editando, setEditando] = useState<string | null>(null);
  const [comprobando, setComprobando] = useState<string | null>(null);
  const [aviso, setAviso] = useState<string | null>(null);
  // Se vuelve a montar el formulario de alta (vacío) tras cada alta buena.
  const [altas, setAltas] = useState(0);

  useEffect(() => {
    let vivo = true;
    listarFuentesSoporte()
      .then((l) => { if (vivo) { setFuentes(l); setError(null); } })
      .catch((e) => {
        if (vivo) setError(extractErrorMessage(e, "No se pudo cargar la lista de centros de soporte."));
      });
    return () => { vivo = false; };
  }, []);

  function sustituir(f: SoporteFuente) {
    setFuentes((l) => (l ?? []).map((x) => (x.clave === f.clave ? f : x)));
  }

  async function comprobar(f: SoporteFuente) {
    setComprobando(f.clave);
    setAviso(null);
    try {
      const nueva = await comprobarFuenteSoporte(f.clave);
      sustituir(nueva);
      setAviso(resumenComprobacion(nueva));
    } catch (e) {
      setAviso(extractErrorMessage(e, `No se pudo comprobar ${f.nombre}.`));
    } finally {
      setComprobando(null);
    }
  }

  async function crear(b: Borrador): Promise<string | null> {
    const { locales, malos } = leerLocales(b.locales);
    if (malos.length > 0) return `«${malos.join(" ")}» no es idioma=locale (p. ej. es=es-419, en=en-us).`;
    const payload: SoporteFuenteIn = {
      clave: b.clave.trim().toLowerCase(), nombre: b.nombre.trim(), tipo: b.tipo, locales,
      activo: b.activo, marca: b.marca.trim() || null, notas: b.notas.trim() || null,
      ...(b.dominio.trim() ? { dominio: b.dominio.trim() } : {}),
      ...(b.base_url.trim() ? { base_url: b.base_url.trim() } : {}),
    };
    try {
      const nueva = await crearFuenteSoporte(payload);
      setFuentes((l) => [...(l ?? []), nueva]);
      setAltas((n) => n + 1);
      setAviso(`${nueva.nombre} añadida a la lista${nueva.activo ? "" : " (desactivada: «Comprobar» y actívala si sirve contenido)"}.`);
      return null;
    } catch (e) {
      return extractErrorMessage(e, "No se pudo añadir la fuente.");
    }
  }

  async function guardar(clave: string, b: Borrador, antes: Borrador): Promise<string | null> {
    const { locales, malos } = leerLocales(b.locales);
    if (malos.length > 0) return `«${malos.join(" ")}» no es idioma=locale (p. ej. es=es-419, en=en-us).`;
    // Solo lo que cambia: tocar el dominio, el tipo, la dirección o los idiomas
    // borra la comprobación (ya no diría la verdad); el nombre o la nota, no.
    // `antes` es lo que tenía el formulario al abrirlo: si mientras tanto se ha
    // (des)activado desde la fila, un «activo» que no se ha tocado aquí no lo deshace.
    const cambios: SoporteFuenteIn = {};
    if (b.nombre.trim() !== antes.nombre) cambios.nombre = b.nombre.trim();
    if (b.dominio.trim() !== antes.dominio) cambios.dominio = b.dominio.trim();
    if (b.tipo !== antes.tipo) cambios.tipo = b.tipo;
    if (b.base_url.trim() !== antes.base_url) cambios.base_url = b.base_url.trim();
    if (b.marca.trim() !== antes.marca) cambios.marca = b.marca.trim() || null;
    if (escribirLocales(locales) !== antes.locales) cambios.locales = locales;
    if (b.activo !== antes.activo) cambios.activo = b.activo;
    if (b.notas.trim() !== antes.notas) cambios.notas = b.notas.trim() || null;
    if (Object.keys(cambios).length === 0) {
      setEditando(null);
      return null;
    }
    try {
      sustituir(await actualizarFuenteSoporte(clave, cambios));
      setEditando(null);
      return null;
    } catch (e) {
      return extractErrorMessage(e, "No se pudo guardar la fuente.");
    }
  }

  const enEdicion = fuentes?.find((f) => f.clave === editando) ?? null;
  // Activas cuya última comprobación no sirvió: ahí no se encuentra nada.
  const malas = (fuentes ?? []).filter((f) => f.activo
    && (f.comprobado_estado === "vacio" || f.comprobado_estado === "error"));

  return (
    <section className="kb-bloque" aria-labelledby="kb-soporte-fuentes-t">
      <h3 id="kb-soporte-fuentes-t">Soporte de terceros (lista blanca)</h3>
      <p className="muted small">
        Los centros de soporte de los fabricantes en los que se busca un procedimiento: «Buscar en
        soporte» del redactor y «Consulta de soporte» de la Cola SAT. <strong>Solo se descarga de
        estos dominios</strong>, nunca de una dirección que traiga el cliente, con tiempo y tamaño
        máximos y una caché que caduca. Antes de activar una fuente, «Comprobar»: si la página se
        pinta con JavaScript, lo que llega es una cáscara vacía y no sirve (no encontraría nada y el
        borrador diría que no hay procedimiento cuando sí lo hay).
      </p>
      {error ? <p className="form-error" role="alert">{error}</p> : null}
      {!error && fuentes === null ? <p className="muted small">Cargando la lista blanca…</p> : null}
      {malas.length > 0 ? (
        <div className="kb-aviso" role="note">
          <p>
            <strong>Activa{malas.length === 1 ? "" : "s"} sin servir contenido:</strong>{" "}
            {malas.map((f) => `${f.nombre} (${f.comprobado_estado === "vacio" ? "cáscara vacía" : "error"})`).join(", ")}.
            {" "}En ella{malas.length === 1 ? "" : "s"} no se encuentra nada: corrige la dirección o desactívala.
          </p>
        </div>
      ) : null}
      {fuentes && fuentes.length === 0 ? (
        <p className="muted small">La lista está vacía: no se puede buscar en ningún soporte.</p>
      ) : null}
      {fuentes && fuentes.length > 0 ? (
        <table className="data-table data-table--responsive erp-settings-table kb-tabla soporte-fuentes-tabla"
          aria-label="Centros de soporte de la lista blanca">
          <thead>
            <tr>
              <th>Fuente</th><th>Dominio</th><th>Tipo</th><th>Marca</th><th>Idiomas</th>
              <th>Activa</th><th>Última comprobación</th><th></th>
            </tr>
          </thead>
          <tbody>
            {fuentes.map((f) => (
              <FilaFuente key={f.clave} fuente={f} canEdit={canEdit} onCambio={sustituir}
                comprobando={comprobando === f.clave} ocupado={comprobando !== null}
                onComprobar={() => void comprobar(f)} onEditar={() => setEditando(f.clave)} />
            ))}
          </tbody>
        </table>
      ) : null}
      {aviso ? <p className="muted small" role="status">{aviso}</p> : null}
      {canEdit && fuentes !== null ? (
        enEdicion ? (
          <FuenteFormulario key={`editar-${enEdicion.clave}`} modo="edicion" inicial={borradorDe(enEdicion)}
            onGuardar={(b, antes) => guardar(enEdicion.clave, b, antes)} onCancelar={() => setEditando(null)} />
        ) : (
          <FuenteFormulario key={`alta-${altas}`} modo="alta" inicial={NUEVA} onGuardar={crear} />
        )
      ) : null}
    </section>
  );
}

function FilaFuente({
  fuente, canEdit, onCambio, comprobando, ocupado, onComprobar, onEditar,
}: {
  fuente: SoporteFuente; canEdit: boolean; onCambio: (f: SoporteFuente) => void;
  comprobando: boolean; ocupado: boolean; onComprobar: () => void; onEditar: () => void;
}) {
  const [guardando, setGuardando] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const estado = estadoFuente(fuente);
  const idiomas = Object.entries(fuente.locales);

  async function activar(activo: boolean) {
    setGuardando(true);
    setError(null);
    try {
      onCambio(await actualizarFuenteSoporte(fuente.clave, { activo }));
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo guardar la fuente."));
    } finally {
      setGuardando(false);
    }
  }

  return (
    <tr className={fuente.activo ? undefined : "is-inactivo"}>
      <td data-label="Fuente">
        <strong>{fuente.nombre}</strong><br /><code>{fuente.clave}</code>
        {fuente.notas ? <><br /><span className="muted small">{fuente.notas}</span></> : null}
      </td>
      <td data-label="Dominio">
        {fuente.dominio}
        {esWeb(fuente.base_url) ? (
          <><br /><a className="small" href={fuente.base_url} target="_blank" rel="noreferrer">{fuente.base_url}</a></>
        ) : null}
      </td>
      <td data-label="Tipo">{TIPO_CORTO[fuente.tipo] ?? fuente.tipo}</td>
      <td data-label="Marca">{fuente.marca ?? "—"}</td>
      <td data-label="Idiomas">
        {idiomas.length > 0
          ? idiomas.map(([i, l]) => <span key={i} className="soporte-fuente-locale">{i} → {l}</span>)
          : <span className="muted small">el suyo por defecto</span>}
      </td>
      <td data-label="Activa">
        <input type="checkbox" aria-label={`Activa ${fuente.nombre}`} checked={fuente.activo}
          disabled={!canEdit || guardando} onChange={(e) => void activar(e.target.checked)} />
      </td>
      <td data-label="Última comprobación">
        <span className={`kb-estado ${estado.clase}`}>{estado.texto}</span>
        {fuente.comprobado_detalle ? (
          <><br /><span className="muted small soporte-fuente-detalle">{fuente.comprobado_detalle}</span></>
        ) : null}
      </td>
      <td>
        <span className="kb-acciones">
          <button type="button" className="button small secondary" aria-label={`Comprobar ${fuente.nombre}`}
            disabled={!canEdit || ocupado} onClick={onComprobar}>
            {comprobando ? "Comprobando…" : "Comprobar"}
          </button>
          {canEdit ? (
            <button type="button" className="button small ghost" aria-label={`Editar ${fuente.nombre}`}
              onClick={onEditar}>Editar</button>
          ) : null}
        </span>
        {error ? <><br /><span className="form-error small" role="alert">{error}</span></> : null}
      </td>
    </tr>
  );
}

function FuenteFormulario({
  modo, inicial, onGuardar, onCancelar,
}: {
  modo: "alta" | "edicion";
  inicial: Borrador;
  /** Devuelve el error que enseñar, o `null` si ha ido bien. `antes`: con lo
   *  que se abrió el formulario (para mandar solo lo que se ha cambiado en él). */
  onGuardar: (b: Borrador, antes: Borrador) => Promise<string | null>;
  onCancelar?: () => void;
}) {
  const [antes] = useState<Borrador>(inicial);
  const [b, setB] = useState<Borrador>(inicial);
  const [ocupado, setOcupado] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const alta = modo === "alta";
  const de = alta ? "(fuente nueva)" : `(${inicial.clave})`;
  const listo = Boolean(b.nombre.trim()) && (alta ? Boolean(b.clave.trim()) : true)
    && Boolean(b.dominio.trim() || b.base_url.trim());

  async function guardar() {
    setOcupado(true);
    setError(null);
    const fallo = await onGuardar(b, antes);
    // Si ha ido bien, el formulario se desmonta (alta nueva o fin de la edición).
    if (fallo) {
      setError(fallo);
      setOcupado(false);
    }
  }

  return (
    <div className="kb-alta soporte-fuente-form" role="group"
      aria-label={alta ? "Añadir un centro de soporte" : `Editar ${inicial.nombre}`}>
      {!alta ? <h4 className="kb-subtitulo soporte-fuente-form-titulo">Editar {inicial.nombre}</h4> : null}
      <label className="field"><span>Clave</span>
        <input type="text" aria-label={`Clave ${de}`} placeholder="p. ej. artisjet" maxLength={40}
          value={b.clave} disabled={!alta} onChange={(e) => setB({ ...b, clave: e.target.value })} />
      </label>
      <label className="field"><span>Nombre</span>
        <input type="text" aria-label={`Nombre ${de}`} placeholder="p. ej. Soporte de artisJet" maxLength={120}
          value={b.nombre} onChange={(e) => setB({ ...b, nombre: e.target.value })} />
      </label>
      <label className="field"><span>Dominio</span>
        <input type="text" aria-label={`Dominio ${de}`} placeholder="support.flux3dp.com" maxLength={255}
          value={b.dominio} onChange={(e) => setB({ ...b, dominio: e.target.value })} />
      </label>
      <label className="field"><span>Tipo</span>
        <select aria-label={`Tipo ${de}`} value={b.tipo} onChange={(e) => setB({ ...b, tipo: e.target.value as Tipo })}>
          {TIPOS.map((t) => <option key={t.id} value={t.id}>{t.label}</option>)}
        </select>
      </label>
      <label className="field kb-campo-url"><span>Dirección base (https://, en ese dominio)</span>
        <input type="url" aria-label={`Dirección base ${de}`} placeholder="https://support.flux3dp.com"
          maxLength={500} value={b.base_url} onChange={(e) => setB({ ...b, base_url: e.target.value })} />
      </label>
      <label className="field"><span>Marca</span>
        <input type="text" aria-label={`Marca ${de}`} placeholder="p. ej. FLUX" maxLength={40}
          value={b.marca} onChange={(e) => setB({ ...b, marca: e.target.value })} />
      </label>
      <label className="field kb-campo-url"><span>Idiomas (idioma=locale, separados por comas)</span>
        <input type="text" aria-label={`Idiomas ${de}`} placeholder="es=es-419, en=en-us, de=de"
          value={b.locales} onChange={(e) => setB({ ...b, locales: e.target.value })} />
      </label>
      <label className="field kb-campo-url"><span>Notas</span>
        <input type="text" aria-label={`Notas ${de}`} maxLength={2000}
          value={b.notas} onChange={(e) => setB({ ...b, notas: e.target.value })} />
      </label>
      <label className="field kb-check">
        <input type="checkbox" aria-label={`Activa ${de}`} checked={b.activo}
          onChange={(e) => setB({ ...b, activo: e.target.checked })} />
        <span>{alta ? "Activa (mejor tras «Comprobar»)" : "Activa"}</span>
      </label>
      <button type="button" className="button small" disabled={ocupado || !listo} onClick={() => void guardar()}>
        {ocupado ? "Guardando…" : (alta ? "Añadir fuente" : "Guardar cambios de la fuente")}
      </button>
      {onCancelar ? (
        <button type="button" className="button small secondary" disabled={ocupado} onClick={onCancelar}>
          Cancelar
        </button>
      ) : null}
      {error ? <p className="form-error" role="alert">{error}</p> : null}
    </div>
  );
}
