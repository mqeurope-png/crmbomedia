"use client";

import { useCallback, useEffect, useState } from "react";
import {
  getArticulosClase,
  HISTORICO_CLASE_LABEL,
  setClaseArticulo,
  type ArticuloClase,
  type HistoricoClase,
} from "../../lib/erpApi";
import { extractErrorMessage } from "../../lib/errors";

/** Los artículos con clase propia (por encima de la de su familia): una
 *  familia que mezcla máquinas y accesorios. Se ponen desde el parque («No es
 *  una máquina») y aquí se cambian o se quitan. */

const CLASES = Object.keys(HISTORICO_CLASE_LABEL) as HistoricoClase[];

export function ArticulosClasePanel({ puedeEditar }: { puedeEditar: boolean }) {
  const [items, setItems] = useState<ArticuloClase[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ocupado, setOcupado] = useState(false);

  const leer = useCallback(async () => {
    try {
      setItems((await getArticulosClase()).items);
    } catch (err) {
      setError(extractErrorMessage(err, "No se han podido leer los artículos con clase propia."));
    }
  }, []);

  useEffect(() => { void leer(); }, [leer]);

  async function cambiar(codart: string, clase: HistoricoClase | null) {
    setError(null);
    setOcupado(true);
    try {
      setItems((await setClaseArticulo(codart, clase)).items);
    } catch (err) {
      setError(extractErrorMessage(err, "No se ha podido cambiar la clase del artículo."));
    } finally {
      setOcupado(false);
    }
  }

  return (
    <section className="erp-card" aria-label="Artículos con clase propia">
      <h3>Artículos con clase propia</h3>
      <p className="form-hint">
        Mandan sobre la clase de su familia, en todos los años y todos los clientes. Sirve para
        las familias que mezclan máquinas y accesorios: se marcan desde el parque de una empresa
        («No es una máquina») y aquí se cambian o se quitan.
      </p>
      {error ? <p className="form-error" role="alert">{error}</p> : null}
      {items === null ? <p className="muted">Cargando…</p> : items.length === 0 ? (
        <p className="muted">Ninguno: todos los artículos van con la clase de su familia.</p>
      ) : (
        <table className="data-table data-table--responsive">
          <thead>
            <tr><th>Artículo</th><th>Familia</th><th>Clase propia</th>{puedeEditar ? <th aria-label="Acciones" /> : null}</tr>
          </thead>
          <tbody>
            {items.map((a) => (
              <tr key={a.codart}>
                <td data-label="Artículo">
                  {a.nombre ?? "—"} <span className="mono muted">{a.codart}</span>
                </td>
                <td data-label="Familia">
                  {a.familia ? <span className="mono">{a.familia}</span> : "—"} {a.familia_nombre ?? ""}
                  {a.clase_familia ? <span className="muted"> · {HISTORICO_CLASE_LABEL[a.clase_familia]}</span> : null}
                </td>
                <td data-label="Clase propia">
                  {puedeEditar ? (
                    <select aria-label={`Clase de ${a.codart}`} value={a.clase} disabled={ocupado}
                            onChange={(e) => void cambiar(a.codart, e.target.value as HistoricoClase)}>
                      {CLASES.map((c) => <option key={c} value={c}>{HISTORICO_CLASE_LABEL[c]}</option>)}
                    </select>
                  ) : HISTORICO_CLASE_LABEL[a.clase]}
                </td>
                {puedeEditar ? (
                  <td>
                    <button type="button" className="button small secondary" disabled={ocupado}
                            onClick={() => void cambiar(a.codart, null)}>
                      Volver a la de su familia
                    </button>
                  </td>
                ) : null}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
