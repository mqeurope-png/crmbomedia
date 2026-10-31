"use client";

import { useEffect, useState } from "react";
import { getHistoricoSeries, marcarHistoricoSerie, type SeriesVenta } from "../../lib/erpApi";
import { extractErrorMessage } from "../../lib/errors";

/** Qué series de FACTUSOL cuentan como venta (31/10/2026). Las que no,
 *  quedan fuera del parque y de «Quién compró qué». Datos, no código: hoy
 *  sobra una serie, mañana otra. Se ve cuánto se queda fuera antes de
 *  decidir. */

const NF = new Intl.NumberFormat("es-ES");

export function SeriesVentaPanel({ puedeEditar }: { puedeEditar: boolean }) {
  const [datos, setDatos] = useState<SeriesVenta | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ocupado, setOcupado] = useState(false);

  useEffect(() => {
    getHistoricoSeries().then(setDatos)
      .catch((e) => setError(extractErrorMessage(e, "No se han podido leer las series.")));
  }, []);

  async function marcar(serie: string, cuenta: boolean) {
    setError(null);
    setOcupado(true);
    try {
      setDatos(await marcarHistoricoSerie(serie, cuenta));
    } catch (e) {
      setError(extractErrorMessage(e, "No se ha podido cambiar la serie."));
    } finally {
      setOcupado(false);
    }
  }

  return (
    <section className="erp-card" aria-label="Series que cuentan como venta">
      <h3>Series que cuentan como venta</h3>
      <p className="form-hint">
        Una serie que no cuenta queda fuera del parque instalado y de «Quién compró qué». No se
        borra nada: se vuelve a marcar y vuelve a contar.
      </p>
      {error ? <p className="form-error" role="alert">{error}</p> : null}
      {datos === null ? <p className="muted">Cargando…</p> : (
        <>
          <p role="status">
            {datos.fuera.documentos === 0 ? "Todas las series cuentan." : (
              <>Con lo marcado se quedan fuera <strong>{NF.format(datos.fuera.documentos)} documentos</strong>
                {" "}y <strong>{NF.format(datos.fuera.lineas)} líneas</strong>.</>
            )}
          </p>
          <table className="data-table data-table--responsive">
            <thead>
              <tr><th>Cuenta</th><th>Serie</th><th className="num">Documentos</th><th className="num">Líneas</th></tr>
            </thead>
            <tbody>
              {datos.items.map((s) => (
                <tr key={s.serie} className={s.cuenta ? undefined : "muted"}>
                  <td data-label="Cuenta">
                    <input type="checkbox" checked={s.cuenta} disabled={!puedeEditar || ocupado}
                           aria-label={`Cuenta la serie ${s.serie}`}
                           onChange={(e) => void marcar(s.serie, e.target.checked)} />
                  </td>
                  <td data-label="Serie">
                    <span className="mono">{s.serie}</span>{" "}
                    {s.nombre ?? <span className="muted">sin nombre en Ajustes ERP</span>}
                  </td>
                  <td data-label="Documentos" className="num">{NF.format(s.documentos)}</td>
                  <td data-label="Líneas" className="num">{NF.format(s.lineas)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </section>
  );
}
