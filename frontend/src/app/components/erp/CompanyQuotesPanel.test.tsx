import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CompanyQuotesPanel } from "./CompanyQuotesPanel";
import {
  convertFactusolQuoteToOrder,
  getQuoteJobStatus,
  listFactusolQuotes,
} from "../../lib/erpApi";

jest.mock("../../lib/erpApi", () => ({
  convertFactusolQuoteToOrder: jest.fn(),
  getQuoteJobStatus: jest.fn(),
  listFactusolQuotes: jest.fn(),
  // El modal hijo usa estos; se dejan inertes.
  createFactusolQuote: jest.fn(),
  updateFactusolQuote: jest.fn(),
  getFactusolQuote: jest.fn(),
  getFactusolCustomerAddresses: jest.fn(),
  waitForQuoteJob: jest.fn(),
  duplicateFactusolQuote: jest.fn(),
  searchFactusolArticles: jest.fn(),
  // Fase 2: catálogos del paso de pago.
  getContrapartidas: jest.fn(() => Promise.resolve([
    { codigo: "6", nombre: "Bomedia Sabadell" }, { codigo: "8", nombre: "Streamtec Sabadell" },
  ])),
  getFactusolFormasPago: jest.fn(() => Promise.resolve([
    { codigo: "002", nombre: "Transferencia" },
  ])),
}));
const mockList = listFactusolQuotes as jest.Mock;
const mockConvert = convertFactusolQuoteToOrder as jest.Mock;
const mockStatus = getQuoteJobStatus as jest.Mock;

function quote(over = {}) {
  return {
    codpre: "77", referencia: "Instalación sala 3", fecha: "2026-08-01",
    clipre: "55555", cliente_nombre: "Acme SL", base: 100, iva: 21, total: 121,
    ...over,
  };
}

beforeEach(() => {
  mockList.mockReset();
  mockConvert.mockReset();
  mockStatus.mockReset();
  mockList.mockResolvedValue({ items: [], unlinked: false });
});

function base(over = {}) {
  return {
    companyId: "c1", companyName: "Acme SL", factusolCodcli: "55555", ...over,
  };
}

describe("CompanyQuotesPanel", () => {
  it("empresa sin vínculo FACTUSOL: pide vincular en vez de listar", async () => {
    render(<CompanyQuotesPanel {...base({ factusolCodcli: null })} />);
    expect(
      await screen.findByText(/no está vinculada a un cliente de FACTUSOL/),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Nueva proforma" })).not.toBeInTheDocument();
  });

  it("lista las proformas del cliente", async () => {
    mockList.mockResolvedValue({ items: [quote()], unlinked: false });
    render(<CompanyQuotesPanel {...base()} />);
    expect(await screen.findByText("Instalación sala 3")).toBeInTheDocument();
    expect(screen.getByText("121.00 €")).toBeInTheDocument();
  });

  it("cada proforma ofrece Editar además de Convertir en pedido", async () => {
    mockList.mockResolvedValue({ items: [quote()], unlinked: false });
    render(<CompanyQuotesPanel {...base()} />);
    expect(await screen.findByRole("button", { name: "Editar" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Convertir en pedido" }))
      .toBeInTheDocument();
  });

  it("convertir en pedido pasa por el paso de pago, encola, espera al job y avisa del pedido y del albarán", async () => {
    mockList.mockResolvedValue({ items: [quote()], unlinked: false });
    mockConvert.mockResolvedValue({ job_id: "job-c1", status: "queued", codpre: "77" });
    mockStatus.mockResolvedValue({
      status: "finished",
      result: {
        order_id: "o-1", order_number: "PRO-000077",
        albaran: { numero: "5-500008", status: "created" },
      },
    });
    const onOrderCreated = jest.fn();
    const user = userEvent.setup();
    render(<CompanyQuotesPanel {...base({ onOrderCreated })} />);

    await user.click(await screen.findByRole("button", { name: "Convertir en pedido" }));
    // Fase 2: paso de pago antes de convertir. Por defecto «sin pago».
    const dialog = await screen.findByRole("dialog", { name: "Convertir proforma en pedido" });
    expect(within(dialog).getByLabelText("Sin pago")).toBeChecked();
    await user.click(within(dialog).getByRole("button", { name: "Crear pedido y albarán" }));

    await waitFor(() => expect(mockConvert).toHaveBeenCalledWith("77", expect.objectContaining({
      create_albaran: true,
      payment: expect.objectContaining({ paid: false, contrapartida: null }),
    })));
    const notice = await screen.findByText(/PRO-000077/);
    expect(notice).toHaveTextContent("Albarán FACTUSOL 5-500008 creado");
    expect(notice).toHaveTextContent("Sin pago: pendiente");
    expect(onOrderCreated).toHaveBeenCalledWith("o-1");
  });

  it("«Pagado» exige la cuenta y viaja al job (opción B: sin factura)", async () => {
    mockList.mockResolvedValue({ items: [quote()], unlinked: false });
    mockConvert.mockResolvedValue({ job_id: "job-c2", status: "queued", codpre: "77" });
    mockStatus.mockResolvedValue({
      status: "finished",
      result: { order_id: "o-2", order_number: "PRO-000077", albaran_error: "no cuadra" },
    });
    const user = userEvent.setup();
    render(<CompanyQuotesPanel {...base()} />);
    await user.click(await screen.findByRole("button", { name: "Convertir en pedido" }));
    const dialog = await screen.findByRole("dialog", { name: "Convertir proforma en pedido" });
    await user.click(within(dialog).getByLabelText("Pagado"));
    const confirm = within(dialog).getByRole("button", { name: "Crear pedido y albarán" });
    expect(confirm).toBeDisabled();                      // sin cuenta no hay pago
    // El catálogo de cuentas llega de forma asíncrona.
    await within(dialog).findByRole("option", { name: "8 · Streamtec Sabadell" });
    await user.selectOptions(within(dialog).getByLabelText("Cuenta del cobro"), "8");
    expect(within(dialog).getByText(/No se emite ninguna factura/)).toBeInTheDocument();
    await user.click(confirm);
    await waitFor(() => expect(mockConvert).toHaveBeenCalledWith("77", expect.objectContaining({
      payment: expect.objectContaining({ paid: true, contrapartida: "8" }),
    })));
    const notice = await screen.findByText(/PRO-000077/);
    expect(notice).toHaveTextContent("El albarán NO se creó: no cuadra");
    expect(notice).toHaveTextContent("Pago apuntado");
  });

  it("un job fallido muestra el error de FACTUSOL", async () => {
    mockList.mockResolvedValue({ items: [quote()], unlinked: false });
    mockConvert.mockResolvedValue({ job_id: "job-c1", status: "queued", codpre: "77" });
    mockStatus.mockResolvedValue({ status: "failed", error: "CODPRE duplicado" });
    const user = userEvent.setup();
    render(<CompanyQuotesPanel {...base()} />);

    await user.click(await screen.findByRole("button", { name: "Convertir en pedido" }));
    const dialog = await screen.findByRole("dialog", { name: "Convertir proforma en pedido" });
    await user.click(within(dialog).getByRole("button", { name: "Crear pedido y albarán" }));
    expect(await screen.findByText("CODPRE duplicado")).toBeInTheDocument();
  });
});
