"use client";

import { useEffect, useMemo, useState } from "react";
import {
  aceptarPropuestasMaquinas,
  borrarPistaArticulo,
  cambiarUmbralMaquinas,
  crearPistaArticulo,
  darPorRevisadasFamilia,
  deshacerRevisadasFamilia,
  getHistoricoMaquinas,
  marcarHistoricoMaquina,
  type MaquinaArticulo,
  type MaquinasResumen,
} from "../../lib/erpApi";
import { extractErrorMessage } from "../../lib/errors";

/** Las máquinas, artículo a artículo (31/10/2026). Una familia de clase
 *  «Máquina» ya no mete a todos sus artículos en el parque: marca cuáles hay
 *  que mirar, y aquí se marca cuáles son máquinas. BoHub propone y Bart
 *  confirma. La propuesta EXIGE evidencia positiva: «máquina» solo si el
 *  nombre lo dice; con pistas a favor y en contra, dudosa; sin pistas, nada.
 *  «Aceptar todas» solo aplica lo claro a lo que nadie ha revisado. */

const EUR = new Intl.NumberFormat("es-ES", { style: "currency", currency: "EUR", maximumFractionDigits: 0 });

type Vista = "todas" | "sin_revisar" | "colisiones";
type Orden = "familia" | "precio_desc" | "precio_asc";

function Propuesta({ i }: { i: MaquinaArticulo }) {
  // Lo revisado (marcado o no) es decisión de Bart: sin propuesta.
  if (i.estado !== "sin_revisar") return <span className="muted small">revisada</span>;
  if (i.propuesta === "dudoso" && i.por_precio) {
    return <span className="badge warn" title="Sin pista en el texto; su precio máximo supera el umbral">
      dudosa · precio</span>;
  }
  if (i.propuesta === "maquina") return <span className="badge ok">máquina · {i.pista_si}</span>;
  if (i.propuesta === "no_maquina") return <span className="badge muted">no · {i.pista_no}</span>;
  if (i.propuesta === "dudoso") {
    return <span className="badge warn" title="Hay pista a favor y en contra: decídelo tú">
      dudosa · {i.pista_si} / {i.pista_no}</span>;
  }
  return <span className="muted small">sin propuesta</span>;
}

const ESTADO: Record<MaquinaArticulo["estado"], string> = {
  maquina: "Máquina", no_maquina: "No es máquina", sin_revisar: "Sin revisar",
};

export function MaquinasPanel({ puedeEditar }: { puedeEditar: boolean }) {
  const [datos, setDatos] = useState<MaquinasResumen | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ocupado, setOcupado] = useState(false);
  const [familia, setFamilia] = useState("");
  const [vista, setVista] = useState<Vista>("todas");
  const [orden, setOrden] = useState<Orden>("familia");
  const [umbral, setUmbral] = useState("");
  const [confirmarLote, setConfirmarLote] = useState(false);
  const [pista, setPista] = useState("");
  const [tipoPista, setTipoPista] = useState<"maquina" | "no_maquina">("maquina");
  const [aviso, setAviso] = useState<string | null>(null);

  useEffect(() => {
    getHistoricoMaquinas().then(setDatos)
      .catch((e) => setError(extractErrorMessage(e, "No se han podido leer los artículos.")));
  }, []);

  async function hacer(accion: () => Promise<MaquinasResumen>, fallo: string) {
    setError(null);
    setAviso(null);
    setOcupado(true);
    try {
      const r = await accion();
      setDatos(r);
      if (r.aplicadas) {
        setAviso(`Aplicadas: ${r.aplicadas.maquina} como máquina y ${r.aplicadas.no_maquina} como no máquina.`);
      }
      if (r.revisadas !== undefined) setAviso(`${r.revisadas} dadas por revisadas como «no es máquina».`);
      if (r.deshechas !== undefined) setAviso(`${r.deshechas} vuelven a «sin revisar».`);
      return true;
    } catch (e) {
      setError(extractErrorMessage(e, fallo));
      return false;
    } finally {
      setOcupado(false);
    }
  }

  const familias = useMemo(() => {
    const m = new Map<string, string>();
    for (const i of datos?.items ?? []) if (i.familia) m.set(i.familia, i.familia_nombre ?? "");
    return [...m.entries()];
  }, [datos]);
  const filas = (datos?.items ?? []).filter((i) =>
    (!familia || i.familia === familia)
    && (vista !== "sin_revisar" || i.estado === "sin_revisar")
    && (vista !== "colisiones" || !!i.propondria));
  if (orden !== "familia") {
    const signo = orden === "precio_desc" ? -1 : 1;
    filas.sort((a, b) => {
      // Sin precio, al final en los dos sentidos.
      if (a.precio_max == null) return b.precio_max == null ? 0 : 1;
      if (b.precio_max == null) return -1;
      return signo * (a.precio_max - b.precio_max);
    });
  }
  const deLaFamilia = (datos?.items ?? []).filter((i) => familia && i.familia === familia);
  const quedan = deLaFamilia.filter((i) => i.estado === "sin_revisar").length;
  const enLote = deLaFamilia.filter((i) => i.en_lote).length;
  const ordenarPorPrecio = () => setOrden(orden === "precio_desc" ? "precio_asc" : "precio_desc");

  return (
    <section className="erp-card" aria-label="Máquinas por artículo">
      <h3>Máquinas, artículo a artículo</h3>
      <p className="form-hint">
        Un artículo solo entra en el parque si está marcado como máquina: que su familia sea de
        clase «Máquina» ya no basta, solo dice que hay que mirarlo. BoHub propone por la
        descripción; la casilla la marcas tú. Lo que no esté marcado se queda fuera.
      </p>
      {error ? <p className="form-error" role="alert">{error}</p> : null}
      {aviso ? <p className="form-success" role="status">{aviso}</p> : null}
      {datos === null ? <p className="muted">Cargando…</p> : (
        <>
          <p className="erp-maquinas-recuento" role="status">
            <strong>{datos.marcadas} de {datos.total}</strong> marcadas como máquina
            {datos.sin_revisar > 0 ? <span className="muted"> · {datos.sin_revisar} sin revisar</span> : null}
          </p>
          <div className="erp-historico-acciones">
            <label className="field">
              <span>Familia</span>
              <select value={familia} onChange={(e) => setFamilia(e.target.value)}>
                <option value="">Todas</option>
                {familias.map(([c, n]) => <option key={c} value={c}>{c} · {n}</option>)}
              </select>
            </label>
            <label className="field">
              <span>Ver</span>
              <select value={vista} onChange={(e) => setVista(e.target.value as Vista)}>
                <option value="todas">Todas</option>
                <option value="sin_revisar">Solo sin revisar</option>
                <option value="colisiones">
                  Revisadas que hoy se propondrían ({datos.colisiones ?? 0})
                </option>
              </select>
            </label>
            {puedeEditar && datos.propuestas > 0 ? (
              <button type="button" className="button small" disabled={ocupado}
                      onClick={() => void hacer(aceptarPropuestasMaquinas,
                        "No se han podido aplicar las propuestas.")}>
                Aceptar todas las propuestas ({datos.propuestas})
              </button>
            ) : null}
          </div>
          {puedeEditar ? (
            <form className="erp-historico-acciones" onSubmit={(e) => {
              e.preventDefault();
              const n = Number(umbral.replace(",", "."));
              if (Number.isFinite(n) && n >= 0) {
                void hacer(() => cambiarUmbralMaquinas(n), "No se ha podido cambiar el umbral.")
                  .then((ok) => { if (ok) setUmbral(""); });
              }
            }}>
              <label className="field">
                <span>Umbral de precio (sin pista, por encima: dudosa)</span>
                <input inputMode="decimal" value={umbral} aria-label="Umbral de precio"
                       placeholder={datos.umbral != null ? EUR.format(datos.umbral) : ""}
                       onChange={(e) => setUmbral(e.target.value)} />
              </label>
              <button type="submit" className="button small secondary" disabled={ocupado || !umbral.trim()}>
                Cambiar umbral
              </button>
            </form>
          ) : null}
          {puedeEditar && familia ? (
            <div className="erp-historico-acciones" aria-label="Revisar la familia">
              {confirmarLote ? (
                <span role="alert">
                  Se darán por revisadas <strong>{quedan}</strong> filas de la familia {familia} como
                  «no es máquina». ¿Seguro?{" "}
                  <button type="button" className="button small" disabled={ocupado}
                          onClick={() => void hacer(() => darPorRevisadasFamilia(familia),
                            "No se ha podido dar por revisada la familia.").then(() => setConfirmarLote(false))}>
                    Confirmar ({quedan})
                  </button>{" "}
                  <button type="button" className="button small secondary" onClick={() => setConfirmarLote(false)}>
                    Cancelar
                  </button>
                </span>
              ) : (
                <>
                  <button type="button" className="button small secondary" disabled={ocupado || quedan === 0}
                          onClick={() => setConfirmarLote(true)}>
                    Dar por revisadas las que quedan ({quedan})
                  </button>
                  {enLote > 0 ? (
                    <button type="button" className="button small secondary" disabled={ocupado}
                            onClick={() => void hacer(() => deshacerRevisadasFamilia(familia),
                              "No se ha podido deshacer.")}>
                      Deshacer «dar por revisadas» ({enLote})
                    </button>
                  ) : null}
                </>
              )}
            </div>
          ) : null}
          {filas.length === 0 ? <p className="muted">Nada que mirar con este filtro.</p> : (
            <table className="data-table data-table--responsive">
              <thead>
                <tr>
                  <th>Es máquina</th><th>Artículo</th><th>Familia</th>
                  <th className="num">Líneas</th>
                  <th className="num" aria-sort={orden === "precio_desc" ? "descending"
                    : orden === "precio_asc" ? "ascending" : "none"}>
                    <button type="button" className="erp-th-orden" onClick={ordenarPorPrecio}>
                      Precio máx. {orden === "precio_desc" ? "▼" : orden === "precio_asc" ? "▲" : "↕"}
                    </button>
                  </th>
                  <th>Propuesta</th>
                </tr>
              </thead>
              <tbody>
                {filas.map((i) => (
                  <tr key={i.codart} className={i.estado === "sin_revisar" ? "is-pendiente" : undefined}>
                    <td data-label="Es máquina">
                      <input type="checkbox" aria-label={`Es máquina ${i.codart}`}
                             checked={i.estado === "maquina"} disabled={!puedeEditar || ocupado}
                             onChange={(e) => void hacer(() => marcarHistoricoMaquina(i.codart, e.target.checked),
                               "No se ha podido marcar el artículo.")} />
                      <span className="muted small"> {ESTADO[i.estado]}</span>
                    </td>
                    <td data-label="Artículo">
                      {i.descripcion ?? "—"} <span className="mono muted">{i.codart}</span>
                    </td>
                    <td data-label="Familia">
                      <span className="mono">{i.familia ?? "—"}</span>
                      {i.familia_clase && i.familia_clase !== "maquina"
                        ? <div className="muted small">marcada; su familia no es de máquinas</div> : null}
                    </td>
                    <td data-label="Líneas" className="num">{i.lineas}</td>
                    <td data-label="Precio máx." className="num">
                      {i.precio_max == null ? "—" : EUR.format(i.precio_max)}
                    </td>
                    <td data-label="Propuesta">
                      <Propuesta i={i} />
                      {vista === "colisiones" && i.propondria ? (
                        <div className="muted small">hoy se propondría: {i.propondria === "maquina" ? "máquina" : "dudosa"}</div>
                      ) : null}
                      {puedeEditar && i.estado === "sin_revisar" ? (
                        <div>
                          <button type="button" className="button small secondary" disabled={ocupado}
                                  onClick={() => void hacer(() => marcarHistoricoMaquina(i.codart, false),
                                    "No se ha podido guardar.")}>
                            No es máquina
                          </button>
                        </div>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <details className="erp-maquinas-pistas">
            <summary>Pistas de la propuesta ({datos.reglas.length})</summary>
            <p className="form-hint">
              Trozos de la descripción (sin mayúsculas ni acentos: «ARTIS» casa con «ARTISJET»). Para proponer
              «máquina» hace falta una que diga que lo es; las que delatan un recambio, accesorio o
              consumible van después. Proponen; no deciden.
            </p>
            {(["maquina", "no_maquina"] as const).map((tipo) => (
              <div key={tipo}>
                <h4>{tipo === "maquina" ? "Dicen que es una máquina" : "Delatan que no lo es"}</h4>
                <ul className="erp-maquinas-pistas-lista">
                  {datos.reglas.filter((r) => r.tipo === tipo).map((r) => (
                    <li key={r.id}>
                      <span className="mono">{r.patron}</span>
                      {puedeEditar ? (
                        <button type="button" className="button small secondary" disabled={ocupado}
                                aria-label={`Quitar la pista ${r.patron}`}
                                onClick={() => void hacer(() => borrarPistaArticulo(r.id),
                                  "No se ha podido quitar la pista.")}>
                          ×
                        </button>
                      ) : null}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
            {puedeEditar ? (
              <form className="erp-historico-acciones" onSubmit={(e) => {
                e.preventDefault();
                void hacer(() => crearPistaArticulo(pista.trim(), tipoPista), "No se ha podido añadir la pista.")
                  .then((ok) => { if (ok) setPista(""); });
              }}>
                <label className="field">
                  <span>Nueva pista</span>
                  <input value={pista} maxLength={64} placeholder="p. ej. LASERPECKER o NOZZLE"
                         onChange={(e) => setPista(e.target.value)} />
                </label>
                <label className="field">
                  <span>Dice que</span>
                  <select value={tipoPista} onChange={(e) => setTipoPista(e.target.value as "maquina" | "no_maquina")}>
                    <option value="maquina">es una máquina</option>
                    <option value="no_maquina">no es una máquina</option>
                  </select>
                </label>
                <button type="submit" className="button small secondary" disabled={ocupado || !pista.trim()}>
                  Añadir
                </button>
              </form>
            ) : null}
          </details>
        </>
      )}
    </section>
  );
}
