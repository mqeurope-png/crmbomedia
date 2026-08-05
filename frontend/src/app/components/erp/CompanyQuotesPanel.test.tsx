import { render, screen, waitFor } from "@testing-library/react";
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
  duplicateFactusolQuote: jest.fn(),
  searchFactusolArticles: jest.fn(),
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

  it("convertir en pedido encola, espera al job y avisa del pedido creado", async () => {
    mockList.mockResolvedValue({ items: [quote()], unlinked: false });
    mockConvert.mockResolvedValue({ job_id: "job-c1", status: "queued", codpre: "77" });
    mockStatus.mockResolvedValue({
      status: "finished",
      result: { order_id: "o-1", order_number: "MANUAL-000004" },
    });
    const onOrderCreated = jest.fn();
    const user = userEvent.setup();
    render(<CompanyQuotesPanel {...base({ onOrderCreated })} />);

    await user.click(await screen.findByRole("button", { name: "Convertir en pedido" }));

    await waitFor(() => expect(mockConvert).toHaveBeenCalledWith("77"));
    expect(await screen.findByText(/MANUAL-000004/)).toBeInTheDocument();
    expect(onOrderCreated).toHaveBeenCalledWith("o-1");
  });

  it("un job fallido muestra el error de FACTUSOL", async () => {
    mockList.mockResolvedValue({ items: [quote()], unlinked: false });
    mockConvert.mockResolvedValue({ job_id: "job-c1", status: "queued", codpre: "77" });
    mockStatus.mockResolvedValue({ status: "failed", error: "CODPRE duplicado" });
    const user = userEvent.setup();
    render(<CompanyQuotesPanel {...base()} />);

    await user.click(await screen.findByRole("button", { name: "Convertir en pedido" }));
    expect(await screen.findByText("CODPRE duplicado")).toBeInTheDocument();
  });
});
