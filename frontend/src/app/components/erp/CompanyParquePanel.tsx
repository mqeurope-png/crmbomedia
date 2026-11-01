"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import {
  anadirMaquinaParque,
  borrarMaquinaParque,
  buscarMaquinasCatalogo,
  darBajaMaquina,
  deshacerBajaMaquina,
  fichaDocumentoHref,
  getComprasEmpresa,
  getHistoricoFamilias,
  getParqueEmpresa,
  HISTORICO_CLASE_LABEL,
  setClaseArticulo,
  type ComprasEmpresa,
  type HistoricoClase,
  type HistoricoGama,
  type MaquinaCatalogo,
  type ParqueEmpresa,
  type ParqueMaquina,
} from "../../lib/erpApi";
import { extractErrorMessage } from "../../lib/errors";

/** Parque instalado y compras de una empresa (ficha de empresa, 30/10/2026):
 *  qué máquinas tiene (de sus facturas y albaranes desde 2014, más las
 *  añadidas a mano) y todos sus documentos. Lee lo volcado de FACTUSOL. */

const NF = new Intl.NumberFormat("es-ES", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const ORIGEN: Record<ParqueMaquina["origen"], string> = {
  factura: "Factura", albaran: "Albarán", manual: "Añadida a mano",
};

function fecha(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(`${iso.slice(0, 10)}T00:00:00`);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString("es-ES");
}

function claveDe(m: ParqueMaquina): string {
  return m.clave ?? `manual-${m.id}`;
}

/** De dónde sale, con la diferencia entre la del catálogo y la escrita a mano. */
function origenDe(m: ParqueMaquina): string {
  if (m.origen !== "manual") return ORIGEN[m.origen];
  return m.del_catalogo ? "Añadida a mano · del catálogo" : "Escrita a mano";
}

type Nueva = {
  /** Lo escrito: lo que se busca en el catálogo y, si no se elige nada, la máquina. */
  texto: string;
  /** La elegida del catálogo (entonces la gama es la de su familia). */
  elegida: MaquinaCatalogo | null;
  gama: string;
  fecha: string;
  nota: string;
};

const NUEVA: Nueva = { texto: "", elegida: null, gama: "", fecha: "", nota: "" };

const compacto = (t: string) => t.toLowerCase().replace(/[^a-z0-9]/g, "");

/** La del catálogo cuyo código (o uno equivalente) es exactamente lo escrito:
 *  escribir «A3000U» y pulsar Añadir sin pinchar la sugerencia la vincula igual. */
function porCodigoExacto(texto: string, encontradas: MaquinaCatalogo[] | null): MaquinaCatalogo | null {
  const t = compacto(texto);
  if (!t || !encontradas) return null;
  return encontradas.find((m) => [m.codart, ...m.equivalentes].some((c) => compacto(c) === t)) ?? null;
}

/** Lo que puede ser algo que no es una máquina. */
const NO_MAQUINA = (Object.keys(HISTORICO_CLASE_LABEL) as HistoricoClase[]).filter((c) => c !== "maquina");

export function CompanyParquePanel({
  companyId, puedeEditar, puedeClasificar = false,
}: { companyId: string; puedeEditar: boolean; puedeClasificar?: boolean }) {
  const [parque, setParque] = useState<ParqueEmpresa | null>(null);
  const [compras, setCompras] = useState<ComprasEmpresa | null>(null);
  const [anio, setAnio] = useState<string | undefined>(undefined);
  const [gamas, setGamas] = useState<HistoricoGama[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [errorCompras, setErrorCompras] = useState<string | null>(null);
  const [ocupado, setOcupado] = useState(false);
  const [bajaDe, setBajaDe] = useState<string | null>(null);
  const [motivo, setMotivo] = useState("");
  const [noMaquina, setNoMaquina] = useState<{ codart: string; clase: HistoricoClase } | null>(null);
  const [nueva, setNueva] = useState<Nueva | null>(null);
  // Lo que devolvió el catálogo, con el texto que se buscó: solo vale si es
  // el que hay escrito ahora.
  const [busqueda, setBusqueda] = useState<
    { texto: string; items: MaquinaCatalogo[] | null; error: boolean } | null>(null);
  const textoBuscado = nueva && !nueva.elegida ? nueva.texto.trim() : "";
  const vigente = textoBuscado.length >= 2 && busqueda?.texto === textoBuscado ? busqueda : null;
  const encontradas = vigente?.items ?? null;
  const errorBusqueda = Boolean(vigente?.error);

  // El buscador del catálogo: a partir de dos letras, un momento después de
  // dejar de escribir.
  useEffect(() => {
    if (textoBuscado.length < 2) return;
    let vivo = true;
    const t = setTimeout(() => {
      buscarMaquinasCatalogo(textoBuscado)
        .then((r) => { if (vivo) setBusqueda({ texto: textoBuscado, items: r, error: false }); })
        // Un fallo no es «no está en el catálogo»: se dice aparte.
        .catch(() => { if (vivo) setBusqueda({ texto: textoBuscado, items: null, error: true }); });
    }, 250);
    return () => { vivo = false; clearTimeout(t); };
  }, [textoBuscado]);

  const leerParque = useCallback(async () => {
    try {
      setParque(await getParqueEmpresa(companyId));
    } catch (err) {
      setError(extractErrorMessage(err, "No se ha podido leer el parque."));
    }
  }, [companyId]);

  useEffect(() => {
    void leerParque();
    getHistoricoFamilias().then((r) => setGamas(r.gamas)).catch(() => undefined);
  }, [leerParque]);

  useEffect(() => {
    let vivo = true;
    getComprasEmpresa(companyId, anio)
      .then((r) => { if (vivo) { setCompras(r); setErrorCompras(null); } })
      .catch((err) => {
        if (!vivo) return;
        // Sin dejar a la vista las compras del año anterior como si fueran de este.
        setCompras(null);
        setErrorCompras(extractErrorMessage(err, "No se han podido leer las compras."));
      });
    return () => { vivo = false; };
  }, [companyId, anio]);

  /** true si ha ido bien (el formulario de alta solo se cierra entonces). */
  async function hacer(accion: () => Promise<unknown>, fallo: string): Promise<boolean> {
    setError(null);
    setOcupado(true);
    try {
      const r = await accion();
      if (r && typeof r === "object" && "maquinas" in r) setParque(r as ParqueEmpresa);
      else await leerParque();
      setBajaDe(null);
      setMotivo("");
      return true;
    } catch (err) {
      setError(extractErrorMessage(err, fallo));
      return false;
    } finally {
      setOcupado(false);
    }
  }

  const ref = (m: ParqueMaquina) => (m.clave ? { clave: m.clave } : { maquina_id: m.id });

  return (
    <>
      <section className="erp-card" aria-label="Máquinas">
        <h3>Máquinas {parque ? <span className="muted">· {parque.activas} en servicio</span> : null}</h3>
        {error ? <p className="form-error" role="alert">{error}</p> : null}
        {parque?.sin_cliente_factusol ? (
          <p className="form-error" role="status">
            Esta empresa no tiene cliente de FACTUSOL vinculado: no se sabe qué ha comprado.
            Vincúlala para ver su parque (una lista vacía no querría decir que no tenga máquinas).
          </p>
        ) : null}
        {parque === null ? <p className="muted">Cargando…</p> : parque.maquinas.length === 0 ? (
          <p className="muted">
            {parque.sin_cliente_factusol ? "Ninguna añadida a mano."
              : "No sale ninguna máquina en sus ventas desde 2014."}
          </p>
        ) : (
          <table className="data-table data-table--responsive">
            <thead>
              <tr>
                <th>Máquina</th><th>Gama</th><th>Fecha</th><th>De dónde sale</th><th>Estado</th>
                {puedeEditar || puedeClasificar ? <th aria-label="Acciones" /> : null}
              </tr>
            </thead>
            <tbody>
              {parque.maquinas.map((m) => (
                <tr key={claveDe(m)} className={m.baja ? "muted" : undefined}>
                  <td data-label="Máquina">
                    {m.articulo ?? "—"}
                    {m.codart ? <span className="mono muted"> {m.codart}</span> : null}
                    {m.cantidad > 1 ? <span className="muted"> · {m.cantidad} uds.</span> : null}
                  </td>
                  <td data-label="Gama">{m.gama_nombre ?? <span className="muted">Sin gama</span>}</td>
                  <td data-label="Fecha">
                    {m.fecha ? fecha(m.fecha) : "—"}
                    {m.ejercicio ? <span className="muted"> · {m.ejercicio}</span> : null}
                  </td>
                  <td data-label="De dónde sale">
                    {m.documento ? (
                      <Link href={fichaDocumentoHref(m.documento.tipo, m.ejercicio ?? "", m.documento.serie,
                                                     m.documento.codigo)} prefetch={false}>
                        {ORIGEN[m.origen]} <span className="mono">{m.documento.numero}</span>
                      </Link>
                    ) : (
                      <span className={`badge ${m.origen === "manual" && !m.del_catalogo ? "warn" : "muted"}`}
                            title={m.origen === "manual" && !m.del_catalogo
                              ? "No es un artículo del catálogo: cuenta con la gama que se le puso."
                              : undefined}>
                        {origenDe(m)}
                      </span>
                    )}
                    {m.albaranes?.map((a) => (
                      <div key={`${a.ejercicio}-${a.numero}`} className="muted">
                        y su{" "}
                        <Link href={fichaDocumentoHref(a.tipo, a.ejercicio, a.serie, a.codigo)} prefetch={false}>
                          albarán <span className="mono">{a.numero}</span>
                        </Link>
                      </div>
                    ))}
                    {!m.baja ? m.mismo_modelo?.map((o) => (
                      <div key={`mm-${o.codart}-${o.documento?.numero ?? ""}`}>
                        <span className="badge warn"
                              title="Mismo modelo con otro código de artículo (el de FACTUSOL y el de la web). Si es la misma máquina, da de baja una de las dos.">
                          ¿La misma máquina que{" "}
                          {o.documento ? (
                            <Link href={fichaDocumentoHref(o.documento.tipo, o.ejercicio ?? "", o.documento.serie,
                                                           o.documento.codigo)} prefetch={false}>
                              <span className="mono">{o.codart}</span>
                            </Link>
                          ) : <span className="mono">{o.codart}</span>}
                          {o.articulo ? ` (${o.articulo})` : ""}?
                        </span>
                      </div>
                    )) : null}
                    {m.posible_misma_venta && !m.baja ? (
                      <div>
                        <span className="badge warn"
                              title="Sin enlace en FACTUSOL entre los dos: puede ser la misma venta o dos compras. Si es la misma, da de baja este albarán.">
                          ¿Misma venta que la{" "}
                          <Link href={fichaDocumentoHref(m.posible_misma_venta.tipo, m.posible_misma_venta.ejercicio,
                                                         m.posible_misma_venta.serie, m.posible_misma_venta.codigo)}
                                prefetch={false}>
                            factura <span className="mono">{m.posible_misma_venta.numero}</span>
                          </Link>?
                        </span>
                      </div>
                    ) : null}
                  </td>
                  <td data-label="Estado">
                    {m.baja ? (
                      <span className="badge warn" title={m.baja.motivo ?? undefined}>
                        Dada de baja{m.baja.motivo ? `: ${m.baja.motivo}` : ""}
                      </span>
                    ) : m.no_cuenta ? (
                      <span className="badge warn"
                            title="Su artículo ya no está marcado como máquina: como una de factura, no cuenta en el parque. Si es una máquina, quítala y añádela escrita a mano.">
                        No cuenta: su artículo ya no es una máquina
                      </span>
                    ) : <span className="badge ok">En servicio</span>}
                  </td>
                  {puedeEditar || puedeClasificar ? (
                    <td>
                      {noMaquina && m.codart === noMaquina.codart && m.origen !== "manual" ? (
                        <span className="erp-historico-acciones">
                          <label className="field">
                            <span>Es un</span>
                            <select value={noMaquina.clase}
                                    onChange={(e) => setNoMaquina({ ...noMaquina, clase: e.target.value as HistoricoClase })}>
                              {NO_MAQUINA.map((c) => <option key={c} value={c}>{HISTORICO_CLASE_LABEL[c]}</option>)}
                            </select>
                          </label>
                          <span className="form-hint">
                            Sale del parque de todos los clientes que lo tengan. Se deshace en Histórico → Familias → «Máquinas, artículo a artículo», marcando su casilla.
                          </span>
                          <button type="button" className="button small" disabled={ocupado}
                                  onClick={() => void hacer(() => setClaseArticulo(noMaquina.codart, noMaquina.clase),
                                    "No se ha podido cambiar la clase del artículo.").then((ok) => { if (ok) setNoMaquina(null); })}>
                            Confirmar
                          </button>
                          <button type="button" className="button small secondary" onClick={() => setNoMaquina(null)}>
                            Cancelar
                          </button>
                        </span>
                      ) : !puedeEditar ? (
                        m.codart && m.origen !== "manual" ? (
                          <button type="button" className="button small secondary" disabled={ocupado}
                                  onClick={() => setNoMaquina({ codart: m.codart as string, clase: "accesorio" })}>
                            No es una máquina
                          </button>
                        ) : null
                      ) : bajaDe === claveDe(m) ? (
                        <span className="erp-historico-acciones">
                          <input aria-label="Motivo de la baja" placeholder="Motivo (opcional)" value={motivo}
                                 maxLength={255} onChange={(e) => setMotivo(e.target.value)} />
                          <button type="button" className="button small" disabled={ocupado}
                                  onClick={() => void hacer(() => darBajaMaquina(companyId, ref(m), motivo || undefined),
                                    "No se ha podido dar de baja.")}>
                            Confirmar baja
                          </button>
                          <button type="button" className="button small secondary" onClick={() => setBajaDe(null)}>
                            Cancelar
                          </button>
                        </span>
                      ) : m.baja ? (
                        <span className="erp-historico-acciones">
                          <button type="button" className="button small secondary" disabled={ocupado}
                                  onClick={() => void hacer(() => deshacerBajaMaquina(companyId, ref(m)),
                                    "No se ha podido deshacer la baja.")}>
                            Volver a poner en servicio
                          </button>
                          {puedeClasificar && m.codart && m.origen !== "manual" ? (
                            <button type="button" className="button small secondary" disabled={ocupado}
                                    onClick={() => setNoMaquina({ codart: m.codart as string, clase: "accesorio" })}>
                              No es una máquina
                            </button>
                          ) : null}
                        </span>
                      ) : (
                        <span className="erp-historico-acciones">
                          <button type="button" className="button small secondary" disabled={ocupado}
                                  onClick={() => { setBajaDe(claveDe(m)); setMotivo(""); }}>
                            Dar de baja
                          </button>
                          {puedeClasificar && m.codart && m.origen !== "manual" ? (
                            <button type="button" className="button small secondary" disabled={ocupado}
                                    onClick={() => setNoMaquina({ codart: m.codart as string, clase: "accesorio" })}>
                              No es una máquina
                            </button>
                          ) : null}
                          {m.origen === "manual" && m.id ? (
                            <button type="button" className="button small secondary" disabled={ocupado}
                                    onClick={() => void hacer(() => borrarMaquinaParque(companyId, m.id as string),
                                      "No se ha podido quitar.")}>
                              Quitar
                            </button>
                          ) : null}
                        </span>
                      )}
                    </td>
                  ) : null}
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <p className="form-hint">
          Sale de sus facturas y albaranes desde 2014 (las familias de clase «Máquina»). Lo vendido
          antes, como línea de nota o en otro sitio no sale: añádelo a mano, buscándolo en el
          catálogo (cuenta como una de factura) o, si no está, escrito tal cual. Lo que ya no está
          en servicio, dalo de baja.
        </p>
        {puedeEditar ? (nueva ? (
          <form className="erp-historico-filtros" onSubmit={(e) => {
            e.preventDefault();
            const elegida = nueva.elegida ?? porCodigoExacto(nueva.texto, encontradas);
            const datos = elegida
              // La gama, la de su familia; la elegida aquí solo si su familia no tiene.
              ? { codart: elegida.codart, descripcion: elegida.descripcion,
                  gama: elegida.gama ? undefined : nueva.gama || undefined }
              : { descripcion: nueva.texto.trim(), gama: nueva.gama || undefined };
            void hacer(() => anadirMaquinaParque(companyId, {
              ...datos, fecha: nueva.fecha || undefined, nota: nueva.nota.trim() || undefined,
            }), "No se ha podido añadir la máquina.").then((ok) => { if (ok) setNueva(null); });
          }}>
            {nueva.elegida ? (
              <div className="field">
                <span>Máquina</span>
                <p role="status" aria-label="Máquina elegida" style={{ margin: 0 }}>
                  <span className="badge ok">Del catálogo</span> {nueva.elegida.descripcion}{" "}
                  <span className="mono muted">{nueva.elegida.codart}</span>{" "}
                  <button type="button" className="button small secondary"
                          onClick={() => setNueva({ ...nueva, elegida: null, gama: "" })}>
                    Cambiar
                  </button>
                </p>
              </div>
            ) : (
              <div className="field">
                <label htmlFor={`maquina-${companyId}`} className="field-label">Máquina</label>
                <input id={`maquina-${companyId}`} value={nueva.texto} maxLength={255} required
                       placeholder="Código o nombre (p. ej. A3000U)" autoComplete="off"
                       onChange={(e) => setNueva({ ...nueva, texto: e.target.value })} />
                {encontradas && encontradas.length > 0 ? (
                  <ul className="erp-maquinas-catalogo" aria-label="Máquinas del catálogo">
                    {encontradas.map((m) => (
                      <li key={m.codart}>
                        <button type="button" className="button small secondary"
                                onClick={() => setNueva({ ...nueva, elegida: m, gama: m.gama ?? "" })}>
                          <span>
                            {m.descripcion} <span className="mono">{m.codart}</span>
                            {" · "}{m.gama_nombre ?? "Sin gama"}
                          </span>
                        </button>
                      </li>
                    ))}
                  </ul>
                ) : null}
                {errorBusqueda ? (
                  <span className="form-error" role="alert">
                    No se ha podido buscar en el catálogo. Vuelve a probar antes de añadirla escrita a mano.
                  </span>
                ) : textoBuscado.length >= 2 && encontradas !== null ? (
                  <span className="form-hint">
                    {encontradas.length === 0
                      ? `Ninguna máquina del catálogo casa con «${textoBuscado}»: se añadirá escrita a mano.`
                      : porCodigoExacto(nueva.texto, encontradas)
                        ? "Es el código de una del catálogo: se añadirá vinculada a ella."
                        : "Elige una del catálogo o, si no está, se añadirá escrita a mano tal cual."}
                  </span>
                ) : null}
              </div>
            )}
            <label className="field"><span>Gama</span>
              {nueva.elegida && !nueva.elegida.gama ? (
                <span className="form-hint">
                  Su familia no tiene gama: elígela aquí o, mejor, dásela a la familia.
                </span>
              ) : null}
              <select value={nueva.gama} disabled={Boolean(nueva.elegida?.gama)}
                      title={nueva.elegida?.gama ? "La de la familia del artículo" : undefined}
                      onChange={(e) => setNueva({ ...nueva, gama: e.target.value })}>
                <option value="">Sin gama</option>
                {gamas.map((g) => <option key={g.codigo} value={g.codigo}>{g.nombre}</option>)}
                {nueva.elegida?.gama && !gamas.some((g) => g.codigo === nueva.elegida?.gama) ? (
                  <option value={nueva.elegida.gama}>{nueva.elegida.gama_nombre ?? nueva.elegida.gama}</option>
                ) : null}
              </select>
            </label>
            <label className="field"><span>Fecha (aprox.)</span>
              <input type="date" value={nueva.fecha} onChange={(e) => setNueva({ ...nueva, fecha: e.target.value })} />
            </label>
            <label className="field"><span>Nota</span>
              <input value={nueva.nota} maxLength={255} placeholder="p. ej. comprada a otro distribuidor"
                     onChange={(e) => setNueva({ ...nueva, nota: e.target.value })} />
            </label>
            <div className="erp-historico-acciones">
              <button type="submit" className="button small"
                      disabled={ocupado || !(nueva.elegida || nueva.texto.trim())}>
                Añadir
              </button>
              <button type="button" className="button small secondary" onClick={() => setNueva(null)}>Cancelar</button>
            </div>
          </form>
        ) : (
          <button type="button" className="button small secondary" onClick={() => setNueva({ ...NUEVA })}>
            Añadir una máquina a mano
          </button>
        )) : null}
      </section>

      <section className="erp-card" aria-label="Qué ha comprado">
        <h3>Qué ha comprado</h3>
        {errorCompras ? (
          <p className="form-error" role="alert">
            {errorCompras}{" "}
            {anio !== undefined ? (
              <button type="button" className="button small secondary" onClick={() => setAnio(undefined)}>
                Volver al ejercicio en curso
              </button>
            ) : null}
          </p>
        ) : compras?.sin_cliente_factusol ? (
          <p className="muted">Sin cliente de FACTUSOL vinculado.</p>
        ) : compras ? (
          <>
            <label className="field erp-compras-anio">
              <span>Ejercicio</span>
              <select value={compras.ejercicio} onChange={(e) => setAnio(e.target.value)}>
                {[...new Set([compras.en_curso, ...compras.ejercicios])].map((a) => (
                  <option key={a} value={a}>{a}{a === compras.en_curso ? " (en curso)" : ""}</option>
                ))}
                <option value="todos">Todo el histórico (2014 → hoy)</option>
              </select>
            </label>
            {compras.ejercicio !== "todos" ? (
              <button type="button" className="button small secondary" onClick={() => setAnio("todos")}>
                Ver todo el histórico
              </button>
            ) : null}
            {compras.documentos.length === 0 ? (
              <p className="muted">Nada en {compras.ejercicio === "todos" ? "el histórico" : compras.ejercicio}.</p>
            ) : (
              <table className="data-table data-table--responsive">
                <thead>
                  <tr><th>Documento</th><th>Año</th><th>Fecha</th><th className="num">Total</th><th>Estado</th></tr>
                </thead>
                <tbody>
                  {compras.documentos.map((d) => (
                    <tr key={`${d.tipo}-${d.ejercicio}-${d.serie}-${d.codigo}`}>
                      <td data-label="Documento">
                        <Link href={fichaDocumentoHref(d.tipo, d.ejercicio, d.serie, d.codigo)} prefetch={false}>
                          {d.tipo_label} <span className="mono">{d.numero}</span>
                        </Link>
                      </td>
                      <td data-label="Año"><span className="badge muted">{d.ejercicio}</span></td>
                      <td data-label="Fecha">{d.fecha ? fecha(d.fecha) : "fecha no válida"}</td>
                      <td data-label="Total" className="num">{d.total == null ? "—" : `${NF.format(d.total)} €`}</td>
                      <td data-label="Estado">{d.estado_label ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </>
        ) : <p className="muted">Cargando…</p>}
      </section>
    </>
  );
}
