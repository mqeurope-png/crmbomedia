"use client";

import { useEffect, useState } from "react";
import {
  comprobarKbRutas,
  createFluxCategoria,
  createInteresProducto,
  createKbRuta,
  createSitioWeb,
  deleteInteresProducto,
  getCatalogoProductos,
  getConocimiento,
  resolverConocimiento,
  sincronizarCatalogo,
  sincronizarFlux,
  updateCatalogoMarca,
  updateFluxCategoria,
  updateInteresProducto,
  updateKbRuta,
  updatePalabrasInteres,
  updateSitioWeb,
  type CatalogoProducto,
  type CatalogoProductoAsistente,
  type CatalogoSincronizacion,
  type Conocimiento,
  type FluxCategoria,
  type InteresProducto,
  type KbResolucion,
  type KbRuta,
  type PalabrasInteres,
  type SitioWeb,
} from "../../lib/erpApi";
import { extractErrorMessage } from "../../lib/errors";
import { ImagenesBloque, VideosBloque } from "./MediosBloques";

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
      {/* Una API anterior a la Fase 2 no trae el catálogo: la sección pinta sin él. */}
      {datos.catalogo && datos.interes_productos ? (
        <>
          <CatalogoBloque datos={datos} canEdit={canEdit} onDatos={setDatos} />
          <InteresProductosBloque datos={datos} canEdit={canEdit} onDatos={setDatos} />
        </>
      ) : null}
      {/* Las imágenes de las webs y los vídeos de los correos compuestos (Fase 3). */}
      {datos.medios ? (
        <>
          <ImagenesBloque datos={datos} canEdit={canEdit} onDatos={setDatos} />
          <VideosBloque datos={datos} canEdit={canEdit} onDatos={setDatos} />
        </>
      ) : null}
      <Probar datos={datos} />
    </div>
  );
}

// --- catálogo y precios (Fase 2) --------------------------------------------------

const FUENTE_TEXTO: Record<string, string> = {
  compositor: "Compositor de correos (máquinas)", woocommerce: "Tiendas de las webs (consumibles y repuestos)",
};
const TIPO_TEXTO: Record<string, string> = { maquina: "máquina", consumible: "consumible", repuesto: "repuesto" };

function ivaTexto(valor: "con" | "sin" | null | undefined): string {
  if (valor === "con") return "con IVA incluido";
  if (valor === "sin") return "sin IVA";
  return "sin declarar (el correo dirá que está por confirmar)";
}

function resumenSincronizacion(s: CatalogoSincronizacion): string {
  if (s.estado === "error") return `Sincronización en error: ${s.detalle ?? "sin detalle"}. El catálogo anterior sigue intacto.`;
  const partes = Object.values(s.resumen ?? {});
  const saltadas = partes.reduce((n, r) => n + (r.saltadas ?? 0), 0);
  const compositor = partes.find((r) => r.marcas !== undefined);
  let base = `Sincronizado ${FUENTE_TEXTO[s.fuente] ?? s.fuente}: ${s.productos} filas (${s.nuevos} nuevas, ${s.actualizados} cambiadas, ${s.idos} desaparecidas${saltadas > 0 ? `, ${saltadas} saltadas por no entrar en la base` : ""}).`;
  if (compositor) {
    const extra: string[] = [];
    if (compositor.marcas !== undefined) {
      extra.push(`${compositor.marcas} marcas con producto${compositor.marcas_sin_producto?.length ? ` (sin producto, fuera: ${compositor.marcas_sin_producto.join(", ")})` : ""}`);
    }
    if (compositor.descartados?.length) extra.push(`descartadas por no ser máquinas: ${compositor.descartados.join(", ")}`);
    if (compositor.sin_precio_publico?.length) extra.push(`sin precio público: ${compositor.sin_precio_publico.join(", ")}`);
    if (extra.length > 0) base = `${base} ${extra.join(" · ")}.`;
  }
  return s.estado === "parcial" ? `${base} Parcial: ${s.detalle ?? "algún origen no respondió"}.` : base;
}

function CatalogoBloque({
  datos, canEdit, onDatos,
}: { datos: Conocimiento; canEdit: boolean; onDatos: (d: Conocimiento) => void }) {
  const cat = datos.catalogo;
  const [sincronizando, setSincronizando] = useState<string | null>(null);
  const [aviso, setAviso] = useState<string | null>(null);
  const [filtros, setFiltros] = useState({ fuente: "", tipo: "", idioma: "", marca: "", q: "", descartados: false });
  const [productos, setProductos] = useState<{ lista: CatalogoProducto[]; total: number; limite: number } | null>(null);
  const [cargando, setCargando] = useState(false);
  const [errorLista, setErrorLista] = useState<string | null>(null);

  async function sincronizar(fuente: "compositor" | "woocommerce") {
    setSincronizando(fuente);
    setAviso(null);
    try {
      const r = await sincronizarCatalogo(fuente);
      onDatos({ ...datos, catalogo: r.catalogo, interes_productos: r.interes_productos });
      setAviso(r.sincronizaciones.map(resumenSincronizacion).join(" "));
    } catch (e) {
      setAviso(extractErrorMessage(e, "No se pudo sincronizar el catálogo."));
    } finally {
      setSincronizando(null);
    }
  }

  async function cargar() {
    setCargando(true);
    setErrorLista(null);
    try {
      const r = await getCatalogoProductos({
        fuente: filtros.fuente || undefined, tipo: filtros.tipo || undefined,
        idioma: filtros.idioma.trim() || undefined, marca: filtros.marca.trim() || undefined,
        q: filtros.q.trim() || undefined, con_descartados: filtros.descartados || undefined,
      });
      setProductos({ lista: r.productos, total: r.total, limite: r.limite });
    } catch (e) {
      setErrorLista(extractErrorMessage(e, "No se pudo cargar el catálogo."));
    } finally {
      setCargando(false);
    }
  }

  const compositor = cat.fuentes.compositor;
  const tiendas = cat.fuentes.woocommerce;
  const porIdioma = (m: Record<string, number>) => Object.entries(m).map(([i, n]) => `${nombreIdioma(i)} ${n}`).join(" · ");
  const ultima = (s: CatalogoSincronizacion | null) => (s
    ? `${fechaCorta(s.inicio)} · ${s.origen} · ${s.estado}${s.origen_actualizado_en ? ` · fila del compositor del ${fechaCorta(s.origen_actualizado_en)}` : ""}`
    : "ninguna");
  const ivaSinDeclarar = Object.values(cat.iva_por_fuente).some((v) => !v);

  return (
    <section className="kb-bloque" aria-labelledby="kb-catalogo-t">
      <h3 id="kb-catalogo-t">Catálogo y precios verificados</h3>
      <p className="muted small">
        Los únicos precios que el asistente puede escribir. Las máquinas salen del compositor de
        correos de Bomedia (su fila de Supabase, solo lectura); los consumibles y repuestos, de las
        tiendas WooCommerce de las webs. El precio se enseña tal cual está escrito en el origen y
        nunca se convierte de moneda; sin precio en el idioma del cliente, el del idioma por defecto
        de la marca, diciéndolo. Todo lo demás no tiene precio: lo que aparece en un correo antiguo no
        es un precio vigente.
      </p>
      {ivaSinDeclarar ? (
        <div className="kb-aviso" role="note">
          <p>
            <strong>IVA sin declarar</strong> en {Object.entries(cat.iva_por_fuente).filter(([, v]) => !v).map(([f]) => FUENTE_TEXTO[f] ?? f).join(" y ")}:
            mientras no se declare en los ajustes de esta sección, los correos dirán que está pendiente de
            confirmar si los precios incluyen IVA. Se declara; no se deduce del número.
          </p>
        </div>
      ) : null}
      <div className="kb-fuentes">
        <div className="kb-fuente" aria-label="Estado del compositor">
          <h4>{FUENTE_TEXTO.compositor}</h4>
          <div>
            <strong>{compositor.productos}</strong> filas (producto × idioma)
            {compositor.productos > 0 ? <span className="muted small"> · {porIdioma(compositor.por_idioma)}</span> : null}
          </div>
          <div className="muted small">Última sincronización: {ultima(compositor.ultima_sincronizacion)}</div>
          {(cat.descartados ?? 0) > 0 || (cat.sin_precio_publico ?? 0) > 0 ? (
            <div className="muted small" data-testid="kb-descartes">
              {(cat.descartados ?? 0) > 0 ? `${cat.descartados} entrada${cat.descartados === 1 ? "" : "s"} descartada${cat.descartados === 1 ? "" : "s"} (sin precio ni área: premios, menciones; se ven con «Ver descartados»)` : null}
              {(cat.descartados ?? 0) > 0 && (cat.sin_precio_publico ?? 0) > 0 ? " · " : null}
              {(cat.sin_precio_publico ?? 0) > 0 ? `${cat.sin_precio_publico} producto${cat.sin_precio_publico === 1 ? "" : "s"} sin precio público («Consultar» en el origen: el correo dice que se prepara a medida)` : null}
            </div>
          ) : null}
          <div className="muted small">
            IVA: {ivaTexto(cat.iva_por_fuente.compositor)} · precios de más de {cat.precio_meses_orientativo} meses: orientativos
          </div>
          {compositor.clave_configurada === false ? (
            <div className="kb-estado is-error">Sin clave del compositor en el servidor (SUPABASE_COMPOSER_KEY): no se puede sincronizar.</div>
          ) : null}
          <div className="kb-acciones">
            <button type="button" className="button small" onClick={() => sincronizar("compositor")}
              disabled={!canEdit || sincronizando !== null}>
              {sincronizando === "compositor" ? "Sincronizando…" : "Sincronizar compositor"}
            </button>
          </div>
        </div>
        <div className="kb-fuente" aria-label="Estado de las tiendas">
          <h4>{FUENTE_TEXTO.woocommerce}</h4>
          <div>
            <strong>{tiendas.productos}</strong> filas
            {tiendas.por_tienda && Object.keys(tiendas.por_tienda).length > 0 ? (
              <span className="muted small"> · {Object.entries(tiendas.por_tienda).map(([s, n]) => `${s} ${n}`).join(" · ")}</span>
            ) : null}
          </div>
          {tiendas.productos > 0 ? (
            <div className="muted small">
              {Object.entries(tiendas.por_tipo).map(([t, n]) => `${n} ${TIPO_TEXTO[t] ?? t}${n === 1 ? "" : "s"}`).join(" · ")}
              {" · "}{porIdioma(tiendas.por_idioma)}
            </div>
          ) : null}
          <div className="muted small">Última sincronización: {ultima(tiendas.ultima_sincronizacion)}</div>
          <div className="muted small">IVA: {ivaTexto(cat.iva_por_fuente.woocommerce)}</div>
          <div className="kb-acciones">
            <button type="button" className="button small" onClick={() => sincronizar("woocommerce")}
              disabled={!canEdit || sincronizando !== null}>
              {sincronizando === "woocommerce" ? "Sincronizando…" : "Sincronizar tiendas"}
            </button>
          </div>
        </div>
      </div>
      {aviso ? <p className="muted small" role="status">{aviso}</p> : null}
      {cat.marcas.length > 0 ? (
        <table className="data-table data-table--responsive erp-settings-table kb-tabla" aria-label="Marcas del compositor">
          <thead><tr><th>Marca</th><th>Idioma por defecto (precio de respaldo)</th><th>Webs</th></tr></thead>
          <tbody>
            {cat.marcas.map((m) => (
              <FilaMarca key={m.nombre} marca={m} canEdit={canEdit} onDatos={onDatos} idiomas={datos.idiomas} />
            ))}
          </tbody>
        </table>
      ) : null}
      <div className="kb-alta">
        <label className="field"><span>Fuente</span>
          <select aria-label="Fuente del catálogo" value={filtros.fuente}
            onChange={(e) => setFiltros({ ...filtros, fuente: e.target.value })}>
            <option value="">todas</option>
            <option value="compositor">compositor</option>
            <option value="woocommerce">tiendas</option>
          </select>
        </label>
        <label className="field"><span>Tipo</span>
          <select aria-label="Tipo de producto" value={filtros.tipo}
            onChange={(e) => setFiltros({ ...filtros, tipo: e.target.value })}>
            <option value="">todos</option>
            {cat.tipos.map((t) => <option key={t} value={t}>{TIPO_TEXTO[t] ?? t}</option>)}
          </select>
        </label>
        <label className="field"><span>Idioma</span>
          <input type="text" aria-label="Idioma del catálogo" maxLength={5} value={filtros.idioma}
            onChange={(e) => setFiltros({ ...filtros, idioma: e.target.value })} />
        </label>
        <label className="field"><span>Marca</span>
          <input type="text" aria-label="Marca del catálogo" maxLength={80} value={filtros.marca}
            onChange={(e) => setFiltros({ ...filtros, marca: e.target.value })} />
        </label>
        <label className="field kb-campo-url"><span>Buscar (nombre, categoría)</span>
          <input type="text" aria-label="Buscar en el catálogo" maxLength={120} value={filtros.q}
            onChange={(e) => setFiltros({ ...filtros, q: e.target.value })} />
        </label>
        <label className="field kb-check">
          <input type="checkbox" aria-label="Ver descartados" checked={filtros.descartados}
            onChange={(e) => setFiltros({ ...filtros, descartados: e.target.checked })} />
          <span>Ver descartados</span>
        </label>
        <button type="button" className="button small" onClick={cargar} disabled={cargando}>
          {cargando ? "Cargando…" : (productos ? "Volver a cargar" : "Ver el catálogo")}
        </button>
      </div>
      {errorLista ? <p className="form-error" role="alert">{errorLista}</p> : null}
      {productos ? (
        <>
          <p className="muted small" role="status">
            {productos.total === 0 ? "Ningún producto con esos filtros." : `${productos.total} fila${productos.total === 1 ? "" : "s"}${productos.total >= productos.limite ? ` (se enseñan las primeras ${productos.limite}: afina los filtros)` : ""}.`}
          </p>
          {productos.total > 0 ? (
            <div className="kb-tabla-scroll">
              <table className="data-table erp-settings-table kb-tabla" aria-label="Productos del catálogo">
                <thead>
                  <tr>
                    <th>Fuente</th><th>Tipo</th><th>Marca</th><th>Producto</th><th>Idioma</th>
                    <th>Precio (tal cual)</th><th>Enlace</th><th>Web</th><th>Origen actualizado</th>
                  </tr>
                </thead>
                <tbody>
                  {productos.lista.map((p) => (
                    <tr key={p.id} className={p.visible && !p.descartado ? undefined : "is-inactivo"}>
                      <td>{p.fuente === "compositor" ? "compositor" : "tienda"}</td>
                      <td>{TIPO_TEXTO[p.tipo] ?? p.tipo}</td>
                      <td>{p.marca}</td>
                      <td>
                        <strong>{p.nombre}</strong>
                        {p.descartado ? <><br /><span className="kb-estado is-error">descartado: {p.motivo_descarte ?? "no es un producto"}</span></> : null}
                        {p.categoria ? <><br /><span className="muted small">{p.categoria}</span></> : null}
                      </td>
                      <td>{p.idioma}</td>
                      <td className="kb-precio">
                        {p.precio_texto ?? (
                          <span className="muted small">{p.precio_publico === false ? "sin precio público (el origen dice «Consultar»)" : "sin precio"}</span>
                        )}
                        {p.moneda && p.precio_texto ? <span className="muted small"> {p.moneda}</span> : null}
                      </td>
                      <td>{p.enlace ? <a href={p.enlace} target="_blank" rel="noreferrer">abrir</a> : "—"}</td>
                      <td>{p.sitio ?? "—"}</td>
                      <td className="muted small">{fechaCorta(p.actualizado_origen)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </>
      ) : null}
    </section>
  );
}

function FilaMarca({
  marca, canEdit, onDatos, idiomas,
}: { marca: Conocimiento["catalogo"]["marcas"][number]; canEdit: boolean; onDatos: (d: Conocimiento) => void; idiomas: string[] }) {
  const [ocupada, setOcupada] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const opciones = Array.from(new Set([...idiomas, marca.idioma_defecto, "es", "fr", "de", "en", "nl"])).sort();

  async function cambiar(idioma: string) {
    setOcupada(true);
    setError(null);
    try {
      onDatos(await updateCatalogoMarca(marca.nombre, { idioma_defecto: idioma }));
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo guardar la marca."));
    } finally {
      setOcupada(false);
    }
  }

  return (
    <tr className={marca.ida ? "is-inactivo" : undefined}>
      <td data-label="Marca"><strong>{marca.nombre}</strong>{marca.ida ? <span className="muted small"> (ya no está en el compositor)</span> : null}</td>
      <td data-label="Idioma por defecto">
        {canEdit ? (
          <select aria-label={`Idioma por defecto de ${marca.nombre}`} value={marca.idioma_defecto}
            disabled={ocupada} onChange={(e) => cambiar(e.target.value)}>
            {opciones.map((i) => <option key={i} value={i}>{i} · {nombreIdioma(i)}</option>)}
          </select>
        ) : `${marca.idioma_defecto} · ${nombreIdioma(marca.idioma_defecto)}`}
        {error ? <><br /><span className="form-error small" role="alert">{error}</span></> : null}
      </td>
      <td data-label="Webs" className="muted small">
        {Object.entries(marca.urls).map(([i, u]) => <span key={i}>{i}: <a href={u} target="_blank" rel="noreferrer">{u}</a> </span>)}
      </td>
    </tr>
  );
}

// --- interés → máquinas -----------------------------------------------------------

function InteresProductosBloque({
  datos, canEdit, onDatos,
}: { datos: Conocimiento; canEdit: boolean; onDatos: (d: Conocimiento) => void }) {
  const mapa = datos.interes_productos;
  const [nueva, setNueva] = useState({ interes: "", marca: "", patron: "", nota: "" });
  const [creando, setCreando] = useState(false);
  const [errorAlta, setErrorAlta] = useState<string | null>(null);
  const intereses = Array.from(new Set([
    ...mapa.filas.map((f) => f.interes), ...mapa.huecos.map((h) => h.interes),
  ])).sort();
  const marcas = Array.from(new Set([
    ...datos.catalogo.marcas.map((m) => m.nombre), ...mapa.filas.map((f) => f.marca).filter(Boolean),
  ])).sort();

  async function crear() {
    setCreando(true);
    setErrorAlta(null);
    try {
      onDatos(await createInteresProducto({
        interes: nueva.interes.trim(), marca: nueva.marca.trim(), patron: nueva.patron.trim(),
        nota: nueva.nota.trim() || null,
      }));
      setNueva({ interes: "", marca: "", patron: "", nota: "" });
    } catch (e) {
      setErrorAlta(extractErrorMessage(e, "No se pudo añadir la fila."));
    } finally {
      setCreando(false);
    }
  }

  return (
    <section className="kb-bloque" aria-labelledby="kb-intereses-t">
      <h3 id="kb-intereses-t">Interés → máquinas: qué se le enseña a cada lead</h3>
      <p className="muted small">
        Por cada interés del clasificador, las máquinas del compositor que encajan, por marca y
        patrón del nombre (vacío = toda la marca). El asistente mete en el borrador hasta seis
        máquinas de los intereses del lead, en su idioma, con precio tal cual y enlace; un interés
        sin fila o sin coincidencia es un hueco: se enlaza la página de producto del interés y no se
        inventa ninguna máquina.
      </p>
      {!mapa.revisado ? (
        <div className="kb-aviso" role="note">
          <p>
            <strong>Propuesta sin revisar</strong> (11/10/2026). Repásala y marca «La tabla
            interés → máquinas está revisada» en los ajustes de esta sección. {mapa.hallazgo}
          </p>
        </div>
      ) : null}
      {mapa.catalogo_vacio ? (
        <p className="muted small">El catálogo está vacío: sincroniza el compositor para ver con qué casa cada fila.</p>
      ) : null}
      {!mapa.de_la_base ? (
        <p className="muted small">Ahora mismo se usa la propuesta de partida: el primer cambio la guarda en la base.</p>
      ) : null}
      <table className="data-table data-table--responsive erp-settings-table kb-tabla" aria-label="Interés a máquinas">
        <thead>
          <tr><th>Interés</th><th>Marca</th><th>Patrón del nombre</th><th>Coincide con</th><th>Nota</th><th>Activa</th><th></th></tr>
        </thead>
        <tbody>
          {mapa.filas.map((f) => <FilaInteresProducto key={f.id} fila={f} canEdit={canEdit} onDatos={onDatos} catalogoVacio={mapa.catalogo_vacio} />)}
        </tbody>
      </table>
      {mapa.palabras ? (
        <>
          <h4 className="kb-subtitulo">Palabras que señalan cada interés</h4>
          <p className="muted small">
            Si la consulta del cliente o la instrucción dicen alguna, las máquinas de ese interés
            entran en el borrador aunque el contacto esté clasificado de otra forma, y si toca
            varios intereses entran las de todos («placas de metal y camisetas»: UV y DTF). Sin
            acentos ni mayúsculas, separadas por comas; una palabra o una frase entera, y un * al
            final vale de prefijo (<code>textil*</code>: textil, textiles, Textilien). Las de cada
            talla de UV solo cuentan si el texto dice también UV.
          </p>
          <table className="data-table data-table--responsive erp-settings-table kb-tabla" aria-label="Palabras por interés">
            <thead><tr><th>Interés</th><th>Palabras</th><th></th></tr></thead>
            <tbody>
              {mapa.palabras.map((p) => (
                <FilaPalabrasInteres key={`${p.interes}:${p.propias}:${p.palabras.join(",")}`} fila={p}
                  canEdit={canEdit} onDatos={onDatos} />
              ))}
            </tbody>
          </table>
        </>
      ) : null}
      {mapa.huecos.length > 0 ? (
        <div className="kb-aviso" role="note" aria-label="Intereses sin máquina">
          <p><strong>Sin máquina:</strong> {mapa.huecos.map((h) => `${h.etiqueta} (${h.interes}): ${h.motivo}`).join(" · ")}.</p>
        </div>
      ) : null}
      {canEdit ? (
        <div className="kb-alta">
          <label className="field"><span>Interés</span>
            <input type="text" aria-label="Interés de la fila nueva" list="kb-intereses-lista" maxLength={40}
              placeholder="p. ej. uv_mediano" value={nueva.interes}
              onChange={(e) => setNueva({ ...nueva, interes: e.target.value })} />
            <datalist id="kb-intereses-lista">{intereses.map((i) => <option key={i} value={i} />)}</datalist>
          </label>
          <label className="field"><span>Marca</span>
            <input type="text" aria-label="Marca de la fila nueva" list="kb-marcas-lista" maxLength={80}
              value={nueva.marca} onChange={(e) => setNueva({ ...nueva, marca: e.target.value })} />
            <datalist id="kb-marcas-lista">{marcas.map((m) => <option key={m} value={m} />)}</datalist>
          </label>
          <label className="field"><span>Patrón del nombre</span>
            <input type="text" aria-label="Patrón de la fila nueva" maxLength={80} placeholder="p. ej. 6090"
              value={nueva.patron} onChange={(e) => setNueva({ ...nueva, patron: e.target.value })} />
          </label>
          <label className="field"><span>Nota</span>
            <input type="text" aria-label="Nota de la fila nueva" maxLength={255}
              value={nueva.nota} onChange={(e) => setNueva({ ...nueva, nota: e.target.value })} />
          </label>
          <button type="button" className="button small" onClick={crear}
            disabled={creando || !nueva.interes.trim() || (!nueva.marca.trim() && !nueva.patron.trim())}>
            {creando ? "Añadiendo…" : "Añadir fila"}
          </button>
          {errorAlta ? <p className="form-error" role="alert">{errorAlta}</p> : null}
        </div>
      ) : null}
    </section>
  );
}

function FilaInteresProducto({
  fila, canEdit, onDatos, catalogoVacio,
}: { fila: InteresProducto; canEdit: boolean; onDatos: (d: Conocimiento) => void; catalogoVacio: boolean }) {
  const [patron, setPatron] = useState(fila.patron);
  const [ocupada, setOcupada] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const clave = `${fila.interes} ${fila.marca} ${fila.patron}`.trim();

  async function cambiar(cambios: Parameters<typeof updateInteresProducto>[1]) {
    setOcupada(true);
    setError(null);
    try {
      onDatos(await updateInteresProducto(fila.id, cambios));
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo guardar la fila."));
    } finally {
      setOcupada(false);
    }
  }

  async function borrar() {
    if (!window.confirm(`¿Borrar la fila «${clave}»? El asistente dejará de enseñar esas máquinas para ese interés.`)) return;
    setOcupada(true);
    setError(null);
    try {
      onDatos(await deleteInteresProducto(fila.id));
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo borrar la fila."));
      setOcupada(false);
    }
  }

  return (
    <tr className={fila.activo ? undefined : "is-inactivo"}>
      <td data-label="Interés">{fila.etiqueta}<br /><code>{fila.interes}</code></td>
      <td data-label="Marca">{fila.marca || <span className="muted small">(cualquiera)</span>}</td>
      <td data-label="Patrón">
        {canEdit ? (
          <span className="kb-inline">
            <input type="text" aria-label={`Patrón de ${clave}`} value={patron} disabled={ocupada}
              onChange={(e) => setPatron(e.target.value)} />
            {patron.trim() !== fila.patron ? (
              <button type="button" className="button small" disabled={ocupada}
                onClick={() => cambiar({ patron: patron.trim() })}>Guardar</button>
            ) : null}
          </span>
        ) : (fila.patron || <span className="muted small">(toda la marca)</span>)}
      </td>
      <td data-label="Coincide con">
        <span className="kb-coincide">
          {fila.coincide.length > 0
            ? fila.coincide.map((c) => <span key={c.origen_id}>{c.nombre}</span>)
            : <span className="is-vacio">{catalogoVacio ? "catálogo vacío" : "sin coincidencia"}</span>}
        </span>
      </td>
      <td data-label="Nota" className="muted small">{fila.nota ?? ""}</td>
      <td data-label="Activa">
        <input type="checkbox" aria-label={`Activa ${clave}`} checked={fila.activo}
          disabled={!canEdit || ocupada} onChange={(e) => cambiar({ activo: e.target.checked })} />
      </td>
      <td>
        {canEdit ? (
          <button type="button" className="button small ghost" aria-label={`Borrar ${clave}`}
            disabled={ocupada} onClick={borrar}>Borrar</button>
        ) : null}
        {error ? <><br /><span className="form-error small" role="alert">{error}</span></> : null}
      </td>
    </tr>
  );
}

function normalizarPalabra(palabra: string): string {
  return palabra.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/\s+/g, " ");
}

function FilaPalabrasInteres({
  fila, canEdit, onDatos,
}: { fila: PalabrasInteres; canEdit: boolean; onDatos: (d: Conocimiento) => void }) {
  const [texto, setTexto] = useState(fila.palabras.join(", "));
  const [ocupada, setOcupada] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const palabras = texto.split(",").map((p) => p.trim()).filter(Boolean);
  // El servidor las guarda en minúsculas y sin acentos: «Textil» es la misma palabra que «textil».
  const cambiada = palabras.map(normalizarPalabra).join(",") !== fila.palabras.join(",");

  async function guardar(lista: string[] | null) {
    setOcupada(true);
    setError(null);
    try {
      const datos = await updatePalabrasInteres(fila.interes, lista);
      // Si la lista guardada es la misma, la fila no se vuelve a montar: el texto queda como lo
      // ha guardado el servidor.
      const guardada = datos.interes_productos.palabras?.find((p) => p.interes === fila.interes);
      if (guardada) setTexto(guardada.palabras.join(", "));
      onDatos(datos);
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudieron guardar las palabras."));
    } finally {
      setOcupada(false);
    }
  }

  return (
    <tr>
      <td data-label="Interés">
        {fila.etiqueta}<br /><code>{fila.interes}</code>
        {fila.propias ? <><br /><span className="muted small">cambiadas</span></> : null}
      </td>
      <td data-label="Palabras">
        {canEdit ? (
          <textarea aria-label={`Palabras de ${fila.interes}`} rows={2} value={texto}
            disabled={ocupada} onChange={(e) => setTexto(e.target.value)} />
        ) : (fila.palabras.join(", ") || <span className="muted small">(ninguna)</span>)}
        {error ? <span className="form-error small" role="alert">{error}</span> : null}
      </td>
      <td>
        {canEdit && cambiada ? (
          <button type="button" className="button small" disabled={ocupada}
            onClick={() => guardar(palabras)}>Guardar</button>
        ) : null}
        {canEdit && fila.propias ? (
          <button type="button" className="link-button small" disabled={ocupada}
            onClick={() => guardar(null)}>↺ las de partida</button>
        ) : null}
      </td>
    </tr>
  );
}

function ProductoAsistente({ p }: { p: CatalogoProductoAsistente }) {
  return (
    <li>
      <strong>{p.nombre}</strong> ({p.marca})
      {" · "}
      {p.precio_texto ? (
        <>
          <span className="kb-precio">{p.precio_texto}</span>
          {p.moneda ? <span className="muted small"> {p.moneda}</span> : null}
          {p.idioma_respaldo ? <span className="muted small"> · precio del idioma «{p.precio_idioma}» (no hay en el pedido)</span> : null}
          {p.orientativo ? <span className="kb-estado is-error"> · orientativo</span> : null}
        </>
      ) : <span className="muted small">sin precio publicado</span>}
      {p.enlace ? <> · <a href={p.enlace} target="_blank" rel="noreferrer">enlace{p.idioma_enlace ? ` (${p.idioma_enlace})` : ""}</a></> : null}
    </li>
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
  const [maquina, setMaquina] = useState("");
  const [resultado, setResultado] = useState<KbResolucion | null>(null);
  const [ocupado, setOcupado] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function probar() {
    setOcupado(true);
    setError(null);
    try {
      setResultado(await resolverConocimiento({
        sitio: sitio || null, idioma: idioma.trim() || null, interes: interes.trim() || null,
        texto: texto.trim() || null, ...(maquina.trim() ? { maquina: maquina.trim() } : {}),
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
        <label className="field"><span>Máquina que tiene (sus pedidos)</span>
          <input type="text" aria-label="Máquina para probar" maxLength={200} placeholder="p. ej. FLUX Beambox II"
            value={maquina} onChange={(e) => setMaquina(e.target.value)} />
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
          {resultado.catalogo ? (
            <>
              <dt>Máquinas del catálogo</dt>
              <dd>
                {resultado.catalogo.maquinas.length > 0 ? (
                  <ul className="kb-productos-asistente" aria-label="Máquinas que saldrían">
                    {resultado.catalogo.maquinas.map((p) => <ProductoAsistente key={p.origen_id} p={p} />)}
                  </ul>
                ) : (
                  <span className="muted small">
                    ninguna{resultado.catalogo.sin_maquina.length > 0 ? ` (sin máquina para ${resultado.catalogo.sin_maquina.join(", ")}: se enlaza la página de producto, no se inventa)` : ""}
                  </span>
                )}
              </dd>
              <dt>Consumibles de su máquina</dt>
              <dd>
                {resultado.catalogo.consumibles.length > 0 ? (
                  <ul className="kb-productos-asistente" aria-label="Consumibles que saldrían">
                    {resultado.catalogo.consumibles.map((p) => <ProductoAsistente key={p.origen_id} p={p} />)}
                  </ul>
                ) : <span className="muted small">ninguno (sin máquina o sin productos que casen)</span>}
              </dd>
              <dt>Avisos ({nombreIdioma(idioma.trim() || "es")})</dt>
              <dd className="muted small">
                IVA: {ivaTexto(resultado.catalogo.iva_por_fuente.compositor)} (compositor) · {ivaTexto(resultado.catalogo.iva_por_fuente.woocommerce)} (tiendas).
                {" "}«{resultado.catalogo.avisos.vies}» «{resultado.catalogo.avisos.no_factura}»
              </dd>
            </>
          ) : null}
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
