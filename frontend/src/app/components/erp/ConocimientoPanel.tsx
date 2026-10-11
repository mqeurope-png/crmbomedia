"use client";

import { useEffect, useState } from "react";
import {
  comprobarKbRutas,
  createFluxCategoria,
  createKbRuta,
  createSitioWeb,
  getConocimiento,
  resolverConocimiento,
  sincronizarFlux,
  updateFluxCategoria,
  updateKbRuta,
  updateSitioWeb,
  type Conocimiento,
  type FluxCategoria,
  type KbResolucion,
  type KbRuta,
  type SitioWeb,
} from "../../lib/erpApi";
import { extractErrorMessage } from "../../lib/errors";

type Props = {
  canEdit: boolean;
};

const IDIOMAS_TEXTO: Record<string, string> = {
  es: "castellano", en: "inglés", de: "alemán", fr: "francés", nl: "neerlandés", pt: "portugués",
  it: "italiano",
};

function nombreIdioma(codigo: string): string {
  return IDIOMAS_TEXTO[codigo] ?? codigo;
}

function fechaCorta(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("es-ES", { day: "2-digit", month: "2-digit", year: "numeric",
    hour: "2-digit", minute: "2-digit" });
}

/** Configuración ERP → Base de conocimiento: las tablas que alimentan los
 *  enlaces del asistente de respuesta. Cada cambio se guarda al momento por su
 *  API (`/api/erp/conocimiento`), aparte del «Guardar cambios» de la sección
 *  (que lleva solo la configuración: sincronización y palabras de FLUX). */
export function ConocimientoPanel({ canEdit }: Props) {
  const [datos, setDatos] = useState<Conocimiento | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let vivo = true;
    getConocimiento()
      .then((d) => { if (vivo) { setDatos(d); setError(null); } })
      .catch((e) => {
        if (vivo) setError(extractErrorMessage(e, "No se pudo cargar la base de conocimiento."));
      });
    return () => { vivo = false; };
  }, []);

  if (error) return <p className="form-error" role="alert">{error}</p>;
  if (!datos) return <p className="muted small">Cargando la base de conocimiento…</p>;

  return (
    <div className="kb-panel">
      <SitiosTabla datos={datos} canEdit={canEdit} onDatos={setDatos} />
      <RutasTabla datos={datos} tipo="soporte" canEdit={canEdit} onDatos={setDatos} />
      <RutasTabla datos={datos} tipo="producto" canEdit={canEdit} onDatos={setDatos} />
      <FluxBloque datos={datos} canEdit={canEdit} onDatos={setDatos} />
      <Probar datos={datos} />
    </div>
  );
}

// --- sitios ----------------------------------------------------------------------

function SitiosTabla({
  datos, canEdit, onDatos,
}: { datos: Conocimiento; canEdit: boolean; onDatos: (d: Conocimiento) => void }) {
  const [nuevo, setNuevo] = useState({
    clave: "", dominio: "", marca: "", empresa: "", idioma_defecto: "es", idiomas: "es",
    tipo: "web" as "web" | "correo", remitente_defecto: "", soporte_propio: false,
  });
  const [creando, setCreando] = useState(false);
  const [errorAlta, setErrorAlta] = useState<string | null>(null);

  async function crear() {
    setCreando(true);
    setErrorAlta(null);
    try {
      const d = await createSitioWeb({
        clave: nuevo.clave.trim(), dominio: nuevo.dominio.trim(), marca: nuevo.marca.trim(),
        empresa: nuevo.empresa.trim(), tipo: nuevo.tipo, idioma_defecto: nuevo.idioma_defecto,
        idiomas: nuevo.idiomas.split(/[\s,]+/).filter(Boolean),
        remitente_defecto: nuevo.remitente_defecto.trim() || null,
        soporte_propio: nuevo.soporte_propio,
      });
      onDatos(d);
      setNuevo({ clave: "", dominio: "", marca: "", empresa: "", idioma_defecto: "es", idiomas: "es",
        tipo: "web", remitente_defecto: "", soporte_propio: false });
    } catch (e) {
      setErrorAlta(extractErrorMessage(e, "No se pudo añadir el sitio."));
    } finally {
      setCreando(false);
    }
  }

  return (
    <section className="kb-bloque" aria-labelledby="kb-sitios-t">
      <h3 id="kb-sitios-t">Sitios del grupo</h3>
      <p className="muted small">
        Las webs y dominios de correo que son nuestros. Deciden qué correo se captura (cualquier
        dirección de estos dominios), el remitente de los acuses y la respuesta a leads, y en qué
        idiomas se puede enlazar cada web.
        {datos.sitios_de_la_base ? "" : " Ahora mismo se usa la lista de partida: el primer cambio la guarda en la base."}
      </p>
      <table className="data-table data-table--responsive erp-settings-table kb-tabla">
        <thead>
          <tr>
            <th>Clave</th><th>Dominio</th><th>Marca · empresa</th><th>Tipo</th>
            <th>Idiomas</th><th>Plugin</th><th>Soporte propio</th><th>Remitente</th><th>Activo</th>
          </tr>
        </thead>
        <tbody>
          {datos.sitios.map((s) => (
            <FilaSitio key={s.clave} sitio={s} canEdit={canEdit} onDatos={onDatos} />
          ))}
        </tbody>
      </table>
      {canEdit ? (
        <div className="kb-alta">
          <label className="field"><span>Clave</span>
            <input type="text" aria-label="Clave del sitio nuevo" placeholder="p. ej. nuevaweb"
              maxLength={40} value={nuevo.clave}
              onChange={(e) => setNuevo({ ...nuevo, clave: e.target.value })} />
          </label>
          <label className="field"><span>Dominio</span>
            <input type="text" aria-label="Dominio del sitio nuevo" placeholder="nuevaweb.com"
              maxLength={120} value={nuevo.dominio}
              onChange={(e) => setNuevo({ ...nuevo, dominio: e.target.value })} />
          </label>
          <label className="field"><span>Marca</span>
            <input type="text" aria-label="Marca del sitio nuevo" maxLength={80} value={nuevo.marca}
              onChange={(e) => setNuevo({ ...nuevo, marca: e.target.value })} />
          </label>
          <label className="field"><span>Empresa</span>
            <input type="text" aria-label="Empresa del sitio nuevo" maxLength={80} value={nuevo.empresa}
              onChange={(e) => setNuevo({ ...nuevo, empresa: e.target.value })} />
          </label>
          <label className="field"><span>Tipo</span>
            <select aria-label="Tipo del sitio nuevo" value={nuevo.tipo}
              onChange={(e) => setNuevo({ ...nuevo, tipo: e.target.value as "web" | "correo" })}>
              <option value="web">web</option>
              <option value="correo">solo correo</option>
            </select>
          </label>
          <label className="field"><span>Idioma por defecto</span>
            <input type="text" aria-label="Idioma por defecto del sitio nuevo" maxLength={5}
              value={nuevo.idioma_defecto}
              onChange={(e) => setNuevo({ ...nuevo, idioma_defecto: e.target.value })} />
          </label>
          <label className="field"><span>Idiomas publicados</span>
            <input type="text" aria-label="Idiomas del sitio nuevo" placeholder="es, en, de"
              value={nuevo.idiomas}
              onChange={(e) => setNuevo({ ...nuevo, idiomas: e.target.value })} />
          </label>
          <label className="field"><span>Remitente por defecto</span>
            <input type="email" aria-label="Remitente del sitio nuevo" placeholder="info@nuevaweb.com"
              maxLength={255} value={nuevo.remitente_defecto}
              onChange={(e) => setNuevo({ ...nuevo, remitente_defecto: e.target.value })} />
          </label>
          <label className="field erp-check-field">
            <input type="checkbox" aria-label="Soporte propio del sitio nuevo"
              checked={nuevo.soporte_propio}
              onChange={(e) => setNuevo({ ...nuevo, soporte_propio: e.target.checked })} />
            <span>Soporte propio (en todos sus idiomas)</span>
          </label>
          <button type="button" className="button small" onClick={crear}
            disabled={creando || !nuevo.clave.trim() || !nuevo.dominio.trim()
              || !nuevo.marca.trim() || !nuevo.empresa.trim()}>
            {creando ? "Añadiendo…" : "Añadir sitio"}
          </button>
          {errorAlta ? <p className="form-error" role="alert">{errorAlta}</p> : null}
        </div>
      ) : null}
    </section>
  );
}

function FilaSitio({
  sitio, canEdit, onDatos,
}: { sitio: SitioWeb; canEdit: boolean; onDatos: (d: Conocimiento) => void }) {
  const [ocupada, setOcupada] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [remitente, setRemitente] = useState(sitio.remitente_defecto ?? "");
  const [idiomas, setIdiomas] = useState(sitio.idiomas.join(", "));

  async function cambiar(cambios: Parameters<typeof updateSitioWeb>[1]) {
    setOcupada(true);
    setError(null);
    try {
      onDatos(await updateSitioWeb(sitio.clave, cambios));
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo guardar el sitio."));
    } finally {
      setOcupada(false);
    }
  }

  const idiomasLista = idiomas.split(/[\s,]+/).filter(Boolean);
  const idiomasCambiados = idiomasLista.join(",") !== sitio.idiomas.join(",");
  const remitenteCambiado = remitente.trim() !== (sitio.remitente_defecto ?? "");

  return (
    <tr className={sitio.activo ? undefined : "is-inactivo"}>
      <td data-label="Clave"><code>{sitio.clave}</code></td>
      <td data-label="Dominio">
        {sitio.host}
        {sitio.dominios_extra.length > 0 ? (
          <><br /><span className="muted small">+ {sitio.dominios_extra.join(", ")}</span></>
        ) : null}
      </td>
      <td data-label="Marca · empresa">{sitio.marca}<br /><span className="muted small">{sitio.empresa}</span></td>
      <td data-label="Tipo">{sitio.tipo === "web" ? "web" : "solo correo"}</td>
      <td data-label="Idiomas">
        {canEdit ? (
          <span className="kb-inline">
            <input type="text" aria-label={`Idiomas de ${sitio.clave}`} value={idiomas}
              disabled={ocupada} onChange={(e) => setIdiomas(e.target.value)} />
            {idiomasCambiados ? (
              <button type="button" className="button small" disabled={ocupada}
                onClick={() => cambiar({ idiomas: idiomasLista })}>Guardar</button>
            ) : null}
          </span>
        ) : sitio.idiomas.join(", ")}
        <br /><span className="muted small">por defecto: {nombreIdioma(sitio.idioma_defecto)}</span>
      </td>
      <td data-label="Plugin">{sitio.plugin_traduccion ?? "—"}{sitio.slugs_traducidos ? " (slugs traducidos)" : ""}</td>
      <td data-label="Soporte propio">
        <input type="checkbox" aria-label={`Soporte propio ${sitio.clave}`}
          checked={sitio.soporte_propio} disabled={!canEdit || ocupada}
          onChange={(e) => cambiar({ soporte_propio: e.target.checked })} />
      </td>
      <td data-label="Remitente">
        {canEdit ? (
          <span className="kb-inline">
            <input type="email" aria-label={`Remitente de ${sitio.clave}`} value={remitente}
              disabled={ocupada} onChange={(e) => setRemitente(e.target.value)} />
            {remitenteCambiado ? (
              <button type="button" className="button small" disabled={ocupada}
                onClick={() => cambiar({ remitente_defecto: remitente.trim() || "" })}>Guardar</button>
            ) : null}
          </span>
        ) : (sitio.remitente_defecto ?? "—")}
      </td>
      <td data-label="Activo">
        <input type="checkbox" aria-label={`Activo ${sitio.clave}`} checked={sitio.activo}
          disabled={!canEdit || ocupada} onChange={(e) => cambiar({ activo: e.target.checked })} />
        {error ? <><br /><span className="form-error small" role="alert">{error}</span></> : null}
      </td>
    </tr>
  );
}

// --- rutas -----------------------------------------------------------------------

function estadoComprobacion(r: KbRuta): { texto: string; clase: string } {
  if (!r.comprobado_en) return { texto: "sin comprobar", clase: "" };
  if (r.comprobado_http === 200) return { texto: `200 · ${fechaCorta(r.comprobado_en)}`, clase: "is-ok" };
  if (r.comprobado_http === null) return { texto: `sin respuesta · ${r.comprobado_error ?? ""}`, clase: "is-error" };
  return { texto: `${r.comprobado_http} · ${fechaCorta(r.comprobado_en)}`, clase: "is-error" };
}

function RutasTabla({
  datos, tipo, canEdit, onDatos,
}: { datos: Conocimiento; tipo: "soporte" | "producto"; canEdit: boolean; onDatos: (d: Conocimiento) => void }) {
  const rutas = datos.rutas.filter((r) => r.tipo === tipo);
  const webs = datos.sitios.filter((s) => s.tipo === "web");
  const [nueva, setNueva] = useState({ sitio: webs[0]?.clave ?? "", idioma: "es", interes: "", url: "", nota: "" });
  const [creando, setCreando] = useState(false);
  const [errorAlta, setErrorAlta] = useState<string | null>(null);
  const [comprobando, setComprobando] = useState(false);
  const [aviso, setAviso] = useState<string | null>(null);
  const esSoporte = tipo === "soporte";
  const titulo = esSoporte ? "Rutas de soporte (web × idioma)" : "Páginas de producto por interés";
  const idSeccion = `kb-rutas-${tipo}`;

  async function crear() {
    setCreando(true);
    setErrorAlta(null);
    try {
      onDatos(await createKbRuta({
        tipo, sitio: nueva.sitio, url: nueva.url.trim(), nota: nueva.nota.trim() || undefined,
        ...(esSoporte ? { idioma: nueva.idioma.trim() } : { interes: nueva.interes.trim() }),
      }));
      setNueva({ ...nueva, url: "", nota: "", interes: "" });
    } catch (e) {
      setErrorAlta(extractErrorMessage(e, "No se pudo añadir la ruta."));
    } finally {
      setCreando(false);
    }
  }

  async function comprobar() {
    setComprobando(true);
    setAviso(null);
    try {
      const r = await comprobarKbRutas();
      onDatos({ ...datos, rutas: r.rutas });
      const mal = r.resultados.filter((x) => !x.ok);
      setAviso(mal.length === 0
        ? `${r.resultados.length} URL comprobadas: todas devuelven 200.`
        : `${r.resultados.length} URL comprobadas, ${mal.length} con problema: ${mal.map((m) => m.url).join(", ")}.`);
    } catch (e) {
      setAviso(extractErrorMessage(e, "No se pudieron comprobar las URL."));
    } finally {
      setComprobando(false);
    }
  }

  return (
    <section className="kb-bloque" aria-labelledby={`${idSeccion}-t`}>
      <h3 id={`${idSeccion}-t`}>{titulo}</h3>
      <p className="muted small">
        {esSoporte
          ? "A dónde se manda a quien pide soporte: la fila exacta web × idioma; si el idioma no está, la del idioma por defecto de la web; y si la web no tiene filas, castellano → Streamtec y el resto → MQ Europe. Lo que no está aquí no se enlaza."
          : "La página de mqeurope.com de cada interés del clasificador. En otro idioma se enlaza la misma página con el prefijo de idioma, si la web lo publica."}
      </p>
      <table className="data-table data-table--responsive erp-settings-table kb-tabla">
        <thead>
          <tr>
            <th>Web</th><th>{esSoporte ? "Idioma" : "Interés"}</th><th>URL</th><th>Nota</th>
            <th>Comprobación</th><th>Activa</th>
          </tr>
        </thead>
        <tbody>
          {rutas.map((r) => <FilaRuta key={r.id} ruta={r} canEdit={canEdit} onDatos={onDatos} />)}
        </tbody>
      </table>
      <div className="kb-acciones">
        {esSoporte ? (
          <button type="button" className="button small" onClick={comprobar} disabled={comprobando}>
            {comprobando ? "Comprobando…" : "Comprobar que las URL devuelven 200"}
          </button>
        ) : null}
        {aviso ? <span className="muted small" role="status">{aviso}</span> : null}
      </div>
      {canEdit ? (
        <div className="kb-alta">
          <label className="field"><span>Web</span>
            <select aria-label={`Web de la ruta nueva (${tipo})`} value={nueva.sitio}
              onChange={(e) => setNueva({ ...nueva, sitio: e.target.value })}>
              {webs.map((w) => <option key={w.clave} value={w.clave}>{w.host}</option>)}
            </select>
          </label>
          {esSoporte ? (
            <label className="field"><span>Idioma</span>
              <input type="text" aria-label="Idioma de la ruta nueva" maxLength={5} value={nueva.idioma}
                onChange={(e) => setNueva({ ...nueva, idioma: e.target.value })} />
            </label>
          ) : (
            <label className="field"><span>Interés</span>
              <input type="text" aria-label="Interés de la página nueva" placeholder="p. ej. dtf"
                maxLength={40} value={nueva.interes}
                onChange={(e) => setNueva({ ...nueva, interes: e.target.value })} />
            </label>
          )}
          <label className="field kb-campo-url"><span>URL</span>
            <input type="url" aria-label={`URL de la ruta nueva (${tipo})`} placeholder="https://…"
              maxLength={500} value={nueva.url}
              onChange={(e) => setNueva({ ...nueva, url: e.target.value })} />
          </label>
          <label className="field"><span>Nota</span>
            <input type="text" aria-label={`Nota de la ruta nueva (${tipo})`} maxLength={255}
              value={nueva.nota} onChange={(e) => setNueva({ ...nueva, nota: e.target.value })} />
          </label>
          <button type="button" className="button small" onClick={crear}
            disabled={creando || !nueva.sitio || !nueva.url.trim()
              || (esSoporte ? !nueva.idioma.trim() : !nueva.interes.trim())}>
            {creando ? "Añadiendo…" : (esSoporte ? "Añadir ruta" : "Añadir página")}
          </button>
          {errorAlta ? <p className="form-error" role="alert">{errorAlta}</p> : null}
        </div>
      ) : null}
    </section>
  );
}

function FilaRuta({
  ruta, canEdit, onDatos,
}: { ruta: KbRuta; canEdit: boolean; onDatos: (d: Conocimiento) => void }) {
  const [url, setUrl] = useState(ruta.url);
  const [ocupada, setOcupada] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const estado = estadoComprobacion(ruta);
  const clave = ruta.tipo === "soporte" ? `${ruta.sitio} ${ruta.idioma}` : `${ruta.sitio} ${ruta.interes}`;

  async function cambiar(cambios: Parameters<typeof updateKbRuta>[1]) {
    setOcupada(true);
    setError(null);
    try {
      onDatos(await updateKbRuta(ruta.id, cambios));
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo guardar la ruta."));
    } finally {
      setOcupada(false);
    }
  }

  return (
    <tr className={ruta.activo ? undefined : "is-inactivo"}>
      <td data-label="Web">{ruta.sitio}</td>
      <td data-label={ruta.tipo === "soporte" ? "Idioma" : "Interés"}>
        {ruta.tipo === "soporte" ? `${ruta.idioma} · ${nombreIdioma(ruta.idioma)}` : <code>{ruta.interes}</code>}
      </td>
      <td data-label="URL">
        {canEdit ? (
          <span className="kb-inline kb-inline-url">
            <input type="url" aria-label={`URL de ${clave}`} value={url} disabled={ocupada}
              onChange={(e) => setUrl(e.target.value)} />
            {url.trim() !== ruta.url ? (
              <button type="button" className="button small" disabled={ocupada}
                onClick={() => cambiar({ url: url.trim() })}>Guardar</button>
            ) : null}
          </span>
        ) : <a href={ruta.url} target="_blank" rel="noreferrer">{ruta.url}</a>}
      </td>
      <td data-label="Nota" className="muted small">{ruta.nota ?? ""}</td>
      <td data-label="Comprobación"><span className={`kb-estado ${estado.clase}`}>{estado.texto}</span></td>
      <td data-label="Activa">
        <input type="checkbox" aria-label={`Activa ${clave}`} checked={ruta.activo}
          disabled={!canEdit || ocupada} onChange={(e) => cambiar({ activo: e.target.checked })} />
        {error ? <><br /><span className="form-error small" role="alert">{error}</span></> : null}
      </td>
    </tr>
  );
}

// --- FLUX ------------------------------------------------------------------------

function FluxBloque({
  datos, canEdit, onDatos,
}: { datos: Conocimiento; canEdit: boolean; onDatos: (d: Conocimiento) => void }) {
  const flux = datos.flux;
  const [sincronizando, setSincronizando] = useState(false);
  const [aviso, setAviso] = useState<string | null>(null);
  const [nueva, setNueva] = useState({ categoria_id: "", modelo: "", palabras: "" });
  const [creando, setCreando] = useState(false);
  const [errorAlta, setErrorAlta] = useState<string | null>(null);
  const ultima = flux.ultima_sincronizacion;

  async function sincronizar() {
    setSincronizando(true);
    setAviso(null);
    try {
      const r = await sincronizarFlux();
      onDatos({ ...datos, flux: { ...flux, articulos_por_idioma: r.articulos_por_idioma, idos: r.idos,
        ultima_sincronizacion: r.ultima_sincronizacion } });
      const s = r.sincronizacion;
      setAviso(s.estado === "ok"
        ? `Sincronizado: ${s.articulos} artículos (${s.nuevos} nuevos, ${s.actualizados} cambiados, ${s.idos} desaparecidos).`
        : `Sincronización ${s.estado}: ${s.detalle ?? "FLUX no respondió en algún idioma"}. El índice anterior sigue intacto.`);
    } catch (e) {
      setAviso(extractErrorMessage(e, "No se pudo sincronizar."));
    } finally {
      setSincronizando(false);
    }
  }

  async function crear() {
    setCreando(true);
    setErrorAlta(null);
    try {
      onDatos(await createFluxCategoria({
        categoria_id: nueva.categoria_id.trim(), modelo: nueva.modelo.trim(),
        palabras: nueva.palabras.split(",").map((p) => p.trim()).filter(Boolean),
      }));
      setNueva({ categoria_id: "", modelo: "", palabras: "" });
    } catch (e) {
      setErrorAlta(extractErrorMessage(e, "No se pudo añadir la categoría."));
    } finally {
      setCreando(false);
    }
  }

  const porIdioma = Object.entries(flux.articulos_por_idioma);

  return (
    <section className="kb-bloque" aria-labelledby="kb-flux-t">
      <h3 id="kb-flux-t">KB de FLUX (support.flux3dp.com)</h3>
      <p className="muted small">
        Se indexan el título y el enlace de cada artículo, por idioma (nunca el cuerpo). A un cliente
        de FLUX se le enlaza la categoría de su modelo en su idioma cuando pregunta cómo se hace algo;
        si la máquina está averiada o pide repuesto o garantía, nuestro soporte. Las FAQ de fluxlasers.es
        no se enlazan nunca.
      </p>
      <div className="kb-flux-estado">
        <div>
          <strong>Índice:</strong>{" "}
          {porIdioma.length === 0 ? "vacío (sin sincronizar todavía)" : (
            porIdioma.map(([idioma, n]) => `${nombreIdioma(idioma)} ${n}`).join(" · ")
          )}
          {flux.idos > 0 ? <span className="muted small"> · {flux.idos} desaparecidos</span> : null}
        </div>
        <div className="muted small">
          Última sincronización: {ultima
            ? `${fechaCorta(ultima.inicio)} · ${ultima.origen} · ${ultima.estado}`
            : "ninguna"}
        </div>
        <div className="kb-acciones">
          <button type="button" className="button small" onClick={sincronizar}
            disabled={sincronizando || !canEdit}>
            {sincronizando ? "Sincronizando…" : "Sincronizar ahora"}
          </button>
          {aviso ? <span className="muted small" role="status">{aviso}</span> : null}
        </div>
      </div>
      <table className="data-table data-table--responsive erp-settings-table kb-tabla">
        <thead>
          <tr><th>Modelo</th><th>Categoría</th><th>Palabras que lo reconocen</th><th>Activa</th></tr>
        </thead>
        <tbody>
          {flux.categorias.map((c) => (
            <FilaCategoria key={c.categoria_id} categoria={c} canEdit={canEdit} onDatos={onDatos} />
          ))}
        </tbody>
      </table>
      {canEdit ? (
        <div className="kb-alta">
          <label className="field"><span>Id de la categoría</span>
            <input type="text" aria-label="Id de la categoría de FLUX nueva" placeholder="número de la URL"
              maxLength={30} value={nueva.categoria_id}
              onChange={(e) => setNueva({ ...nueva, categoria_id: e.target.value })} />
          </label>
          <label className="field"><span>Modelo</span>
            <input type="text" aria-label="Modelo de la categoría de FLUX nueva" maxLength={80}
              value={nueva.modelo} onChange={(e) => setNueva({ ...nueva, modelo: e.target.value })} />
          </label>
          <label className="field"><span>Palabras (separadas por comas)</span>
            <input type="text" aria-label="Palabras de la categoría de FLUX nueva"
              value={nueva.palabras} onChange={(e) => setNueva({ ...nueva, palabras: e.target.value })} />
          </label>
          <button type="button" className="button small" onClick={crear}
            disabled={creando || !nueva.categoria_id.trim() || !nueva.modelo.trim()}>
            {creando ? "Añadiendo…" : "Añadir categoría"}
          </button>
          {errorAlta ? <p className="form-error" role="alert">{errorAlta}</p> : null}
        </div>
      ) : null}
    </section>
  );
}

function FilaCategoria({
  categoria, canEdit, onDatos,
}: { categoria: FluxCategoria; canEdit: boolean; onDatos: (d: Conocimiento) => void }) {
  const [palabras, setPalabras] = useState(categoria.palabras.join(", "));
  const [ocupada, setOcupada] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const lista = palabras.split(",").map((p) => p.trim()).filter(Boolean);
  const cambiadas = lista.join("|") !== categoria.palabras.join("|");

  async function cambiar(cambios: Parameters<typeof updateFluxCategoria>[1]) {
    setOcupada(true);
    setError(null);
    try {
      onDatos(await updateFluxCategoria(categoria.categoria_id, cambios));
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo guardar la categoría."));
    } finally {
      setOcupada(false);
    }
  }

  return (
    <tr className={categoria.activo ? undefined : "is-inactivo"}>
      <td data-label="Modelo"><strong>{categoria.modelo}</strong></td>
      <td data-label="Categoría">
        <a href={categoria.enlace_es} target="_blank" rel="noreferrer"><code>{categoria.categoria_id}</code></a>
      </td>
      <td data-label="Palabras">
        {canEdit ? (
          <span className="kb-inline">
            <input type="text" aria-label={`Palabras de ${categoria.modelo}`} value={palabras}
              disabled={ocupada} onChange={(e) => setPalabras(e.target.value)} />
            {cambiadas ? (
              <button type="button" className="button small" disabled={ocupada}
                onClick={() => cambiar({ palabras: lista })}>Guardar</button>
            ) : null}
          </span>
        ) : categoria.palabras.join(", ")}
      </td>
      <td data-label="Activa">
        <input type="checkbox" aria-label={`Activa ${categoria.modelo}`} checked={categoria.activo}
          disabled={!canEdit || ocupada} onChange={(e) => cambiar({ activo: e.target.checked })} />
        {error ? <><br /><span className="form-error small" role="alert">{error}</span></> : null}
      </td>
    </tr>
  );
}

// --- probar ----------------------------------------------------------------------

function Probar({ datos }: { datos: Conocimiento }) {
  const webs = datos.sitios.filter((s) => s.tipo === "web");
  const [sitio, setSitio] = useState(webs[0]?.clave ?? "");
  const [idioma, setIdioma] = useState("de");
  const [interes, setInteres] = useState("");
  const [texto, setTexto] = useState("");
  const [resultado, setResultado] = useState<KbResolucion | null>(null);
  const [ocupado, setOcupado] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function probar() {
    setOcupado(true);
    setError(null);
    try {
      setResultado(await resolverConocimiento({
        sitio: sitio || null, idioma: idioma.trim() || null, interes: interes.trim() || null,
        texto: texto.trim() || null,
      }));
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo resolver."));
    } finally {
      setOcupado(false);
    }
  }

  const enlace = (e: { url: string; origen: string } | null | undefined, vacio: string) =>
    e ? <><a href={e.url} target="_blank" rel="noreferrer">{e.url}</a> <span className="muted small">({e.origen})</span></> : <span className="muted small">{vacio}</span>;

  return (
    <section className="kb-bloque" aria-labelledby="kb-probar-t">
      <h3 id="kb-probar-t">Probar: qué enlaces saldrían</h3>
      <p className="muted small">
        Lo mismo que mira el asistente antes de redactar: elige web e idioma (y, si quieres, el
        interés y lo que escribiría el cliente) y mira qué enlaces le tocan.
      </p>
      <div className="kb-alta">
        <label className="field"><span>Web</span>
          <select aria-label="Web para probar" value={sitio} onChange={(e) => setSitio(e.target.value)}>
            <option value="">(sin web: remitente desconocido)</option>
            {webs.map((w) => <option key={w.clave} value={w.clave}>{w.host}</option>)}
          </select>
        </label>
        <label className="field"><span>Idioma</span>
          <input type="text" aria-label="Idioma para probar" maxLength={5} value={idioma}
            onChange={(e) => setIdioma(e.target.value)} />
        </label>
        <label className="field"><span>Interés</span>
          <input type="text" aria-label="Interés para probar" maxLength={40} value={interes}
            onChange={(e) => setInteres(e.target.value)} />
        </label>
        <label className="field kb-campo-url"><span>Lo que escribe el cliente</span>
          <input type="text" aria-label="Texto para probar" maxLength={4000} value={texto}
            onChange={(e) => setTexto(e.target.value)} />
        </label>
        <button type="button" className="button small" onClick={probar} disabled={ocupado}>
          {ocupado ? "Resolviendo…" : "Probar"}
        </button>
      </div>
      {error ? <p className="form-error" role="alert">{error}</p> : null}
      {resultado ? (
        <dl className="kb-resultado" aria-label="Enlaces que saldrían">
          <dt>Soporte</dt><dd>{enlace(resultado.soporte, "ninguno (sin web ni idioma)")}</dd>
          <dt>Página de producto</dt><dd>{enlace(resultado.producto, "ninguna (sin interés o sin página)")}</dd>
          {resultado.flux ? (
            <>
              <dt>FLUX</dt>
              <dd>
                {resultado.flux.modelo ? `${resultado.flux.modelo} · ` : "modelo desconocido · "}
                {resultado.flux.intencion === "kb" ? "cómo se hace → KB de FLUX"
                  : resultado.flux.intencion === "soporte" ? "avería / repuesto → nuestro soporte"
                  : "no está claro → nuestro soporte"}
                <br />{enlace(resultado.flux.enlace, "sin enlace")}
                {resultado.flux.articulos.length > 0 ? (
                  <ul className="kb-articulos">
                    {resultado.flux.articulos.map((a) => (
                      <li key={a.url}><a href={a.url} target="_blank" rel="noreferrer">{a.titulo}</a>{a.outdated ? " (desfasado)" : ""}</li>
                    ))}
                  </ul>
                ) : null}
              </dd>
            </>
          ) : null}
        </dl>
      ) : null}
    </section>
  );
}
