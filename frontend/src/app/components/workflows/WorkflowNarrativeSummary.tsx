"use client";

import { BookOpen } from "lucide-react";
import { useMemo } from "react";
import {
  buildNarrativeSummary,
  type NarrativeEdge,
  type NarrativeNode,
} from "../../lib/workflowsHumanize";

type Props = {
  triggerType: string;
  steps: NarrativeNode[];
  edges: NarrativeEdge[];
};

/** Resumen narrado del workflow, pegado al pie del editor. Se
 *  regenera con cada cambio del grafo. */
export function WorkflowNarrativeSummary({
  triggerType,
  steps,
  edges,
}: Props) {
  const narrative = useMemo(() => {
    const entry = steps.find((s) => s.type === "trigger") ?? steps[0];
    if (!entry) return "";
    return buildNarrativeSummary(triggerType, steps, edges, entry.id);
  }, [triggerType, steps, edges]);

  if (!narrative) return null;
  return (
    <section className="workflow-narrative">
      <h3>
        <BookOpen size={13} aria-hidden /> Resumen del workflow
      </h3>
      <pre className="workflow-narrative-text">{narrative}</pre>
    </section>
  );
}
