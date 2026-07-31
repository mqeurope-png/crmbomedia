"use client";

import Link from "next/link";
import { useState } from "react";
import { COMPANY_DEMO, STATUS_META } from "../mocks";

/** Pantalla 6 — Ficha de empresa con integración FACTUSOL. */
export default function CompanyDemoPage() {
  const [codcli, setCodcli] = useState<string | null>(COMPANY_DEMO.factusolCodcli);
  const [creating, setCreating] = useState(false);

  function createInFactusol() {
    setCreating(true);
    // Mock: en el real esto encola un job en worker-factusol (EscribirRegistro
    // F_CLI) y el chip aparece cuando el job confirma el CODCLI.
    setTimeout(() => { setCodcli("430087"); setCreating(false); }, 900);
  }

  return (
    <main>
      <h1 className="erp-h1">{COMPANY_DEMO.name}</h1>
      <p className="erp-sub">Ficha de empresa (reusa `companies` del CRM) + integración FACTUSOL.</p>
      <div className="erp-grid2">
        <div>
          <div className="erp-card">
            <h3>Datos</h3>
            <dl className="erp-kv">
              <dt>CIF</dt><dd>{COMPANY_DEMO.cif}</dd>
              <dt>Ciudad</dt><dd>{COMPANY_DEMO.city}</dd>
              <dt>Email</dt><dd>{COMPANY_DEMO.email}</dd>
              <dt>Teléfono</dt><dd>{COMPANY_DEMO.phone}</dd>
            </dl>
          </div>
          <div className="erp-card">
            <h3>Pedidos de esta empresa</h3>
            <table className="erp-table">
              <thead><tr><th>Pedido</th><th>Total</th><th>Pago</th><th>Facturación</th></tr></thead>
              <tbody>
                {COMPANY_DEMO.orders.map((o) => (
                  <tr key={o.id}>
                    <td><Link href={`/erp-prototype/orders/${o.id}`}>{o.number}</Link></td>
                    <td>{o.total}</td>
                    <td><span className={`erp-chip ${STATUS_META[o.payment]?.tone}`}>{STATUS_META[o.payment]?.label}</span></td>
                    <td><span className={`erp-chip ${STATUS_META[o.invoicing]?.tone}`}>{STATUS_META[o.invoicing]?.label}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
        <div>
          <div className="erp-card">
            <h3>FACTUSOL</h3>
            {codcli ? (
              <>
                <span className="erp-fsol-chip">✓ FACTUSOL · CODCLI {codcli}</span>
                <p className="erp-sub" style={{ marginTop: 10 }}>
                  Vinculada. El CODCLI vive en external_references de la empresa;
                  facturas y presupuestos usan este cliente.
                </p>
              </>
            ) : (
              <>
                <span className="erp-fsol-chip missing">Sin vincular</span>
                <div className="erp-actions">
                  <button
                    className="erp-btn primary" type="button"
                    onClick={createInFactusol} disabled={creating}
                  >
                    {creating ? "Creando…" : "Crear en FACTUSOL"}
                  </button>
                </div>
                <p className="erp-sub" style={{ marginTop: 10 }}>
                  Encola EscribirRegistro F_CLI en worker-factusol; el chip verde
                  aparece cuando el job confirma el CODCLI asignado.
                </p>
              </>
            )}
          </div>
        </div>
      </div>
    </main>
  );
}
