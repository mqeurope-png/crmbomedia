"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { PageHeader } from "../../components/PageHeader";
import { ArticulosClasePanel } from "../../components/erp/ArticulosClasePanel";
import { MaquinasPanel } from "../../components/erp/MaquinasPanel";
import { SeriesVentaPanel } from "../../components/erp/SeriesVentaPanel";
import { getCurrentUser, type User } from "../../lib/api";
import { Cap, can } from "../../lib/capabilities";
import {
  aceptarPropuestaFamilia,
  aceptarPropuestasFamilias,
  borrarHistoricoRegla,
  buscarHistoricoArticulos,
  cargarHistorico,
  clasificarHistoricoFamilia,
  crearHistoricoGama,
  crearHistoricoRegla,
  downloadHistoricoCompradoresCsv,
  getHistoricoCompradores,
  getHistoricoEstado,
  getHistoricoFamilias,
  getHistoricoReglas,
  HISTORICO_CLASE_LABEL,
  mantenerClasificacionFamilia,
  saveBlob,
  type HistoricoArticulo,
  type HistoricoClase,
  type HistoricoComprador,
  type HistoricoEstado,
  type HistoricoEstadoCarga,
  type HistoricoFamilia,
  type HistoricoFiltro,
  type HistoricoGama,
  type HistoricoRegla,
} from "../../lib/erpApi";
import { extractErrorMessage } from "../../lib/errors";

/** ERP · Quién compró qué — el histórico de ventas de FACTUSOL (2014 → hoy)
 *  volcado en BoHub. Se consulta lo volcado, nunca FACTUSOL en vivo; la carga
 *  solo LEE FACTUSOL. */

type Tab = "compradores" | "familias" | "carga";

const CLASE_LABEL = HISTORICO_CLASE_LABEL;
const CLASES = Object.keys(CLASE_LABEL) as HistoricoClase[];
const ESTADO_LABEL: Record<HistoricoEstado, string> = {
  cargado: "Cargado", cargando: "Cargando…", no_disponible: "No disponible en FACTUSOL",
  error: "Error", sin_cargar: "Sin cargar",
};
const ESTADO_TONE: Record<HistoricoEstado, string> = {
  cargado: "ok", cargando: "muted", no_disponible: "warn", error: "bad", sin_cargar: "muted",
};
/** Cada cuánto se mira si ha terminado una carga en marcha. */
const POLL_MS = 5000;
const nf = new Intl.NumberFormat("es-ES");

function fecha(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso.length === 10 ? `${iso}T00:00:00` : iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString("es-ES");
}

function fechaHora(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("es-ES", { dateStyle: "short", timeStyle: "short" });
}

export default function HistoricoPage() {
  const [tab, setTab] = useState<Tab>("compradores");
  const [usuario, setUsuario] = useState<User | null>(null);
  const [estado, setEstado] = useState<HistoricoEstadoCarga | null>(null);
  const [familias, setFamilias] = useState<HistoricoFamilia[]>([]);
  const [gamas, setGamas] = useState<HistoricoGama[]>([]);
  const [error, setError] = useState<string | null>(null);
  /** No se pudo leer quién es el usuario: la tabla queda en solo lectura y se
   *  dice (antes se quedaba en texto plano sin explicar por qué). */
  const [sinUsuario, setSinUsuario] = useState(false);

  const cargarEstado = useCallback(async () => {
    try {
      setEstado(await getHistoricoEstado());
    } catch (err) {
      setError(extractErrorMessage(err, "No se ha podido leer el estado de la carga."));
    }
  }, []);
  const cargarFamilias = useCallback(async () => {
    try {
      const r = await getHistoricoFamilias();
      setFamilias(r.items);
      setGamas(r.gamas);
    } catch (err) {
      setError(extractErrorMessage(err, "No se han podido leer las familias."));
    }
  }, []);

  useEffect(() => {
    getCurrentUser().then(setUsuario).catch(() => setSinUsuario(true));
    void cargarEstado();
    void cargarFamilias();
  }, [cargarEstado, cargarFamilias]);

  // Mientras haya una carga en marcha, se mira cada pocos segundos.
  const enMarcha = estado?.ejercicios.some((e) => e.estado === "cargando") ?? false;
  useEffect(() => {
    if (!enMarcha) return undefined;
    const t = window.setInterval(() => {
      if (document.visibilityState === "visible") void cargarEstado();
    }, POLL_MS);
    return () => window.clearInterval(t);
  }, [enMarcha, cargarEstado]);
  const enMarchaAntes = useRef(false);
  useEffect(() => {
    if (enMarchaAntes.current && !enMarcha) void cargarFamilias();
    enMarchaAntes.current = enMarcha;
  }, [enMarcha, cargarFamilias]);

  const cargados = estado?.ejercicios.filter((e) => e.estado === "cargado" || e.cargado_at) ?? [];
  const avisos = estado?.ejercicios.filter(
    (e) => e.estado === "no_disponible" || e.estado === "error") ?? [];

  return (
    <main className="shell shell-wide erp-flow">
      <PageHeader
        title="Quién compró qué"
        eyebrow="ERP"
        description="El histórico de ventas de FACTUSOL (2014 → hoy) volcado en BoHub: qué clientes compraron un artículo o una familia, cuándo y cuántas unidades."
        crumbs={[{ label: "ERP", href: "/erp" }, { label: "Quién compró qué" }]}
      />
      {error ? <p className="form-error" role="alert">{error}</p> : null}
      {estado && cargados.length === 0 ? (
        <p className="form-hint" role="status">
          Todavía no hay ventas volcadas. Lánzalo desde la pestaña «Carga».
        </p>
      ) : null}
      {avisos.length > 0 ? (
        <p className="form-error" role="alert">
          {avisos.length === 1 ? "Un ejercicio no se ha podido cargar" : `${avisos.length} ejercicios no se han podido cargar`}
          {" "}({avisos.map((e) => e.ejercicio).join(", ")}). Mira la pestaña «Carga».
        </p>
      ) : null}

      <nav className="erp-tabs" aria-label="Secciones">
        {(["compradores", "familias", "carga"] as const).map((t) => (
          <button
            key={t}
            type="button"
            className={`button small ${tab === t ? "" : "secondary"}`}
            onClick={() => setTab(t)}
          >
            {t === "compradores" ? "Compradores" : t === "familias" ? "Familias" : "Carga"}
          </button>
        ))}
      </nav>

      {tab === "compradores" ? (
        <CompradoresPanel enCurso={estado?.en_curso ?? null} familias={familias} gamas={gamas} />
      ) : null}
      {tab === "familias" ? (
        <FamiliasPanel
          familias={familias}
          gamas={gamas}
          puedeClasificar={can(usuario, Cap.CONFIG)}
          motivoSoloLectura={sinUsuario ? "sin_usuario" : usuario ? "sin_permiso" : null}
          onRecargar={cargarFamilias}
        />
      ) : null}
      {tab === "carga" ? (
        <CargaPanel
          estado={estado}
          puedeCargar={can(usuario, Cap.DOCUMENTS)}
          onLanzada={cargarEstado}
        />
      ) : null}
    </main>
  );
}

// --- Compradores -------------------------------------------------------------------------

function CompradoresPanel({
  enCurso, familias, gamas,
}: { enCurso: string | null; familias: HistoricoFamilia[]; gamas: HistoricoGama[] }) {
  const [texto, setTexto] = useState("");
  const [articulo, setArticulo] = useState<HistoricoArticulo | null>(null);
  const [sugerencias, setSugerencias] = useState<HistoricoArticulo[]>([]);
  // Se cierran al buscar (aunque llegue tarde la respuesta) y se abren al escribir.
  const [sugerenciasAbiertas, setSugerenciasAbiertas] = useState(true);
  const [familia, setFamilia] = useState("");
  const [clase, setClase] = useState("");
  const [gama, setGama] = useState("");
  const [desde, setDesde] = useState("");
  const [hasta, setHasta] = useState("");
  const [resultado, setResultado] = useState<{
    items: HistoricoComprador[]; total: number; sin_empresa: number;
  } | null>(null);
  const [buscando, setBuscando] = useState(false);
  // El filtro de lo que se está viendo: el CSV exporta eso, no el formulario.
  const [buscado, setBuscado] = useState<HistoricoFiltro | null>(null);
  const [error, setError] = useState<string | null>(null);

  const anios = useMemo(() => {
    const fin = Number(enCurso) || new Date().getFullYear();
    return Array.from({ length: fin - 2013 }, (_, i) => String(fin - i));
  }, [enCurso]);

  // Sugerencias de artículos mientras se escribe (código o nombre).
  useEffect(() => {
    if (articulo || texto.trim().length < 2) return undefined;
    let vivo = true;
    const t = window.setTimeout(() => {
      buscarHistoricoArticulos(texto.trim())
        .then((r) => { if (vivo) setSugerencias(r.items); })
        .catch(() => { if (vivo) setSugerencias([]); });
    }, 250);
    return () => { vivo = false; window.clearTimeout(t); };
  }, [texto, articulo]);

  const visibles = articulo || !sugerenciasAbiertas || texto.trim().length < 2 ? [] : sugerencias;

  const filtro: HistoricoFiltro = {
    codart: articulo?.codart,
    q: articulo ? undefined : texto.trim() || undefined,
    familia: familia || undefined,
    clase: clase || undefined,
    gama: gama || undefined,
    desde: desde ? Number(desde) : undefined,
    hasta: hasta ? Number(hasta) : undefined,
  };
  const hayCriterio = Boolean(
    filtro.codart || filtro.q || filtro.familia || filtro.clase || filtro.gama);

  async function buscar() {
    if (!hayCriterio) return;
    setSugerenciasAbiertas(false);
    setBuscando(true);
    setError(null);
    try {
      setResultado(await getHistoricoCompradores(filtro));
      setBuscado(filtro);
    } catch (err) {
      setError(extractErrorMessage(err, "No se ha podido buscar."));
    } finally {
      setBuscando(false);
    }
  }

  async function exportar() {
    try {
      if (buscado) saveBlob(await downloadHistoricoCompradoresCsv(buscado), "quien_compro.csv");
    } catch (err) {
      setError(extractErrorMessage(err, "No se ha podido exportar."));
    }
  }

  return (
    <section className="erp-card">
      <form
        className="erp-historico-filtros"
        onSubmit={(e) => { e.preventDefault(); void buscar(); }}
      >
        <label className="field">
          <span>Artículo (código o nombre)</span>
          <input
            type="search"
            aria-label="Artículo"
            value={articulo ? `${articulo.codart} · ${articulo.descripcion ?? ""}` : texto}
            placeholder="p. ej. 1712 o «láser 1390»"
            onChange={(e) => {
              setArticulo(null);
              setTexto(e.target.value);
              setSugerenciasAbiertas(true);
            }}
            aria-autocomplete="list"
          />
          {visibles.length > 0 ? (
            <ul className="erp-historico-sugerencias" role="listbox" aria-label="Artículos">
              {visibles.map((a) => (
                <li key={a.codart}>
                  <button
                    type="button"
                    role="option"
                    aria-selected={false}
                    onClick={() => { setArticulo(a); setSugerencias([]); }}
                  >
                    <span className="mono">{a.codart}</span> {a.descripcion ?? ""}
                    <span className="muted"> · {nf.format(a.ventas)} ventas
                      {a.ultimo_ejercicio !== enCurso ? ` · catálogo de ${a.ultimo_ejercicio}` : ""}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          ) : null}
        </label>
        <label className="field">
          <span>Familia</span>
          <select value={familia} onChange={(e) => setFamilia(e.target.value)}>
            <option value="">Cualquiera</option>
            {[...familias].sort((a, b) => a.codigo.localeCompare(b.codigo)).map((f) => (
              <option key={f.codigo} value={f.codigo}>
                {f.codigo} · {f.descripcion ?? ""}{f.clase ? ` (${CLASE_LABEL[f.clase]})` : ""}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Clase</span>
          <select value={clase} onChange={(e) => setClase(e.target.value)}>
            <option value="">Cualquiera</option>
            {CLASES.map((c) => <option key={c} value={c}>{CLASE_LABEL[c]}</option>)}
          </select>
        </label>
        <label className="field">
          <span>Gama</span>
          <select value={gama} onChange={(e) => setGama(e.target.value)}>
            <option value="">Cualquiera</option>
            {gamas.map((g) => <option key={g.codigo} value={g.codigo}>{g.nombre}</option>)}
          </select>
        </label>
        <label className="field">
          <span>Desde</span>
          <select value={desde} onChange={(e) => setDesde(e.target.value)}>
            <option value="">Desde el principio</option>
            {anios.map((a) => <option key={a} value={a}>{a}</option>)}
          </select>
        </label>
        <label className="field">
          <span>Hasta</span>
          <select value={hasta} onChange={(e) => setHasta(e.target.value)}>
            <option value="">Hasta hoy</option>
            {anios.map((a) => <option key={a} value={a}>{a}</option>)}
          </select>
        </label>
        <div className="erp-historico-acciones">
          <button type="submit" className="button small" disabled={!hayCriterio || buscando}>
            {buscando ? "Buscando…" : "Buscar"}
          </button>
          <button
            type="button"
            className="button small secondary"
            disabled={!buscado || !resultado?.total}
            onClick={() => void exportar()}
          >
            Exportar CSV
          </button>
        </div>
      </form>
      {error ? <p className="form-error" role="alert">{error}</p> : null}

      {resultado ? (
        <>
          <p className="muted" role="status">
            {resultado.total === 0
              ? "Nadie lo ha comprado con esos filtros (solo cuentan las líneas con artículo)."
              : `${nf.format(resultado.total)} ${resultado.total === 1 ? "cliente" : "clientes"}`
                + (resultado.sin_empresa
                  ? ` · ${nf.format(resultado.sin_empresa)} sin empresa vinculada en BoHub`
                  : "")}
          </p>
          {resultado.total > 0 ? (
            <table className="data-table data-table--responsive">
              <thead>
                <tr>
                  <th>Cliente FACTUSOL</th>
                  <th>Empresa en BoHub</th>
                  <th>Última compra</th>
                  <th>Documento</th>
                  <th className="num">Unidades</th>
                  <th className="num">Compras</th>
                  <th>Artículos</th>
                </tr>
              </thead>
              <tbody>
                {resultado.items.map((c) => (
                  <tr key={c.codcli ?? c.nombre ?? ""}>
                    <td data-label="Cliente">
                      {c.nombre ?? "—"} <span className="mono muted">{c.codcli ?? ""}</span>
                    </td>
                    <td data-label="Empresa">
                      {c.empresa ? (
                        <Link href={`/companies/${c.empresa.id}`} prefetch={false}>
                          {c.empresa.nombre}
                        </Link>
                      ) : (
                        <span className="badge warn">Sin empresa vinculada</span>
                      )}
                    </td>
                    <td data-label="Última compra">
                      {c.ultima_compra?.fecha
                        ? fecha(c.ultima_compra.fecha)
                        : (
                          <span title="La fecha que tiene FACTUSOL no es válida">
                            {c.ultima_compra?.ejercicio ?? "—"} (fecha no válida)
                          </span>
                        )}
                    </td>
                    <td data-label="Documento">
                      {c.ultima_compra ? (
                        <>
                          {c.ultima_compra.tipo === "factura" ? "Factura" : "Albarán"}{" "}
                          <span className="mono">{c.ultima_compra.documento}</span>
                          <span className="muted"> · {c.ultima_compra.ejercicio}</span>
                        </>
                      ) : "—"}
                    </td>
                    <td data-label="Unidades" className="num">{nf.format(c.unidades)}</td>
                    <td data-label="Compras" className="num">{c.compras}</td>
                    <td data-label="Artículos">
                      {c.articulos.map((a) => (
                        <div key={a.codart}>
                          <span className="mono">{a.codart}</span> {a.descripcion ?? ""}
                        </div>
                      ))}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : null}
          <p className="form-hint">
            Las unidades salen de las facturas; de un artículo que el cliente solo tiene en
            albaranes sin facturar, de los albaranes. Las líneas de nota (sin artículo) no cuentan.
          </p>
        </>
      ) : null}
    </section>
  );
}

// --- Familias ----------------------------------------------------------------------------

const ESTADO_PROPUESTA: Record<string, { texto: string; tono: string }> = {
  propuesta: { texto: "Propuesta", tono: "ok" },
  revisar: { texto: "Revisar", tono: "warn" },
  descartada: { texto: "Se queda la suya", tono: "muted" },
};

function FamiliasPanel({
  familias, gamas, puedeClasificar, motivoSoloLectura = null, onRecargar,
}: {
  familias: HistoricoFamilia[];
  gamas: HistoricoGama[];
  puedeClasificar: boolean;
  /** Por qué la tabla no se puede editar (si no se puede). */
  motivoSoloLectura?: "sin_usuario" | "sin_permiso" | null;
  onRecargar: () => Promise<void>;
}) {
  const [error, setError] = useState<string | null>(null);
  const [aviso, setAviso] = useState<string | null>(null);
  const [soloRevisar, setSoloRevisar] = useState(false);
  const [ocupado, setOcupado] = useState(false);
  const [nuevaGama, setNuevaGama] = useState("");
  const sinClase = familias.filter((f) => !f.clase).length;
  const sinGama = familias.filter((f) => !f.gama).length;
  const nPropuestas = familias.filter((f) => f.propuesta?.estado === "propuesta").length;
  const nRevisar = familias.filter((f) => f.propuesta?.estado === "revisar").length;
  const nombreGama = (c: string | null) => gamas.find((g) => g.codigo === c)?.nombre ?? c ?? "";
  const visibles = soloRevisar ? familias.filter((f) => f.propuesta?.estado === "revisar") : familias;

  /** Hace la acción y recarga. Si la acción devuelve un texto, es el aviso. */
  async function hacer(accion: () => Promise<unknown>, fallo: string, ok?: string) {
    setError(null);
    setAviso(null);
    setOcupado(true);
    try {
      const r = await accion();
      await onRecargar();
      const texto = typeof r === "string" ? r : ok;
      if (texto) setAviso(texto);
    } catch (err) {
      setError(extractErrorMessage(err, fallo));
    } finally {
      setOcupado(false);
    }
  }

  function textoPropuesta(f: HistoricoFamilia): string {
    const p = f.propuesta;
    if (!p) return "";
    return [p.clase ? CLASE_LABEL[p.clase] : null, p.gama ? nombreGama(p.gama) : null]
      .filter(Boolean).join(" · ");
  }

  return (
    <section className="erp-card">
      <p className="muted">
        {nf.format(familias.length)} familias de FACTUSOL
        {sinClase ? ` · ${nf.format(sinClase)} sin clase` : " · todas con clase"}
        {sinGama ? ` · ${nf.format(sinGama)} sin gama` : ""}.
        {" "}La clase dice qué es (un accesorio no es una máquina; un recambio no es un
        consumible); la gama, en una máquina, qué es, y en lo demás, para qué máquina sirve.
        Se conservan al recargar.
      </p>
      {!puedeClasificar && motivoSoloLectura ? (
        <p className="form-hint" role="note">
          {motivoSoloLectura === "sin_usuario"
            ? "Solo lectura: no se ha podido comprobar tu usuario. Recarga la página para poder clasificar."
            : "Solo lectura: clasificar las familias y marcar las máquinas es de quien tiene «Configuración ERP» (hoy, el administrador)."}
        </p>
      ) : null}
      {puedeClasificar ? (
        <div className="erp-historico-acciones erp-historico-propuestas">
          <button
            type="button"
            className="button small"
            disabled={ocupado || nPropuestas === 0}
            onClick={() => void hacer(async () => {
              const r = await aceptarPropuestasFamilias();
              return `Aplicadas ${r.aplicadas} propuestas. Las de «Revisar» no se han tocado.`;
            }, "No se han podido aplicar las propuestas.")}
          >
            Aceptar las {nPropuestas} propuestas
          </button>
          <label className="checkbox-inline">
            <input
              type="checkbox"
              checked={soloRevisar}
              onChange={(e) => setSoloRevisar(e.target.checked)}
            />
            {" "}Solo las de revisar ({nRevisar})
          </label>
        </div>
      ) : null}
      {nRevisar > 0 ? (
        <p className="form-hint">
          {nRevisar === 1 ? "Una familia ya clasificada choca" : `${nRevisar} familias ya clasificadas chocan`}
          {" "}con la propuesta. No se cambian solas: acepta la propuesta o mantén la tuya.
        </p>
      ) : null}
      {error ? <p className="form-error" role="alert">{error}</p> : null}
      {aviso ? <p className="form-hint" role="status">{aviso}</p> : null}
      {familias.length === 0 ? (
        <p className="form-hint">Las familias llegan con la primera carga.</p>
      ) : (
        <table className="data-table data-table--responsive">
          <thead>
            <tr>
              <th>Familia</th>
              <th>Descripción</th>
              <th className="num">Artículos</th>
              <th className="num">Líneas vendidas</th>
              <th>Clase</th>
              <th>Gama</th>
              <th>Propuesta</th>
            </tr>
          </thead>
          <tbody>
            {visibles.map((f) => {
              const p = f.propuesta;
              const marca = p ? ESTADO_PROPUESTA[p.estado] : undefined;
              return (
                <tr key={f.codigo}>
                  <td data-label="Familia" className="mono">{f.codigo}</td>
                  <td data-label="Descripción">
                    {f.descripcion ?? <span className="muted">Solo en catálogos de años anteriores</span>}
                  </td>
                  <td data-label="Artículos" className="num">{nf.format(f.articulos)}</td>
                  <td data-label="Líneas vendidas" className="num">{nf.format(f.ventas)}</td>
                  <td data-label="Clase">
                    {puedeClasificar ? (
                      <select
                        aria-label={`Clase de ${f.codigo}`}
                        value={f.clase ?? ""}
                        disabled={ocupado}
                        onChange={(e) => void hacer(
                          () => clasificarHistoricoFamilia(f.codigo, {
                            clase: (e.target.value || null) as HistoricoClase | null,
                          }),
                          "No se ha podido guardar la clase.")}
                      >
                        <option value="">Sin clasificar</option>
                        {CLASES.map((c) => <option key={c} value={c}>{CLASE_LABEL[c]}</option>)}
                      </select>
                    ) : (f.clase ? CLASE_LABEL[f.clase] : <span className="muted">Sin clasificar</span>)}
                  </td>
                  <td data-label="Gama">
                    {puedeClasificar ? (
                      <select
                        aria-label={`Gama de ${f.codigo}`}
                        value={f.gama ?? ""}
                        disabled={ocupado}
                        onChange={(e) => void hacer(
                          () => clasificarHistoricoFamilia(f.codigo, { gama: e.target.value || null }),
                          "No se ha podido guardar la gama.")}
                      >
                        <option value="">Sin gama</option>
                        {gamas.map((g) => <option key={g.codigo} value={g.codigo}>{g.nombre}</option>)}
                      </select>
                    ) : (f.gama ? nombreGama(f.gama) : <span className="muted">Sin gama</span>)}
                  </td>
                  <td data-label="Propuesta">
                    {marca ? (
                      <>
                        <span className={`badge ${marca.tono}`}>{marca.texto}</span>{" "}
                        {textoPropuesta(f)}
                        {puedeClasificar && p
                          && (p.estado === "revisar" || p.estado === "propuesta" || p.estado === "descartada") ? (
                          <span className="erp-historico-acciones">
                            <button
                              type="button"
                              className="button small secondary"
                              disabled={ocupado}
                              aria-label={`Aceptar la propuesta de ${f.codigo}`}
                              onClick={() => void hacer(() => aceptarPropuestaFamilia(f.codigo),
                                "No se ha podido aceptar la propuesta.")}
                            >
                              Aceptar
                            </button>
                            {p.estado === "revisar" ? (
                              <button
                                type="button"
                                className="button small secondary"
                                disabled={ocupado}
                                aria-label={`Mantener la clasificación de ${f.codigo}`}
                                onClick={() => void hacer(
                                  () => mantenerClasificacionFamilia(f.codigo),
                                  "No se ha podido guardar.")}
                              >
                                Mantener la mía
                              </button>
                            ) : null}
                          </span>
                        ) : null}
                      </>
                    ) : null}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      {puedeClasificar ? (
        <form
          className="erp-historico-acciones erp-historico-nueva-gama"
          onSubmit={(e) => {
            e.preventDefault();
            const nombre = nuevaGama.trim();
            if (!nombre) return;
            void hacer(async () => { await crearHistoricoGama(nombre); setNuevaGama(""); },
              "No se ha podido añadir la gama.", `Gama «${nombre}» añadida.`);
          }}
        >
          <label className="field">
            <span>Nueva gama</span>
            <input
              value={nuevaGama}
              maxLength={64}
              placeholder="p. ej. Corte por agua"
              onChange={(e) => setNuevaGama(e.target.value)}
            />
          </label>
          <button type="submit" className="button small secondary" disabled={ocupado || !nuevaGama.trim()}>
            Añadir gama
          </button>
        </form>
      ) : null}
      <MaquinasPanel puedeEditar={puedeClasificar} />
      <SeriesVentaPanel puedeEditar={puedeClasificar} />
      <ArticulosClasePanel puedeEditar={puedeClasificar} />
      <ReglasPanel gamas={gamas} puedeEditar={puedeClasificar} onCambio={onRecargar} />
    </section>
  );
}

function ReglasPanel({
  gamas, puedeEditar, onCambio,
}: { gamas: HistoricoGama[]; puedeEditar: boolean; onCambio: () => Promise<void> }) {
  const [reglas, setReglas] = useState<HistoricoRegla[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [campo, setCampo] = useState<"codigo" | "nombre">("codigo");
  const [patron, setPatron] = useState("");
  const [clase, setClase] = useState("");
  const [gama, setGama] = useState("");
  const nombreGama = (c: string | null) => gamas.find((g) => g.codigo === c)?.nombre ?? c ?? "—";

  async function leer() {
    try {
      setReglas((await getHistoricoReglas()).items);
    } catch (err) {
      setError(extractErrorMessage(err, "No se han podido leer las reglas."));
    }
  }

  async function cambiar(accion: () => Promise<unknown>, fallo: string) {
    setError(null);
    try {
      await accion();
      await Promise.all([leer(), onCambio()]);
    } catch (err) {
      setError(extractErrorMessage(err, fallo));
    }
  }

  return (
    <details
      className="erp-historico-reglas"
      onToggle={(e) => { if ((e.target as HTMLDetailsElement).open && !reglas) void leer(); }}
    >
      <summary>Reglas que proponen clase y gama</summary>
      <p className="form-hint">
        Por el código exacto de la familia o porque su nombre contiene un texto. Gana la de
        menor orden. Proponen; no clasifican nada solas.
      </p>
      {error ? <p className="form-error" role="alert">{error}</p> : null}
      {reglas === null ? <p className="muted">Cargando…</p> : (
        <table className="data-table data-table--responsive">
          <thead>
            <tr><th>Si</th><th>Patrón</th><th>Clase</th><th>Gama</th><th className="num">Orden</th><th aria-label="Acciones" /></tr>
          </thead>
          <tbody>
            {reglas.map((r) => (
              <tr key={r.id}>
                <td data-label="Si">{r.campo === "codigo" ? "El código es" : "El nombre contiene"}</td>
                <td data-label="Patrón" className="mono">{r.patron}</td>
                <td data-label="Clase">{r.clase ? CLASE_LABEL[r.clase] : "—"}</td>
                <td data-label="Gama">{nombreGama(r.gama)}</td>
                <td data-label="Orden" className="num">{r.orden}</td>
                <td>
                  {puedeEditar ? (
                    <button
                      type="button"
                      className="button small secondary"
                      aria-label={`Quitar la regla ${r.patron}`}
                      onClick={() => void cambiar(() => borrarHistoricoRegla(r.id),
                        "No se ha podido quitar la regla.")}
                    >
                      Quitar
                    </button>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {puedeEditar ? (
        <form
          className="erp-historico-filtros"
          onSubmit={(e) => {
            e.preventDefault();
            void cambiar(async () => {
              await crearHistoricoRegla({
                campo, patron: patron.trim(), clase: (clase || null) as HistoricoClase | null,
                gama: gama || null, orden: 100,
              });
              setPatron("");
            }, "No se ha podido añadir la regla.");
          }}
        >
          <label className="field">
            <span>Si</span>
            <select value={campo} onChange={(e) => setCampo(e.target.value as "codigo" | "nombre")}>
              <option value="codigo">El código es</option>
              <option value="nombre">El nombre contiene</option>
            </select>
          </label>
          <label className="field">
            <span>Patrón</span>
            <input value={patron} maxLength={64} onChange={(e) => setPatron(e.target.value)} />
          </label>
          <label className="field">
            <span>Propone clase</span>
            <select value={clase} onChange={(e) => setClase(e.target.value)}>
              <option value="">—</option>
              {CLASES.map((c) => <option key={c} value={c}>{CLASE_LABEL[c]}</option>)}
            </select>
          </label>
          <label className="field">
            <span>Propone gama</span>
            <select value={gama} onChange={(e) => setGama(e.target.value)}>
              <option value="">—</option>
              {gamas.map((g) => <option key={g.codigo} value={g.codigo}>{g.nombre}</option>)}
            </select>
          </label>
          <div className="erp-historico-acciones">
            <button
              type="submit"
              className="button small secondary"
              disabled={!patron.trim() || !(clase || gama)}
            >
              Añadir regla
            </button>
          </div>
        </form>
      ) : null}
    </details>
  );
}

// --- Carga -------------------------------------------------------------------------------

function CargaPanel({
  estado, puedeCargar, onLanzada,
}: {
  estado: HistoricoEstadoCarga | null;
  puedeCargar: boolean;
  onLanzada: () => Promise<void>;
}) {
  const [error, setError] = useState<string | null>(null);
  const [lanzando, setLanzando] = useState(false);
  if (!estado) return <section className="erp-card"><p className="muted">Cargando…</p></section>;
  const enMarcha = estado.ejercicios.some((e) => e.estado === "cargando");
  const { notas } = estado;

  async function lanzar(ejercicios: string[]) {
    setLanzando(true);
    setError(null);
    try {
      await cargarHistorico(ejercicios);
      await onLanzada();
    } catch (err) {
      setError(extractErrorMessage(err, "No se ha podido lanzar la carga."));
    } finally {
      setLanzando(false);
    }
  }

  return (
    <section className="erp-card">
      <p className="muted">
        BoHub guarda las facturas y los albaranes de cada ejercicio, con sus líneas y el catálogo
        de ese año. El ejercicio en curso ({estado.en_curso}) se recarga solo cada día a las 05:20;
        los anteriores, cuando se lanzan aquí. Solo se lee FACTUSOL.
      </p>
      {notas.lineas > 0 ? (
        <p role="status">
          De {nf.format(notas.lineas)} líneas volcadas, {nf.format(notas.producto)} son de
          producto y <strong>{nf.format(notas.notas)} son notas</strong> (sin artículo: portes,
          condiciones, garantías…) y no cuentan como venta.
        </p>
      ) : null}
      {puedeCargar ? (
        <p>
          <button
            type="button"
            className="button small"
            disabled={enMarcha || lanzando}
            onClick={() => void lanzar([])}
          >
            Cargar los {estado.ejercicios.length} ejercicios
          </button>
          {enMarcha ? <span className="muted"> Hay una carga en marcha…</span> : null}
        </p>
      ) : null}
      {error ? <p className="form-error" role="alert">{error}</p> : null}
      <table className="data-table data-table--responsive">
        <thead>
          <tr>
            <th>Ejercicio</th>
            <th>Estado</th>
            <th>Última carga</th>
            <th className="num">Facturas</th>
            <th className="num">Albaranes</th>
            <th className="num">Líneas</th>
            <th className="num">De nota</th>
            <th className="num">Artículos</th>
            <th aria-label="Acciones" />
          </tr>
        </thead>
        <tbody>
          {estado.ejercicios.map((e) => (
            <tr key={e.ejercicio}>
              <td data-label="Ejercicio" className="mono">{e.ejercicio}</td>
              <td data-label="Estado">
                <span className={`badge ${ESTADO_TONE[e.estado]}`}>{ESTADO_LABEL[e.estado]}</span>
                {e.detalle ? <div className="form-hint">{e.detalle}</div> : null}
              </td>
              <td data-label="Última carga">{fechaHora(e.cargado_at)}</td>
              <td data-label="Facturas" className="num">{nf.format(e.facturas)}</td>
              <td data-label="Albaranes" className="num">{nf.format(e.albaranes)}</td>
              <td data-label="Líneas" className="num">{nf.format(e.lineas)}</td>
              <td data-label="De nota" className="num">{nf.format(e.lineas_nota)}</td>
              <td data-label="Artículos" className="num">{nf.format(e.articulos)}</td>
              <td>
                {puedeCargar ? (
                  <button
                    type="button"
                    className="button small secondary"
                    disabled={enMarcha || lanzando}
                    onClick={() => void lanzar([e.ejercicio])}
                  >
                    {e.estado === "sin_cargar" ? "Cargar" : "Volver a cargar"}
                  </button>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
