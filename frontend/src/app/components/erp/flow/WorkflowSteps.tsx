"use client";

import type { ReactNode } from "react";
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
 *  calcula el backend (`workflow.steps`): aquí no se deduce nada.
 *
 *  Lote 2 · PR-2: en `vertical` los 7 pasos van en columna (cabe en cualquier
 *  ancho): icono con conector, título y dato del paso en la misma fila, y el
 *  paso actual como la ÚNICA tarjeta azul de la pantalla, con lo que
 *  `renderAction` devuelva dentro (la ficha incrusta ahí «Siguiente paso» con
 *  su botón). Los pasos hechos llevan ✓; los omitidos dicen por qué. */
export function WorkflowSteps({
  steps, vertical = false, renderAction,
}: {
  steps: WorkflowStep[];
  /** Línea de vida en columna (ficha). Sin él, el stepper horizontal de siempre. */
  vertical?: boolean;
  /** Contenido extra de un paso (solo se pinta en `vertical`): la ficha lo
   *  usa para meter la acción del paso actual dentro de su tarjeta. */
  renderAction?: (step: WorkflowStep, index: number) => ReactNode;
}) {
  if (!vertical) {
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
  return (
    <ol className="erp-flow-steps is-vertical" aria-label="Ciclo del pedido">
      {steps.map((s, i) => {
        const extra = renderAction?.(s, i) ?? null;
        return (
          <li
            key={s.key}
            className={`erp-flow-step is-${s.state}${s.optional ? " is-optional" : ""}`}
            aria-current={s.state === "now" ? "step" : undefined}
          >
            <span className="erp-flow-step-ic" aria-hidden>
              {s.optional
                ? (s.state === "done" ? "✓" : "·")
                : s.state === "done" ? "✓" : s.state === "skipped" ? "—" : i + 1}
            </span>
            <div className={`erp-flow-step-body${s.state === "now" ? " is-card" : ""}`}>
              <div className="erp-flow-step-row">
                <span className="erp-flow-step-t">{s.label}</span>
                {s.state === "now" ? (
                  <span className="erp-flow-step-tag">Paso actual</span>
                ) : s.optional ? (
                  <span className="erp-flow-step-tag is-optional">opcional</span>
                ) : null}
                <span className="erp-flow-step-d">{stepDetail(s)}</span>
              </div>
              {extra ? <div className="erp-flow-step-x">{extra}</div> : null}
            </div>
            {/* El actual ya lleva «Paso actual» visible: no repetirlo. */}
            {s.state === "now" ? null : <span className="sr-only">{TITLE[s.state]}</span>}
          </li>
        );
      })}
    </ol>
  );
}

/** Texto del dato de un paso en la línea de vida: el detalle del backend
 *  (fecha, importe, nº de albarán/factura) o, si no lo hay, qué le pasa. */
function stepDetail(s: WorkflowStep): string {
  if (s.state === "skipped") return s.detail ? `no aplica · ${s.detail}` : "no aplica";
  if (s.detail) return s.detail;
  return s.state === "done" ? "hecho" : "pendiente";
}

/** Lectura rápida de la línea de vida («Paso 5 de 6 · Cobro»): el índice del
 *  paso actual (1-based) o null si no hay ninguno (todos hechos u omitidos).
 *  Los hitos OPCIONALES (p. ej. «Factura enviada») NO cuentan para el total ni
 *  para «N hechos»: son informativos y van SIEMPRE al final de la lista. */
export function stepProgress(steps: WorkflowStep[]): {
  current: number | null;
  label: string | null;
  total: number;
  done: number;
} {
  const obligatorios = steps.filter((s) => !s.optional);
  const idx = steps.findIndex((s) => s.state === "now");
  return {
    current: idx >= 0 ? idx + 1 : null,
    label: idx >= 0 ? steps[idx].label : null,
    total: obligatorios.length,
    done: obligatorios.filter((s) => s.state === "done").length,
  };
}

/** Lote 2 · PR-2 — la barra de 7 segmentos de la cabecera de la ficha: un
 *  segmento por paso con su color de estado y, debajo, «Paso 6 de 7 · Cobro».
 *  Sale de `workflow.steps`, nada más. */
export function WorkflowProgress({ steps }: { steps: WorkflowStep[] }) {
  const p = stepProgress(steps);
  const text = p.current != null
    ? `Paso ${p.current} de ${p.total}`
    : p.done === p.total
      ? `${p.total} de ${p.total} pasos hechos`
      : `${p.done} de ${p.total} pasos hechos`;
  return (
    <div className="erp-flow-progress">
      <div className="erp-flow-progress-bar" aria-hidden>
        {steps.filter((s) => !s.optional).map((s) => (
          <span key={s.key} className={`erp-flow-progress-seg is-${s.state}`} title={s.label} />
        ))}
      </div>
      <p className="erp-flow-progress-txt">
        {text}
        {p.label ? <> · <strong>{p.label}</strong></> : null}
      </p>
    </div>
  );
}
