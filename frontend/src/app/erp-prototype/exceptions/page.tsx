"use client";

import { useState } from "react";
import { EXCEPTIONS } from "../mocks";

const KIND_LABEL: Record<string, string> = {
  sku_unmapped: "SKU sin mapear",
  factusol_write_failed: "Escritura FACTUSOL fallida",
  shipping_incident: "Incidencia transporte",
};

/** Pantalla 4 — Bandeja de excepciones. */
export default function ExceptionsPage() {
  const [rows, setRows] = useState(EXCEPTIONS);
  const patch = (id: string, status: string) =>
    setRows((r) => r.map((e) => (e.id === id ? { ...e, status } : e)));

  return (
    <main>
      <h1 className="erp-h1">Excepciones</h1>
      <p className="erp-sub">
        Todo lo que rompe el flujo feliz aterriza aquí con acción de resolución.
        Deduplicado (p.ej. un SKU sin mapear = 1 excepción aunque llegue en 5 pedidos).
      </p>
      <table className="erp-table">
        <thead><tr><th>Tipo</th><th>Pedido</th><th>Detalle</th><th>Estado</th><th>Acción</th></tr></thead>
        <tbody>
          {rows.map((e) => (
            <tr key={e.id}>
              <td><strong>{KIND_LABEL[e.kind] ?? e.kind}</strong><div className="erp-domchip">{e.at}</div></td>
              <td>{e.order}</td>
              <td>{e.detail}</td>
              <td>
                {e.status === "open" ? <span className="erp-chip bad">Abierta</span> : null}
                {e.status === "ack" ? <span className="erp-chip warn">Vista</span> : null}
                {e.status === "resolved" ? <span className="erp-chip ok">Resuelta</span> : null}
              </td>
              <td>
                <div className="erp-actions">
                  {e.status === "open" ? (
                    <button className="erp-btn" type="button" onClick={() => patch(e.id, "ack")}>Marcar vista</button>
                  ) : null}
                  {e.status !== "resolved" ? (
                    <button className="erp-btn primary" type="button" onClick={() => patch(e.id, "resolved")}>Resolver</button>
                  ) : null}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </main>
  );
}
