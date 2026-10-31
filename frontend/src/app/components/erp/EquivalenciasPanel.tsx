"use client";

import { useEffect, useState } from "react";
import {
  declararEquivalencia,
  getEquivalencias,
  quitarEquivalencia,
  type Equivalencia,
} from "../../lib/erpApi";
import { extractErrorMessage } from "../../lib/errors";

/** Códigos equivalentes (1/11/2026): el mismo modelo con el código interno de
 *  FACTUSOL y el de la web. Un albarán con uno y su factura con el otro se
 *  emparejan, el parque cuenta una máquina y «Quién compró qué» encuentra
 *  las ventas de los dos. Datos, no código: se añaden aquí. */
export function EquivalenciasPanel({ puedeEditar }: { puedeEditar: boolean }) {
  const [items, setItems] = useState<Equivalencia[] | null>(null);
  const [canonico, setCanonico] = useState("");
  const [codart, setCodart] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [ocupado, setOcupado] = useState(false);

  useEffect(() => {
    getEquivalencias().then((r) => setItems(r.items))
      .catch((e) => setError(extractErrorMessage(e, "No se han podido leer las equivalencias.")));
  }, []);

  async function hacer(accion: () => Promise<{ items: Equivalencia[] }>, fallo: string): Promise<boolean> {
    setError(null);
    setOcupado(true);
    try {
      setItems((await accion()).items);
      return true;
    } catch (e) {
      setError(extractErrorMessage(e, fallo));
      return false;
    } finally {
      setOcupado(false);
    }
  }

  return (
    <section className="erp-card" aria-label="Códigos equivalentes">
      <h3>Códigos equivalentes</h3>
      <p className="form-hint">
        El mismo modelo con dos códigos: el de FACTUSOL y el de la web. Declarados aquí, un
        albarán con uno y su factura con el otro son la misma venta, el parque cuenta una
        máquina y al buscar uno salen también las ventas del otro. Los que no estén aquí
        siguen dando el aviso de «¿la misma máquina?» en la ficha del cliente.
      </p>
      {error ? <p className="form-error" role="alert">{error}</p> : null}
      {items === null ? <p className="muted">Cargando…</p> : items.length === 0 ? (
        <p className="muted">No hay ninguna declarada.</p>
      ) : (
        <table className="data-table data-table--responsive">
          <thead>
            <tr><th>Código</th><th>Es el mismo modelo que</th>{puedeEditar ? <th /> : null}</tr>
          </thead>
          <tbody>
            {items.map((e) => (
              <tr key={e.codart}>
                <td data-label="Código">
                  <span className="mono">{e.codart}</span> {e.descripcion ?? ""}
                </td>
                <td data-label="Es el mismo modelo que">
                  <span className="mono">{e.canonico}</span> {e.canonico_descripcion ?? ""}
                </td>
                {puedeEditar ? (
                  <td>
                    <button type="button" className="button small secondary" disabled={ocupado}
                            aria-label={`Quitar la equivalencia de ${e.codart}`}
                            onClick={() => void hacer(() => quitarEquivalencia(e.codart),
                              "No se ha podido quitar.")}>
                      Quitar
                    </button>
                  </td>
                ) : null}
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {puedeEditar ? (
        <form className="erp-historico-acciones" onSubmit={(ev) => {
          ev.preventDefault();
          void hacer(() => declararEquivalencia(canonico.trim(), codart.trim()),
            "No se ha podido declarar.")
            .then((ok) => { if (ok) { setCanonico(""); setCodart(""); } });
        }}>
          <label className="field">
            <span>Código</span>
            <input value={codart} maxLength={64} placeholder="p. ej. el de la web"
                   onChange={(ev) => setCodart(ev.target.value)} />
          </label>
          <label className="field">
            <span>Es el mismo modelo que</span>
            <input value={canonico} maxLength={64} placeholder="p. ej. el de FACTUSOL"
                   onChange={(ev) => setCanonico(ev.target.value)} />
          </label>
          <button type="submit" className="button small secondary"
                  disabled={ocupado || !canonico.trim() || !codart.trim()}>
            Declarar
          </button>
        </form>
      ) : null}
    </section>
  );
}
