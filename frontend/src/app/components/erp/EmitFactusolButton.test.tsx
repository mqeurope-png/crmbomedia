import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { EmitFactusolButton } from "./EmitFactusolButton";
import {
  emitFactusolInvoice,
  getFactusolInvoiceStatus,
  getFactusolFormasPago,
  getFactusolSeries,
} from "../../lib/erpApi";

jest.mock("../../lib/erpApi", () => ({
  emitFactusolInvoice: jest.fn(),
  getFactusolInvoiceStatus: jest.fn(),
  getFactusolFormasPago: jest.fn(),
  getFactusolSeries: jest.fn(),
}));

const mockEmit = emitFactusolInvoice as jest.Mock;
const mockStatus = getFactusolInvoiceStatus as jest.Mock;
const mockFormas = getFactusolFormasPago as jest.Mock;
const mockSeries = getFactusolSeries as jest.Mock;

function props(over = {}) {
  return {
    orderId: "o1", invoiceStatus: "not_invoiced",
    factusolInvoiceNumber: null, totalAmount: 200, currency: "EUR",
    companyId: "c1", ...over,
  };
}

beforeEach(() => {
  mockEmit.mockReset();
  mockStatus.mockReset();
  mockFormas.mockReset();
  mockFormas.mockResolvedValue([]);
  mockSeries.mockReset();
  mockSeries.mockResolvedValue({
    items: [{ serie: 5, nombre: "Streamtec", is_default: true, is_known: true }],
    default: 5,
  });
});

describe("EmitFactusolButton", () => {
  it("muestra el badge si ya está facturado (props)", () => {
    render(<EmitFactusolButton {...props({ factusolInvoiceNumber: "526067" })} />);
    expect(screen.getByLabelText("Factura FACTUSOL")).toHaveTextContent("526067");
    expect(screen.queryByRole("button", { name: /Emitir/ })).not.toBeInTheDocument();
  });

  it("muestra el badge si el estado en vivo indica factura existente", () => {
    render(
      <EmitFactusolButton
        {...props({ factusolStatus: { status: "invoiced", codfac: "260695" } })}
      />,
    );
    expect(screen.getByLabelText("Factura FACTUSOL")).toHaveTextContent("260695");
  });

  it("muestra badge de albarán y aún permite emitir", () => {
    render(
      <EmitFactusolButton
        {...props({ factusolStatus: { status: "albaran", albaran_codigo: "5001" } })}
      />,
    );
    expect(screen.getByLabelText("Albarán FACTUSOL")).toHaveTextContent("5001");
    expect(
      screen.getByRole("button", { name: /Emitir factura FACTUSOL/ }),
    ).toBeInTheDocument();
  });

  it("no renderiza nada si está facturado externamente", () => {
    const { container } = render(
      <EmitFactusolButton {...props({ invoiceStatus: "already_invoiced_externally" })} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("confirma (modal simple), emite y hace polling hasta invoiced", async () => {
    mockEmit.mockResolvedValue({ job_id: "job-1", order_id: "o1", status: "queued" });
    mockStatus.mockResolvedValue({ status: "invoiced", codfac: "526067" });
    const onInvoiced = jest.fn();
    const user = userEvent.setup();
    render(<EmitFactusolButton {...props()} onInvoiced={onInvoiced} />);

    await user.click(screen.getByRole("button", { name: /Emitir factura FACTUSOL/ }));
    // Modal de confirmación con advertencia irreversible.
    expect(screen.getByText(/no es reversible/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Emitir factura" }));

    await waitFor(() => expect(mockEmit).toHaveBeenCalledWith("o1", undefined));
    await waitFor(() => expect(mockStatus).toHaveBeenCalledWith("o1", "job-1"));
    expect(await screen.findByLabelText("Factura FACTUSOL")).toHaveTextContent("526067");
    expect(onInvoiced).toHaveBeenCalledWith("526067");
  });

  it("con enableOptions abre el modal de 5 campos y emite con opciones", async () => {
    mockEmit.mockResolvedValue({ job_id: "job-9", order_id: "o1", status: "queued" });
    mockStatus.mockResolvedValue({ status: "invoiced", codfac: "526067" });
    mockFormas.mockResolvedValue([{ codigo: "03", nombre: "Transferencia" }]);
    const user = userEvent.setup();
    render(<EmitFactusolButton {...props({ enableOptions: true })} />);

    await user.click(screen.getByRole("button", { name: /Emitir factura FACTUSOL/ }));
    // Los 5 campos del modal.
    expect(screen.getByLabelText("Tipo")).toHaveValue("1");
    expect(screen.getByLabelText("Empresa emisora / Serie")).toBeInTheDocument();
    expect(screen.getByLabelText("Fecha de emisión")).toBeInTheDocument();
    expect(screen.getByLabelText("Observaciones")).toBeInTheDocument();
    // Forma de pago cargada de F_FOP.
    expect(await screen.findByRole("option", { name: "Transferencia" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Emitir factura" }));
    await waitFor(() => expect(mockEmit).toHaveBeenCalled());
    const [oid, opts] = mockEmit.mock.calls[0];
    expect(oid).toBe("o1");
    expect(opts.tipfac).toBe("1");
    expect(await screen.findByLabelText("Factura FACTUSOL")).toHaveTextContent("526067");
  });

  it("muestra error si el polling devuelve failed", async () => {
    mockEmit.mockResolvedValue({ job_id: "job-2", order_id: "o1", status: "queued" });
    mockStatus.mockResolvedValue({ status: "failed", error: "boom" });
    const user = userEvent.setup();
    render(<EmitFactusolButton {...props()} />);
    await user.click(screen.getByRole("button", { name: /Emitir factura FACTUSOL/ }));
    await user.click(screen.getByRole("button", { name: "Emitir factura" }));
    expect(await screen.findByText(/Error: boom/)).toBeInTheDocument();
  });

  it("cancelar cierra el modal sin emitir", async () => {
    const user = userEvent.setup();
    render(<EmitFactusolButton {...props()} />);
    await user.click(screen.getByRole("button", { name: /Emitir factura FACTUSOL/ }));
    await user.click(screen.getByRole("button", { name: "Cancelar" }));
    expect(mockEmit).not.toHaveBeenCalled();
  });
});
