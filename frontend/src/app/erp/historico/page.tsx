"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { PageHeader } from "../../components/PageHeader";
import { getCurrentUser, type User } from "../../lib/api";
import { Cap, can } from "../../lib/capabilities";
import {
  buscarHistoricoArticulos,
  cargarHistorico,
  clasificarHistoricoFamilia,
  downloadHistoricoCompradoresCsv,
  getHistoricoCompradores,
  getHistoricoEstado,
  getHistoricoFamilias,
  saveBlob,
  type HistoricoArticulo,
  type HistoricoClase,
  type HistoricoComprador,
  type HistoricoEstado,
  type HistoricoEstadoCarga,
  type HistoricoFamilia,
  type HistoricoFiltro,
} from "../../lib/erpApi";
import { extractErrorMessage } from "../../lib/errors";

/** ERP · Quién compró qué — el histórico de ventas de FACTUSOL (2014 → hoy)
 *  volcado en BoHub. Se consulta lo volcado, nunca FACTUSOL en vivo; la carga
 *  solo LEE FACTUSOL. */

type Tab = "compradores" | "familias" | "carga";

const CLASE_LABEL: Record<HistoricoClase, string> = {
  maquina: "Máquina", consumible: "Consumible", otro: "Otro",
};
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
  const [error, setError] = useState<string | null>(null);

  const cargarEstado = useCallback(async () => {
    try {
      setEstado(await getHistoricoEstado());
    } catch (err) {
      setError(extractErrorMessage(err, "No se ha podido leer el estado de la carga."));
    }
  }, []);
  const cargarFamilias = useCallback(async () => {
    try {
      setFamilias((await getHistoricoFamilias()).items);
    } catch (err) {
      setError(extractErrorMessage(err, "No se han podido leer las familias."));
    }
  }, []);

  useEffect(() => {
    getCurrentUser().then(setUsuario).catch(() => undefined);
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
        <CompradoresPanel enCurso={estado?.en_curso ?? null} familias={familias} />
      ) : null}
      {tab === "familias" ? (
        <FamiliasPanel
          familias={familias}
          puedeClasificar={can(usuario, Cap.CONFIG)}
          onCambio={(f) => setFamilias((prev) => prev.map((x) => (x.codigo === f.codigo ? f : x)))}
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
  enCurso, familias,
}: { enCurso: string | null; familias: HistoricoFamilia[] }) {
  const [texto, setTexto] = useState("");
  const [articulo, setArticulo] = useState<HistoricoArticulo | null>(null);
  const [sugerencias, setSugerencias] = useState<HistoricoArticulo[]>([]);
  // Se cierran al buscar (aunque llegue tarde la respuesta) y se abren al escribir.
  const [sugerenciasAbiertas, setSugerenciasAbiertas] = useState(true);
  const [familia, setFamilia] = useState("");
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
    desde: desde ? Number(desde) : undefined,
    hasta: hasta ? Number(hasta) : undefined,
  };
  const hayCriterio = Boolean(filtro.codart || filtro.q || filtro.familia);

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
              ? "Nadie lo ha comprado en esos años (solo cuentan las líneas con artículo)."
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
            Las unidades salen de las facturas; si un cliente solo tiene albaranes sin facturar,
            de los albaranes. Las líneas de nota (sin artículo) no cuentan.
          </p>
        </>
      ) : null}
    </section>
  );
}

// --- Familias ----------------------------------------------------------------------------

function FamiliasPanel({
  familias, puedeClasificar, onCambio,
}: {
  familias: HistoricoFamilia[];
  puedeClasificar: boolean;
  onCambio: (f: HistoricoFamilia) => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const sinClase = familias.filter((f) => !f.clase).length;

  async function clasificar(f: HistoricoFamilia, valor: string) {
    setError(null);
    const clase = (valor || null) as HistoricoClase | null;
    try {
      const r = await clasificarHistoricoFamilia(f.codigo, clase);
      onCambio({ ...f, clase: r.clase });
    } catch (err) {
      setError(extractErrorMessage(err, "No se ha podido guardar la clase."));
    }
  }

  return (
    <section className="erp-card">
      <p className="muted">
        {nf.format(familias.length)} familias de FACTUSOL
        {sinClase ? ` · ${nf.format(sinClase)} sin clasificar` : " · todas clasificadas"}.
        {" "}La clase (máquina, consumible u otro) la decide oficina aquí; se conserva al recargar.
      </p>
      {error ? <p className="form-error" role="alert">{error}</p> : null}
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
            </tr>
          </thead>
          <tbody>
            {familias.map((f) => (
              <tr key={f.codigo}>
                <td data-label="Familia" className="mono">{f.codigo}</td>
                <td data-label="Descripción">{f.descripcion ?? "—"}</td>
                <td data-label="Artículos" className="num">{nf.format(f.articulos)}</td>
                <td data-label="Líneas vendidas" className="num">{nf.format(f.ventas)}</td>
                <td data-label="Clase">
                  {puedeClasificar ? (
                    <select
                      aria-label={`Clase de ${f.codigo}`}
                      value={f.clase ?? ""}
                      onChange={(e) => void clasificar(f, e.target.value)}
                    >
                      <option value="">Sin clasificar</option>
                      {(Object.keys(CLASE_LABEL) as HistoricoClase[]).map((c) => (
                        <option key={c} value={c}>{CLASE_LABEL[c]}</option>
                      ))}
                    </select>
                  ) : (f.clase ? CLASE_LABEL[f.clase] : <span className="muted">Sin clasificar</span>)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
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
