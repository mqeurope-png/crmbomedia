"use client";

import { useEffect, useMemo, useState } from "react";
import {
  aceptarPropuestasMaquinas,
  borrarPistaArticulo,
  crearPistaArticulo,
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

function Propuesta({ i }: { i: MaquinaArticulo }) {
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
  const [soloSinRevisar, setSoloSinRevisar] = useState(false);
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
    (!familia || i.familia === familia) && (!soloSinRevisar || i.estado === "sin_revisar"));

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
            <label className="field-toggle">
              <input type="checkbox" checked={soloSinRevisar}
                     onChange={(e) => setSoloSinRevisar(e.target.checked)} />
              <span>Solo sin revisar</span>
            </label>
            {puedeEditar && datos.propuestas > 0 ? (
              <button type="button" className="button small" disabled={ocupado}
                      onClick={() => void hacer(aceptarPropuestasMaquinas,
                        "No se han podido aplicar las propuestas.")}>
                Aceptar todas las propuestas ({datos.propuestas})
              </button>
            ) : null}
          </div>
          {filas.length === 0 ? <p className="muted">Nada que mirar con este filtro.</p> : (
            <table className="data-table data-table--responsive">
              <thead>
                <tr>
                  <th>Es máquina</th><th>Artículo</th><th>Familia</th>
                  <th className="num">Líneas</th><th>Propuesta</th>
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
                    <td data-label="Propuesta"><Propuesta i={i} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <details className="erp-maquinas-pistas">
            <summary>Pistas de la propuesta ({datos.reglas.length})</summary>
            <p className="form-hint">
              Palabras de la descripción (palabra entera, sin mayúsculas ni acentos). Para proponer
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
