import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { EmitFactusolButton } from "./EmitFactusolButton";
import { emitFactusolInvoice, getFactusolInvoiceStatus } from "../../lib/erpApi";

jest.mock("../../lib/erpApi", () => ({
  emitFactusolInvoice: jest.fn(),
  getFactusolInvoiceStatus: jest.fn(),
}));

const mockEmit = emitFactusolInvoice as jest.Mock;
const mockStatus = getFactusolInvoiceStatus as jest.Mock;

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
});

describe("EmitFactusolButton", () => {
  it("muestra el badge si ya está facturado", () => {
    render(<EmitFactusolButton {...props({ factusolInvoiceNumber: "526067" })} />);
    expect(screen.getByLabelText("Factura FACTUSOL")).toHaveTextContent("526067");
    expect(screen.queryByRole("button", { name: /Emitir/ })).not.toBeInTheDocument();
  });

  it("no renderiza nada si está facturado externamente", () => {
    const { container } = render(
      <EmitFactusolButton {...props({ invoiceStatus: "already_invoiced_externally" })} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("deshabilita el botón si el pedido no tiene empresa", () => {
    render(<EmitFactusolButton {...props({ companyId: null })} />);
    expect(screen.getByRole("button", { name: /Emitir factura FACTUSOL/ })).toBeDisabled();
  });

  it("confirma, emite y hace polling hasta invoiced", async () => {
    mockEmit.mockResolvedValue({ job_id: "job-1", order_id: "o1", status: "queued" });
    mockStatus.mockResolvedValue({ status: "invoiced", codfac: "526067" });
    const onInvoiced = jest.fn();
    const user = userEvent.setup();
    render(<EmitFactusolButton {...props()} onInvoiced={onInvoiced} />);

    await user.click(screen.getByRole("button", { name: /Emitir factura FACTUSOL/ }));
    // Modal de confirmación con advertencia irreversible.
    expect(screen.getByText(/no es reversible/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Emitir factura" }));

    await waitFor(() => expect(mockEmit).toHaveBeenCalledWith("o1"));
    await waitFor(() => expect(mockStatus).toHaveBeenCalledWith("o1", "job-1"));
    // Al facturar, el componente pasa a mostrar el badge con el CODFAC.
    expect(await screen.findByLabelText("Factura FACTUSOL")).toHaveTextContent("526067");
    expect(onInvoiced).toHaveBeenCalledWith("526067");
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
