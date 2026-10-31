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
 *  que mirar, y aquí se marca cuáles son máquinas. BoHub propone (por las
 *  pistas de la descripción) y Bart confirma; «Aceptar todas» solo toca lo
 *  que nadie ha revisado. Lo no marcado no entra en el parque. */

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
            {datos.propuestas > 0 ? <span className="muted"> · {datos.propuestas} sin revisar</span> : null}
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
                    <td data-label="Familia"><span className="mono">{i.familia ?? "—"}</span></td>
                    <td data-label="Líneas" className="num">{i.lineas}</td>
                    <td data-label="Propuesta">
                      {i.propuesta === "maquina"
                        ? <span className="badge ok">máquina</span>
                        : <span className="badge muted" title="La pista que lo delata">no · {i.pista}</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <details className="erp-maquinas-pistas">
            <summary>Pistas de que algo no es una máquina ({datos.reglas.length})</summary>
            <p className="form-hint">
              Palabras de la descripción (palabra entera, sin mayúsculas ni acentos) que delatan
              un recambio, accesorio o consumible. Proponen; no deciden.
            </p>
            <ul className="erp-maquinas-pistas-lista">
              {datos.reglas.map((r) => (
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
            {puedeEditar ? (
              <form className="erp-historico-acciones" onSubmit={(e) => {
                e.preventDefault();
                void hacer(() => crearPistaArticulo(pista.trim()), "No se ha podido añadir la pista.")
                  .then((ok) => { if (ok) setPista(""); });
              }}>
                <label className="field">
                  <span>Nueva pista</span>
                  <input value={pista} maxLength={64} placeholder="p. ej. NOZZLE"
                         onChange={(e) => setPista(e.target.value)} />
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
