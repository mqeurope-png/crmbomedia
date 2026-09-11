"use client";

import type { ReactNode } from "react";
import type { FactusolQuote } from "../../lib/erpApi";

/** Tabla de proformas FACTUSOL compartida por la ficha de empresa
 *  (`CompanyQuotesPanel`) y el buscador del alta de pedido (`QuotePicker`):
 *  mismas columnas, acciones por fila a cargo del padre. */
export function QuotesTable({
  quotes,
  actions,
  showCliente = false,
  emptyText = "Sin proformas.",
}: {
  quotes: FactusolQuote[];
  actions: (q: FactusolQuote) => ReactNode;
  showCliente?: boolean;
  emptyText?: string;
}) {
  if (quotes.length === 0) {
    return <p className="muted small">{emptyText}</p>;
  }
  return (
    <table className="data-table">
      <thead>
        <tr>
          <th>Nº</th><th>Fecha</th>
          {showCliente ? <th>Cliente</th> : null}
          <th>Referencia</th><th>Total</th><th />
        </tr>
      </thead>
      <tbody>
        {quotes.map((q) => (
          <tr key={q.codpre ?? ""}>
            <td>{q.codpre}</td>
            <td className="muted small">{q.fecha ?? "—"}</td>
            {showCliente ? <td>{q.cliente_nombre ?? q.clipre ?? "—"}</td> : null}
            <td>{q.referencia || "—"}</td>
            <td>{q.total.toFixed(2)} €</td>
            <td className="erp-quote-row-actions">{actions(q)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
