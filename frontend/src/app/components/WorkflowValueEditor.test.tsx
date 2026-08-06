import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SegmentValueEditor } from "./SegmentValueEditor";
import type { SegmentFieldDescriptor } from "../lib/api";
import { listActiveWorkflows } from "../lib/workflowsApi";

jest.mock("../lib/workflowsApi", () => ({
  listActiveWorkflows: jest.fn(),
}));
const mockList = listActiveWorkflows as jest.Mock;

const spec: SegmentFieldDescriptor = {
  key: "in_workflow",
  label: "En workflow",
  type: "uuid-multi",
  comparators: ["in", "not_in"],
  enum_values: [],
};

beforeEach(() => {
  mockList.mockReset();
  mockList.mockResolvedValue([
    { id: "wf-1", name: "Bienvenida" },
    { id: "wf-2", name: "Reactivación" },
  ]);
});

describe("WorkflowMultiEditor (CRM-1.6)", () => {
  it("carga los workflows activos como opciones, no un input de UUID", async () => {
    render(<SegmentValueEditor spec={spec} comparator="in"
                               value={[]} onChange={() => {}} />);
    expect(await screen.findByText("Bienvenida")).toBeInTheDocument();
    expect(screen.getByText("Reactivación")).toBeInTheDocument();
    // No hay input de texto libre para pegar UUID.
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("seleccionar un workflow guarda su UUID en el value", async () => {
    const onChange = jest.fn();
    const user = userEvent.setup();
    render(<SegmentValueEditor spec={spec} comparator="in"
                               value={[]} onChange={onChange} />);
    await screen.findByText("Bienvenida");
    await user.click(screen.getByRole("checkbox", { name: /Bienvenida/ }));
    expect(onChange).toHaveBeenCalledWith(["wf-1"]);
  });

  it("muestra un aviso si no hay workflows activos", async () => {
    mockList.mockResolvedValue([]);
    render(<SegmentValueEditor spec={spec} comparator="in"
                               value={[]} onChange={() => {}} />);
    expect(await screen.findByText("No hay workflows activos."))
      .toBeInTheDocument();
  });
});
