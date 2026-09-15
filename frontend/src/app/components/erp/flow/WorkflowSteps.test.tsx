import { render, screen, within } from "@testing-library/react";
import { WorkflowProgress, WorkflowSteps, stepProgress } from "./WorkflowSteps";
import type { WorkflowStep } from "../../../lib/erpApi";

/** Lote 2 · PR-2 — la «línea de vida»: el stepper horizontal de siempre y el
 *  modo `vertical` de la ficha (icono numerado con ✓ en los hechos, dato del
 *  paso en la misma fila, «Paso actual» y el contenido de `renderAction`
 *  dentro del paso). La barra de 7 segmentos sale de los mismos pasos. */

const STEPS: WorkflowStep[] = [
  { key: "creado", label: "Creado", state: "done", detail: "2026-09-12" },
  { key: "pagado", label: "Pagado", state: "done", detail: "4290.00 EUR" },
  { key: "aprobado", label: "Aprobado", state: "done", detail: "2026-09-12" },
  { key: "albaran", label: "Albarán", state: "skipped", detail: "lo crea WooCommerce" },
  { key: "factura", label: "Factura", state: "done", detail: "F-2026/118" },
  { key: "cobro", label: "Cobro", state: "now", detail: null },
  { key: "enviado", label: "Enviado", state: "pending", detail: null },
];

describe("WorkflowSteps", () => {
  it("horizontal (por defecto): 7 pasos con su icono de estado y el actual marcado", () => {
    render(<WorkflowSteps steps={STEPS} />);
    const list = screen.getByRole("list", { name: "Ciclo del pedido" });
    expect(list).not.toHaveClass("is-vertical");
    const items = within(list).getAllByRole("listitem");
    expect(items).toHaveLength(7);
    expect(items[0]).toHaveTextContent("✓");
    expect(items[3]).toHaveClass("is-skipped");
    expect(items[5]).toHaveAttribute("aria-current", "step");
    expect(items[6]).toHaveTextContent("—");
  });

  it("vertical: ✓ en los hechos, número en los demás, el dato en la fila y «no aplica · motivo» en los omitidos", () => {
    render(<WorkflowSteps steps={STEPS} vertical />);
    const list = screen.getByRole("list", { name: "Ciclo del pedido" });
    expect(list).toHaveClass("erp-flow-steps", "is-vertical");
    const items = within(list).getAllByRole("listitem");
    expect(items).toHaveLength(7);
    const icon = (li: HTMLElement) => li.querySelector(".erp-flow-step-ic")?.textContent;
    expect(icon(items[0])).toBe("✓");
    expect(icon(items[3])).toBe("—");
    expect(icon(items[5])).toBe("6");
    expect(icon(items[6])).toBe("7");
    expect(items[1]).toHaveTextContent("4290.00 EUR");
    expect(items[3]).toHaveTextContent("no aplica · lo crea WooCommerce");
    expect(items[4]).toHaveTextContent("F-2026/118");
    expect(items[6]).toHaveTextContent("pendiente");
    // El actual: aria-current, «Paso actual» y la ÚNICA tarjeta.
    expect(items[5]).toHaveAttribute("aria-current", "step");
    expect(items[5]).toHaveTextContent("Paso actual");
    expect(list.querySelectorAll(".erp-flow-step-body.is-card")).toHaveLength(1);
    expect(items[5].querySelector(".erp-flow-step-body.is-card")).not.toBeNull();
  });

  it("vertical: lo que devuelve `renderAction` se pinta dentro de SU paso (la ficha mete ahí la acción del actual)", () => {
    render(
      <WorkflowSteps
        steps={STEPS}
        vertical
        renderAction={(s) => (s.state === "now" ? <button type="button">Registrar cobro</button> : null)}
      />,
    );
    const items = screen.getAllByRole("listitem");
    expect(within(items[5]).getByRole("button", { name: "Registrar cobro" })).toBeInTheDocument();
    expect(screen.getAllByRole("button")).toHaveLength(1);
    expect(items[5].querySelector(".erp-flow-step-x")).not.toBeNull();
    expect(items[4].querySelector(".erp-flow-step-x")).toBeNull();
  });
});

describe("WorkflowProgress", () => {
  it("un segmento por paso con su estado y la lectura «Paso 6 de 7 · Cobro»", () => {
    render(<WorkflowProgress steps={STEPS} />);
    expect(screen.getByText(/Paso 6 de 7/)).toHaveTextContent("Paso 6 de 7 · Cobro");
    const segs = document.querySelectorAll(".erp-flow-progress-seg");
    expect(Array.from(segs).map((s) => s.className.replace("erp-flow-progress-seg ", ""))).toEqual([
      "is-done", "is-done", "is-done", "is-skipped", "is-done", "is-now", "is-pending",
    ]);
  });

  it("sin paso actual (todo hecho) dice cuántos pasos hay hechos", () => {
    const done = STEPS.map((s) => ({ ...s, state: "done" as const, detail: "ok" }));
    render(<WorkflowProgress steps={done} />);
    expect(screen.getByText("7 de 7 pasos hechos")).toBeInTheDocument();
    expect(stepProgress(done)).toEqual({ current: null, label: null, total: 7, done: 7 });
    // 4 hechos: el albarán omitido no cuenta como hecho.
    expect(stepProgress(STEPS)).toEqual({ current: 6, label: "Cobro", total: 7, done: 4 });
  });
});
