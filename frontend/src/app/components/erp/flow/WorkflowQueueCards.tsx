"use client";

import type { WorkflowQueue } from "../../../lib/erpApi";

/** Orden de las colas en la bandeja (el de la maqueta). */
export const QUEUE_ORDER: readonly WorkflowQueue[] = [
  "por_revisar", "por_facturar", "por_cobrar", "por_enviar", "incidencias", "listo",
] as const;

/** Colores de cada cola (los de la maqueta): el mismo tono identifica la cola
 *  en la bandeja y en cualquier pantalla que la enseñe. */
export const QUEUE_COLOR: Record<WorkflowQueue, string> = {
  por_revisar: "#E0A92A",
  por_facturar: "#2F6BFF",
  por_cobrar: "#1F9D74",
  por_enviar: "#7A4BD6",
  incidencias: "#E2554A",
  listo: "#9AA2AD",
};

export const QUEUE_LABEL: Record<WorkflowQueue, string> = {
  por_revisar: "Por revisar",
  por_facturar: "Por facturar",
  por_cobrar: "Por cobrar",
  por_enviar: "Por enviar",
  incidencias: "Incidencias",
  listo: "Listo",
};

/** Qué significa cada cola, para el subtítulo de la lista. */
export const QUEUE_HINT: Record<WorkflowQueue, string> = {
  por_revisar: "esperando tu aprobación",
  por_facturar: "aprobados, sin factura en FACTUSOL",
  por_cobrar: "facturados, sin cobro registrado en FACTUSOL",
  por_enviar: "cobrados, pendientes de salir",
  incidencias: "algo bloquea el pedido",
  listo: "nada pendiente",
};

/** Cabecera de la bandeja: una tarjeta por cola con su contador. Es la
 *  organización PRIMARIA del trabajo (los filtros de estado quedan como
 *  refinamiento). Volver a pulsar la cola activa quita el filtro. */
export function WorkflowQueueCards({
  counts, active, onSelect, queues = QUEUE_ORDER,
}: {
  counts: Partial<Record<WorkflowQueue, number>>;
  active: WorkflowQueue | null;
  onSelect: (queue: WorkflowQueue | null) => void;
  queues?: readonly WorkflowQueue[];
}) {
  return (
    <nav className="erp-flow-queues" aria-label="Colas de trabajo">
      {queues.map((q) => (
        <button
          key={q}
          type="button"
          className={`erp-flow-queue${active === q ? " is-active" : ""}`}
          style={{ ["--qc" as string]: QUEUE_COLOR[q] }}
          aria-pressed={active === q}
          aria-label={`${QUEUE_LABEL[q]} (${counts[q] ?? 0})`}
          title={QUEUE_HINT[q]}
          onClick={() => onSelect(active === q ? null : q)}
        >
          <span className="erp-flow-queue-n">{counts[q] ?? 0}</span>
          <span className="erp-flow-queue-l">
            <span className="erp-flow-dot" aria-hidden />
            {QUEUE_LABEL[q]}
          </span>
        </button>
      ))}
    </nav>
  );
}
