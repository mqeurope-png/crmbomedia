import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { EmitFactusolModal } from "./EmitFactusolModal";
import { getFactusolFormasPago, getFactusolSeries, getMencionesLegales } from "../../lib/erpApi";

jest.mock("../../lib/erpApi", () => ({
  getFactusolFormasPago: jest.fn(),
  getFactusolSeries: jest.fn(),
  getMencionesLegales: jest.fn(),
}));
const mockFormas = getFactusolFormasPago as jest.Mock;
const mockSeries = getFactusolSeries as jest.Mock;
const mockMenciones = getMencionesLegales as jest.Mock;

/** Menciones de Configuración ERP: una revisada y otra de referencia sin revisar. */
const MENCIONES = {
  items: [
    { id: "fuera-ue", nombre: "Fuera de la UE", revisado: true,
      texto: "Operación exenta de IVA por exportación." },
    { id: "canarias", nombre: "Canarias", revisado: false,
      texto: "Operación exenta de IVA por exportación. Destino: Canarias." },
  ],
  max: 255,
  verifactu_activo: false,
};

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
  mockMenciones.mockReset();
  mockMenciones.mockResolvedValue(MENCIONES);
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

  it("mención legal: solo las revisadas se pueden elegir; enseña su texto y manda su id", async () => {
    const onSubmit = jest.fn();
    const user = userEvent.setup();
    render(<EmitFactusolModal {...base({ onSubmit })} />);
    const select = await screen.findByLabelText("Mención legal");
    expect(select).toHaveValue("");
    expect(screen.getByRole("option", { name: "Ninguna" })).toBeEnabled();
    expect(screen.getByRole("option", { name: "Fuera de la UE" })).toBeEnabled();
    expect(screen.getByRole("option", { name: "Canarias (sin revisar)" })).toBeDisabled();
    expect(screen.getByText("Va en las observaciones de la factura (salen en el PDF).")).toBeInTheDocument();
    await user.selectOptions(select, "fuera-ue");
    expect(screen.getByText("«Operación exenta de IVA por exportación.»")).toBeInTheDocument();
    expect(screen.queryByLabelText("Texto de la mención")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Emitir factura" }));
    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({ mencion_id: "fuera-ue", mencion_texto: null }),
    );
  });

  it("mención legal: «Texto libre…» abre el texto (máx. 255) y lo manda como mencion_texto", async () => {
    const onSubmit = jest.fn();
    const user = userEvent.setup();
    render(<EmitFactusolModal {...base({ onSubmit })} />);
    await user.selectOptions(await screen.findByLabelText("Mención legal"), "Texto libre…");
    const texto = screen.getByLabelText("Texto de la mención");
    expect(texto).toHaveAttribute("maxLength", "255");
    await user.type(texto, "Inversión del sujeto pasivo.");
    expect(screen.getByText("28/255")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Emitir factura" }));
    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({ mencion_id: null, mencion_texto: "Inversión del sujeto pasivo." }),
    );
  });

  it("mención legal: «Texto libre…» vacío no deja emitir (saldría sin mención sin avisar)", async () => {
    const onSubmit = jest.fn();
    const user = userEvent.setup();
    render(<EmitFactusolModal {...base({ onSubmit })} />);
    const select = await screen.findByLabelText("Mención legal");
    await user.selectOptions(select, "Texto libre…");
    const emitir = screen.getByRole("button", { name: "Emitir factura" });
    expect(emitir).toBeDisabled();
    expect(screen.getByText(/Escribe el texto de la mención, o elige otra opción/)).toBeInTheDocument();
    // Solo espacios tampoco vale.
    await user.type(screen.getByLabelText("Texto de la mención"), "   ");
    expect(emitir).toBeDisabled();
    await user.type(screen.getByLabelText("Texto de la mención"), "Exenta.");
    expect(emitir).toBeEnabled();
    expect(screen.queryByText(/Escribe el texto de la mención/)).not.toBeInTheDocument();
    // Volver a «Ninguna» también deja emitir (sin mención, a sabiendas).
    await user.clear(screen.getByLabelText("Texto de la mención"));
    expect(emitir).toBeDisabled();
    await user.selectOptions(select, "Ninguna");
    expect(emitir).toBeEnabled();
    await user.click(emitir);
    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({ mencion_id: null, mencion_texto: null }),
    );
  });

  it("sin tocar la mención no manda ninguna", async () => {
    const onSubmit = jest.fn();
    const user = userEvent.setup();
    render(<EmitFactusolModal {...base({ onSubmit })} />);
    await screen.findByLabelText("Mención legal");
    await user.click(screen.getByRole("button", { name: "Emitir factura" }));
    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({ mencion_id: null, mencion_texto: null }),
    );
  });

  it("si la lista de menciones no carga, el campo no sale y se emite igual", async () => {
    mockMenciones.mockRejectedValue(new Error("caído"));
    const onSubmit = jest.fn();
    const user = userEvent.setup();
    render(<EmitFactusolModal {...base({ onSubmit })} />);
    await screen.findByRole("option", { name: "5 · Streamtec" });
    expect(screen.queryByLabelText("Mención legal")).not.toBeInTheDocument();
    expect(screen.queryByText(/Va en las observaciones/)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Emitir factura" }));
    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ serie: null, mencion_id: null }));
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
