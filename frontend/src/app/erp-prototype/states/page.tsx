/** Pantalla 5 — Vista visual de las 4 máquinas de estado del pedido. */

const FLOWS: { domain: string; steps: string[]; guards: string[] }[] = [
  {
    domain: "1 · Pago",
    steps: ["Pendiente", "Pagado", "(Reembolsado)"],
    guards: ["Automático via webhook Woo (date_paid)", "Reembolso: solo admin + motivo"],
  },
  {
    domain: "2 · Preparación (SAT)",
    steps: ["En cola", "Preparando", "Embalado"],
    guards: ["Empezar exige Pagado (salvo override admin)", "⚠ ¿foto obligatoria al embalar? (Bart)", "Bloqueado ⇄ con nota"],
  },
  {
    domain: "3 · Transporte",
    steps: ["Sin enviar", "Etiqueta creada", "En tránsito", "Entregado"],
    guards: ["Crear envío exige Embalado", "En tránsito exige tracking number", "Incidencia ⇄ con descripción"],
  },
  {
    domain: "4 · Facturación (FACTUSOL)",
    steps: ["Sin facturar", "Factura pendiente", "Facturada"],
    guards: ["Exige Pagado + todos los SKU mapeados", "Numeración SIEMPRE de FACTUSOL", "Error → bandeja excepciones"],
  },
];

export default function StatesPage() {
  return (
    <main>
      <h1 className="erp-h1">Máquinas de estado del pedido</h1>
      <p className="erp-sub">
        4 dominios independientes — ninguno bloquea a los otros salvo los guards marcados.
        Detalle completo con roles/evidencias en docs/erp/state-machines.md.
      </p>
      {FLOWS.map((f) => (
        <div className="erp-card" key={f.domain}>
          <h3>{f.domain}</h3>
          <div className="erp-flow">
            {f.steps.map((s, i) => (
              <span key={s} style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
                {i > 0 ? <span className="arrow">→</span> : null}
                <span className="erp-chip active">{s}</span>
              </span>
            ))}
          </div>
          <div className="erp-actions">
            {f.guards.map((g) => <span className="erp-guard" key={g}>{g}</span>)}
          </div>
        </div>
      ))}
    </main>
  );
}
