import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { EmitFactusolModal } from "./EmitFactusolModal";
import { getFactusolFormasPago } from "../../lib/erpApi";

jest.mock("../../lib/erpApi", () => ({ getFactusolFormasPago: jest.fn() }));
const mockFormas = getFactusolFormasPago as jest.Mock;

beforeEach(() => {
  mockFormas.mockReset();
  mockFormas.mockResolvedValue([]);
});

function base(over = {}) {
  return {
    totalAmount: 186.34, currency: "EUR",
    onSubmit: jest.fn(), onCancel: jest.fn(), ...over,
  };
}

describe("EmitFactusolModal", () => {
  it("renderiza los 5 campos con defaults y la advertencia", async () => {
    render(<EmitFactusolModal {...base()} />);
    expect(await screen.findByLabelText("Tipo")).toHaveValue("1");
    expect(screen.getByLabelText("Serie")).toHaveValue("");
    expect(screen.getByLabelText("Fecha de emisión")).toBeInTheDocument();
    expect(screen.getByLabelText("Forma de pago")).toBeInTheDocument();
    expect(screen.getByLabelText("Observaciones")).toBeInTheDocument();
    expect(screen.getByText(/no es reversible/)).toBeInTheDocument();
  });

  it("carga las formas de pago (F_FOP) en el desplegable", async () => {
    mockFormas.mockResolvedValue([
      { codigo: "03", nombre: "Transferencia" },
      { codigo: "01", nombre: "Contado" },
    ]);
    render(<EmitFactusolModal {...base()} />);
    expect(await screen.findByRole("option", { name: "Transferencia" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Contado" })).toBeInTheDocument();
  });

  it("envía las opciones elegidas por el operador", async () => {
    mockFormas.mockResolvedValue([{ codigo: "03", nombre: "Transferencia" }]);
    const onSubmit = jest.fn();
    const user = userEvent.setup();
    render(<EmitFactusolModal {...base({ onSubmit })} />);
    await user.type(screen.getByLabelText("Serie"), "A");
    await user.type(screen.getByLabelText("Observaciones"), "Pago 30d");
    await user.selectOptions(await screen.findByLabelText("Forma de pago"), "03");
    await user.click(screen.getByRole("button", { name: "Emitir factura" }));
    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({
        tipfac: "1", serfac: "A", fopfac: "03", comfac: "Pago 30d",
      }),
    );
  });

  it("cancelar llama onCancel sin emitir", async () => {
    const onCancel = jest.fn();
    const onSubmit = jest.fn();
    const user = userEvent.setup();
    render(<EmitFactusolModal {...base({ onCancel, onSubmit })} />);
    await user.click(await screen.findByRole("button", { name: "Cancelar" }));
    expect(onCancel).toHaveBeenCalled();
    expect(onSubmit).not.toHaveBeenCalled();
  });
});
