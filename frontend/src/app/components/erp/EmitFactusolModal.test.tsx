import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { EmitFactusolModal } from "./EmitFactusolModal";
import { getFactusolFormasPago, getFactusolSeries } from "../../lib/erpApi";

jest.mock("../../lib/erpApi", () => ({
  getFactusolFormasPago: jest.fn(),
  getFactusolSeries: jest.fn(),
}));
const mockFormas = getFactusolFormasPago as jest.Mock;
const mockSeries = getFactusolSeries as jest.Mock;

/** ERP-E2 — las series son las empresas emisoras; Streamtec (5) por defecto. */
const SERIES = {
  items: [
    { serie: 1, nombre: "Bomedia", is_default: false, is_known: true },
    { serie: 2, nombre: "MQ Europe", is_default: false, is_known: true },
    { serie: 5, nombre: "Streamtec", is_default: true, is_known: true },
    { serie: 7, nombre: "Serie 7", is_default: false, is_known: false },
  ],
  default: 5,
};

beforeEach(() => {
  mockFormas.mockReset();
  mockFormas.mockResolvedValue([]);
  mockSeries.mockReset();
  mockSeries.mockResolvedValue(SERIES);
});

function base(over = {}) {
  return {
    totalAmount: 186.34, currency: "EUR",
    onSubmit: jest.fn(), onCancel: jest.fn(), ...over,
  };
}

describe("EmitFactusolModal", () => {
  it("renderiza los 4 campos y la advertencia, SIN campo «Tipo»", async () => {
    render(<EmitFactusolModal {...base()} />);
    expect(
      await screen.findByLabelText("Empresa emisora / Serie"),
    ).toBeInTheDocument();
    // ERP-E2-fix2: «Tipo» era un malentendido (TIPFAC es la serie) y su
    // default "1" sellaba todas las facturas como Bomedia.
    expect(screen.queryByLabelText("Tipo")).not.toBeInTheDocument();
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

  it("por defecto hereda la serie del pedido y ofrece las empresas", async () => {
    render(<EmitFactusolModal {...base()} />);
    expect(
      await screen.findByRole("option", { name: "5 · Streamtec" }),
    ).toBeInTheDocument();
    // ERP-E2-fix1: sin selección → el backend hereda la serie del F_PCL.
    expect(screen.getByLabelText("Empresa emisora / Serie")).toHaveValue("");
    expect(screen.getByText(/serie con la que el pedido ya está/i)).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "2 · MQ Europe" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "1 · Bomedia" })).toBeInTheDocument();
    // Las series sin nombre siguen disponibles, detrás de las conocidas.
    expect(screen.getByRole("option", { name: "7 · Serie 7" })).toBeInTheDocument();
  });

  it("envía la serie elegida (no un texto libre) junto al resto de opciones", async () => {
    mockFormas.mockResolvedValue([{ codigo: "03", nombre: "Transferencia" }]);
    const onSubmit = jest.fn();
    const user = userEvent.setup();
    render(<EmitFactusolModal {...base({ onSubmit })} />);
    await screen.findByRole("option", { name: "2 · MQ Europe" });
    await user.selectOptions(
      screen.getByLabelText("Empresa emisora / Serie"), "2",
    );
    await user.type(screen.getByLabelText("Observaciones"), "Pago 30d");
    await user.selectOptions(await screen.findByLabelText("Forma de pago"), "03");
    await user.click(screen.getByRole("button", { name: "Emitir factura" }));
    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({ serie: 2, fopfac: "03", comfac: "Pago 30d" }),
    );
    // `serfac` era la columna fantasma; `tipfac`, el sellado a serie 1.
    expect(onSubmit.mock.calls[0][0]).not.toHaveProperty("serfac");
    expect(onSubmit.mock.calls[0][0]).not.toHaveProperty("tipfac");
  });

  it("sin tocar el selector manda serie null (= heredar la del pedido)", async () => {
    const onSubmit = jest.fn();
    const user = userEvent.setup();
    render(<EmitFactusolModal {...base({ onSubmit })} />);
    await screen.findByRole("option", { name: "5 · Streamtec" });
    await user.click(screen.getByRole("button", { name: "Emitir factura" }));
    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({ serie: null }),
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
