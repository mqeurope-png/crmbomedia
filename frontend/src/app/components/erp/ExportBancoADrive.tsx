"use client";

import { useState } from "react";
import { anadirExportBankDrive, type BankExportPlan } from "../../lib/erpApi";
import { extractErrorMessage } from "../../lib/errors";

const eur = (n: number) =>
  n.toLocaleString("es-ES", { style: "currency", currency: "EUR" });

const fecha = (iso: string) => {
  const [y, m, d] = iso.split("-");
  return `${d}/${m}/${y}`;
};

const plural = (n: number, uno: string, varios: string) => `${n} ${n === 1 ? uno : varios}`;

/** El fichero del banco, tal cual, a la hoja de Drive de la cuenta: primero
 *  la vista previa (qué trae, qué ya está, qué se añadiría) y, al confirmar,
 *  las filas que faltan arriba de la hoja. No toca ninguna fila existente. */
export function ExportBancoADrive({
  accountId, accountName, disabled, onAnadido,
}: {
  accountId: string;
  accountName: string;
  disabled?: boolean;
  onAnadido: (plan: BankExportPlan) => void | Promise<void>;
}) {
  const [fichero, setFichero] = useState<File | null>(null);
  const [plan, setPlan] = useState<BankExportPlan | null>(null);
  const [ocupado, setOcupado] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function previa(f: File) {
    setOcupado(true);
    setError(null);
    setPlan(null);
    setFichero(f);
    try {
      setPlan(await anadirExportBankDrive(f, accountId, false));
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo comparar el fichero con la hoja."));
    } finally {
      setOcupado(false);
    }
  }

  async function anadir() {
    if (!fichero) return;
    setOcupado(true);
    setError(null);
    try {
      const hecho = await anadirExportBankDrive(fichero, accountId, true);
      setPlan(null);
      setFichero(null);
      await onAnadido(hecho);
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo añadir a la hoja."));
    } finally {
      setOcupado(false);
    }
  }

  return (
    <div>
      <label className="field">
        <span>Escribir en la hoja de Drive lo que falte · .xls / .xlsx / .csv</span>
        <small className="muted">El fichero del banco tal cual: añade a la hoja de {accountName} las filas que le falten.</small>
        <input
          type="file"
          accept=".xls,.xlsx,.csv"
          aria-label="Export del banco para la hoja"
          disabled={disabled || ocupado}
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) void previa(f);
            e.currentTarget.value = "";
          }}
        />
      </label>
      {error ? <p className="form-error" role="alert">{error}</p> : null}
      {plan ? (
        <div className="erp-card" aria-label="Vista previa del export">
          <p>
            <strong>{plural(plan.total, "movimiento", "movimientos")} en el fichero</strong>
            {" · "}{plan.ya} ya {plan.ya === 1 ? "está" : "están"}
            {" · "}{plural(plan.nuevos, "se añadirá", "se añadirán")} a la hoja de {accountName}
          </p>
          {plan.nuevos ? (
            <>
              <p className="muted small">
                Van arriba, debajo de la cabecera, del más reciente al más antiguo, con factura y
                pedido vacías. Ninguna fila que ya esté en la hoja se toca.
              </p>
              <table className="data-table" aria-label="Movimientos que se añadirán">
                <thead>
                  <tr><th>Fecha</th><th>Concepto</th><th>Importe</th><th>Saldo</th></tr>
                </thead>
                <tbody>
                  {plan.movimientos.map((m, i) => (
                    <tr key={`${m.fecha_oper}-${i}`}>
                      <td>{fecha(m.fecha_oper)}</td>
                      <td>{m.concepto}</td>
                      <td>{eur(m.importe)}</td>
                      <td>{m.saldo === null ? "—" : eur(m.saldo)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="erp-doc-filters">
                <button type="button" className="button small" disabled={ocupado}
                  onClick={() => void anadir()}>
                  Añadir {plural(plan.nuevos, "movimiento", "movimientos")} a la hoja
                </button>
                <button type="button" className="button small secondary" disabled={ocupado}
                  onClick={() => { setPlan(null); setFichero(null); }}>
                  Cancelar
                </button>
              </div>
            </>
          ) : (
            <p className="muted small">La hoja ya tiene todos: no hay nada que añadir.</p>
          )}
        </div>
      ) : null}
    </div>
  );
}
