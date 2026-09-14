"use client";

import type { ReactNode } from "react";
import type { OrderWorkflow } from "../../../lib/erpApi";

/** «Siguiente paso» de la ficha: lo que el sistema dice que toca, con su
 *  acción incrustada. El texto sale del backend (`next_action_hint`); los
 *  botones los pone la ficha, que es quien sabe abrir cada modal. */
export function NextActionBar({
  workflow, children,
}: {
  workflow: OrderWorkflow;
  children?: ReactNode;
}) {
  return (
    <section
      className={`erp-flow-nowbar${workflow.blocked ? " is-blocking" : ""}`}
      aria-label="Siguiente paso"
    >
      <div className="erp-flow-nowbar-txt">
        <p className="erp-flow-nowbar-k">
          {workflow.blocked ? "Hay que resolver esto" : "Siguiente paso"}
        </p>
        <p className="erp-flow-nowbar-v">{workflow.next_action_hint}</p>
      </div>
      {children ? <div className="erp-flow-nowbar-actions">{children}</div> : null}
    </section>
  );
}
