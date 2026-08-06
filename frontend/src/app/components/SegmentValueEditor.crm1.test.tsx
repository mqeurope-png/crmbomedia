import { render, screen } from "@testing-library/react";
import { SegmentValueEditor } from "./SegmentValueEditor";
import type { SegmentFieldDescriptor } from "../lib/api";

/** CRM-1 — los campos de llamada son fields del builder de reglas (así salen
 *  en el panel de filtros de /contacts). El editor debe mostrar etiquetas
 *  legibles, no los slugs que guarda el backend. */

const callResult: SegmentFieldDescriptor = {
  key: "call_result",
  label: "Resultado de llamada",
  type: "enum",
  comparators: ["eq", "neq", "in", "not_in"],
  enum_values: [
    "contacted", "no_answer", "voicemail", "call_back",
    "interested", "not_interested", "info_requested", "other",
  ],
};

const callAction: SegmentFieldDescriptor = {
  key: "call_action",
  label: "Acción tras llamada",
  type: "enum",
  comparators: ["eq", "in"],
  enum_values: [
    "change_pipeline", "adjust_lead_score", "adjust_star_score",
    "create_callback_task", "add_to_workflow",
  ],
};

describe("SegmentValueEditor · filtros de llamada (CRM-1)", () => {
  it("el resultado de llamada muestra etiquetas legibles, no slugs", () => {
    render(<SegmentValueEditor spec={callResult} comparator="in"
                               value={[]} onChange={() => {}} />);
    expect(screen.getByText("Interesado")).toBeInTheDocument();
    expect(screen.getByText("Pidió información")).toBeInTheDocument();
    // El slug crudo no debe verse.
    expect(screen.queryByText("info_requested")).not.toBeInTheDocument();
  });

  it("la acción posterior incluye «Ajustó star score»", () => {
    render(<SegmentValueEditor spec={callAction} comparator="in"
                               value={[]} onChange={() => {}} />);
    expect(screen.getByText("Ajustó star score")).toBeInTheDocument();
    expect(screen.getByText("Cambió pipeline")).toBeInTheDocument();
  });
});
