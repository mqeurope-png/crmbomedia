import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import NewManualOrderPage from "./page";
import { listContacts } from "../../../lib/api";
import { getCompany, listCompanies } from "../../../lib/companiesApi";
import {
  createOrder,
  listFactusolQuotes,
  previewOrderFromFactusol,
  searchFactusolArticles,
  searchFactusolCustomers,
  searchFactusolQuotes,
} from "../../../lib/erpApi";

const push = jest.fn();
const refresh = jest.fn();
jest.mock("next/navigation", () => ({
  useRouter: () => ({ push, refresh }),
  useSearchParams: () => new URLSearchParams(""),
}));
jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ children, href, className }: { children: React.ReactNode; href: string; className?: string }) => (
    <a href={href} className={className}>{children}</a>
  ),
}));
jest.mock("../../../lib/api", () => ({ listContacts: jest.fn() }));
jest.mock("../../../lib/companiesApi", () => ({
  listCompanies: jest.fn(),
  createCompany: jest.fn(),
  getCompany: jest.fn(),
}));
jest.mock("../../../lib/erpApi", () => ({
  createOrder: jest.fn(),
  createFactusolCustomer: jest.fn(),
  createFactusolCustomerAndLink: jest.fn(),
  linkFactusolCustomer: jest.fn(),
  searchFactusolCustomers: jest.fn(),
  listFactusolQuotes: jest.fn(),
  getFactusolQuote: jest.fn(),
  searchFactusolArticles: jest.fn(),
  previewOrderFromFactusol: jest.fn(),
  searchFactusolQuotes: jest.fn(),
  listFactusolDocuments: jest.fn(() => Promise.resolve({ items: [], total: 0 })),
}));

/** Empresa CRM vinculada a FACTUSOL, con datos VIEJOS distintos de F_CLI. */
const LINKED = {
  id: "c1", name: "Acme SL", tax_id: "B00000000",
  address_line: "Calle Vieja 9", city: "Lleida", postal_code: "25001",
  state: "Lleida", country: "España", factusol_company_id: "55555",
};

const F_CLI = {
  codcli: "55555", nombre: "Acme", nif: "B12345678", nofcli: "ACME SL", noccli: "Acme",
  nifcli: "B12345678", domcli: "C/ Mayor 1", pobcli: "Madrid", cpocli: "28001",
  procli: "Madrid", paicli: "724", pais_iso2: "ES", emacli: null, telcli: null,
  crm_link: { type: "company" as const, id: "c1", name: "Acme SL" },
  factusol_matches_crm_id: "c1",
};

/** Espera a que el datalist de empresas (debounced) tenga la empresa. */
async function waitForCompanyOption() {
  await waitFor(() =>
    expect(document.querySelector('#erp-new-order-companies option[value="Acme SL"]'))
      .not.toBeNull(),
  );
}

const QUOTE = {
  codpre: "574", referencia: "Cabezal + SAT", fecha: "2026-09-01", clipre: "55555",
  cliente_nombre: "Roca Joiers", base: 355, iva: 74.55, total: 429.55,
};

beforeEach(() => {
  (listCompanies as jest.Mock).mockReset();
  (listCompanies as jest.Mock).mockResolvedValue({ items: [LINKED], total: 1 });
  (listContacts as jest.Mock).mockReset();
  (listContacts as jest.Mock).mockResolvedValue({ items: [], total: 0 });
  (getCompany as jest.Mock).mockReset();
  (createOrder as jest.Mock).mockReset();
  (createOrder as jest.Mock).mockResolvedValue({ id: "new-order-1" });
  (listFactusolQuotes as jest.Mock).mockReset();
  (listFactusolQuotes as jest.Mock).mockResolvedValue({ items: [QUOTE], unlinked: false });
  (searchFactusolArticles as jest.Mock).mockReset();
  (searchFactusolArticles as jest.Mock).mockResolvedValue([]);
  (searchFactusolCustomers as jest.Mock).mockReset();
  (searchFactusolCustomers as jest.Mock).mockResolvedValue([F_CLI]);
  (searchFactusolQuotes as jest.Mock).mockReset();
  (searchFactusolQuotes as jest.Mock).mockResolvedValue([QUOTE]);
  (previewOrderFromFactusol as jest.Mock).mockReset();
  (previewOrderFromFactusol as jest.Mock).mockResolvedValue({
    doc_type: "presupuestos", serie: 1, codigo: 574, numero: "1-000574",
    fecha: "2026-09-01", total: 429.55, referencia: "Cabezal + SAT", estado: "0",
    estado_label: "Pendiente", forma_pago: "002", forma_pago_nombre: "Transferencia",
    cliente_codigo: "55555", cliente_nombre: "Roca Joiers",
    company_id: "c1", company_name: "Acme SL", company_linked: true,
    lines: [
      { position: 1, codart: "MBO", description: "Cabezal MBO 250", quantity: 1,
        unit_price: 250, line_total: 250, discount_pct: 0, iva_pct: null },
      { position: 2, codart: "SAT", description: "Hora SAT", quantity: 1,
        unit_price: 105, line_total: 105, discount_pct: 0, iva_pct: null },
    ],
    order_number: "PRO-000574", external_id: "574", already_imported: null,
  });
});

describe("Alta de pedido — buscador de proformas y precarga desde FACTUSOL", () => {
  it("test_alta_pedido_buscador_proformas: busca, lista y carga la elegida en el pedido", async () => {
    const user = userEvent.setup();
    render(<NewManualOrderPage />);
    // Por defecto «Presupuesto / proforma»: el buscador sustituye a serie+número.
    expect(screen.queryByLabelText("Número del documento FACTUSOL")).not.toBeInTheDocument();
    await user.type(screen.getByLabelText("Buscar proforma"), "roca");
    await waitFor(() =>
      expect(searchFactusolQuotes).toHaveBeenCalledWith("roca", { days_back: 365 }),
    );
    expect(await screen.findByText("Cabezal + SAT")).toBeInTheDocument();
    expect(screen.getByText("Roca Joiers")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Cargar en el pedido" }));
    await waitFor(() =>
      expect(previewOrderFromFactusol).toHaveBeenCalledWith("presupuestos", 1, 574),
    );
    expect(await screen.findByText(/presupuesto 1-000574 cargado: 2 línea\(s\)/)).toBeInTheDocument();
    expect(screen.getByLabelText("SKU línea 1")).toHaveValue("MBO");
    expect(screen.getByLabelText("Descripción línea 2")).toHaveValue("Hora SAT");
    expect(screen.getByText(/Origen:/)).toHaveTextContent("presupuesto FACTUSOL 1-000574");
  });

  it("con empresa elegida, el buscador lista sus proformas sin escribir nada", async () => {
    const user = userEvent.setup();
    render(<NewManualOrderPage />);
    await waitForCompanyOption();
    await user.type(screen.getByPlaceholderText("Buscar empresa…"), "Acme SL");
    await waitFor(() =>
      expect(listFactusolQuotes).toHaveBeenCalledWith({ company_id: "c1", days_back: 365 }),
    );
    expect((await screen.findAllByText("Cabezal + SAT")).length).toBeGreaterThan(0);
  });

  it("test_alta_pedido_precarga_empresa_desde_factusol: empresa vinculada → NIF y dirección de F_CLI", async () => {
    const user = userEvent.setup();
    render(<NewManualOrderPage />);
    await waitForCompanyOption();
    await user.type(screen.getByPlaceholderText("Buscar empresa…"), "Acme SL");
    await waitFor(() =>
      expect(searchFactusolCustomers).toHaveBeenCalledWith("55555", "codcli"),
    );
    // FACTUSOL manda: pisa el NIF y la dirección viejos del CRM.
    expect(await screen.findByDisplayValue("B12345678")).toBeInTheDocument();
    expect(screen.getByLabelText("Dirección de envío")).toHaveValue("C/ Mayor 1");
    expect(screen.getByLabelText("Ciudad de envío")).toHaveValue("Madrid");
    expect(screen.getByLabelText("Código postal de envío")).toHaveValue("28001");
    expect(screen.getByLabelText("País de envío")).toHaveValue("España");
    expect(screen.getByRole("status")).toHaveTextContent(
      /Datos del cliente FACTUSOL nº 55555 cargados en el pedido/,
    );
    expect(screen.queryByDisplayValue("Calle Vieja 9")).not.toBeInTheDocument();
  });
});
