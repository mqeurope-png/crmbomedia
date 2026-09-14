"use client";

import type { WorkflowStep } from "../../../lib/erpApi";

const ICON: Record<WorkflowStep["state"], string> = {
  done: "✓",
  now: "●",
  pending: "○",
  skipped: "—",
};

const TITLE: Record<WorkflowStep["state"], string> = {
  done: "Hecho",
  now: "Paso actual",
  pending: "Pendiente",
  skipped: "No aplica",
};

/** La «línea de vida» del pedido: Creado → Pagado → Aprobado → Albarán →
 *  Factura → Cobro → Enviado, con el paso actual resaltado. Los estados los
 *  calcula el backend (`workflow.steps`): aquí no se deduce nada. */
export function WorkflowSteps({ steps }: { steps: WorkflowStep[] }) {
  return (
    <ol className="erp-flow-steps" aria-label="Ciclo del pedido">
      {steps.map((s) => (
        <li
          key={s.key}
          className={`erp-flow-step is-${s.state}`}
          aria-current={s.state === "now" ? "step" : undefined}
        >
          <span className="erp-flow-step-ic" aria-hidden>{ICON[s.state]}</span>
          <span className="erp-flow-step-t">{s.label}</span>
          <span className="erp-flow-step-d">{s.detail ?? "—"}</span>
          <span className="sr-only">{TITLE[s.state]}</span>
        </li>
      ))}
    </ol>
  );
}
