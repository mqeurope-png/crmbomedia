import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import NewManualOrderPage from "./page";
import { listContacts } from "../../../lib/api";
import { listCompanies } from "../../../lib/companiesApi";
import {
  createOrder,
  listFactusolQuotes,
  searchFactusolArticles,
  searchFactusolCustomers,
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
  searchFactusolQuotes: jest.fn(() => Promise.resolve([])),
  listFactusolDocuments: jest.fn(() => Promise.resolve({ items: [], total: 0 })),
  getContrapartidas: jest.fn(() => Promise.resolve([])),
  getFactusolFormasPago: jest.fn(() => Promise.resolve([])),
}));

const mockCreate = createOrder as jest.Mock;

const LINKED = {
  id: "c1", name: "Duplicoder SL", tax_id: "B12345678",
  address_line: "C Aribau 171", city: "Barcelona", postal_code: "08036",
  state: "Barcelona", country: "España", factusol_company_id: "55555",
};

const F_CLI = {
  codcli: "55555", nombre: "Duplicoder", nif: "B12345678",
  nofcli: "DUPLICODER, S.L.", noccli: "Duplicoder", nifcli: "B12345678",
  domcli: "C Aribau 171", pobcli: "Barcelona", cpocli: "08036",
  procli: "Barcelona", paicli: "724", pais_iso2: "ES", emacli: null, telcli: null,
  crm_link: { type: "company" as const, id: "c1", name: "Duplicoder SL" },
  factusol_matches_crm_id: "c1",
};

async function elegirEmpresaYLinea(user: ReturnType<typeof userEvent.setup>) {
  await waitFor(() =>
    expect(document.querySelector('#erp-new-order-companies option[value="Duplicoder SL"]'))
      .not.toBeNull(),
  );
  await user.type(screen.getByPlaceholderText("Buscar empresa…"), "Duplicoder SL");
  await user.type(screen.getByLabelText("Descripción línea 1"), "Tinta cyan");
  await user.clear(screen.getByLabelText("Cantidad línea 1"));
  await user.type(screen.getByLabelText("Cantidad línea 1"), "2");
  await user.type(screen.getByLabelText("Precio línea 1"), "40");
}

beforeEach(() => {
  push.mockReset();
  (listCompanies as jest.Mock).mockReset();
  (listCompanies as jest.Mock).mockResolvedValue({ items: [LINKED], total: 1 });
  (listContacts as jest.Mock).mockReset();
  (listContacts as jest.Mock).mockResolvedValue({ items: [], total: 0 });
  mockCreate.mockReset();
  mockCreate.mockResolvedValue({ id: "new-order-1" });
  (searchFactusolCustomers as jest.Mock).mockReset();
  (searchFactusolCustomers as jest.Mock).mockResolvedValue([F_CLI]);
  (listFactusolQuotes as jest.Mock).mockReset();
  (listFactusolQuotes as jest.Mock).mockResolvedValue({ items: [], unlinked: false });
  (searchFactusolArticles as jest.Mock).mockReset();
  (searchFactusolArticles as jest.Mock).mockResolvedValue([]);
});

describe("Alta de pedido manual — PORTES como línea aparte (como los web)", () => {
  it("los portes se mandan como su propia línea marcada, sin mezclarse con la mercancía", async () => {
    const user = userEvent.setup();
    render(<NewManualOrderPage />);
    await elegirEmpresaYLinea(user);
    await user.type(screen.getByLabelText("Portes"), "19");

    // El total del pedido suma los portes (80 + 19).
    expect(screen.getByText(/Total:/)).toHaveTextContent("99.00 EUR");
    expect(screen.getByText(/como línea aparte/)).toHaveTextContent(
      "Portes: 19.00 EUR como línea aparte",
    );

    const submit = screen.getByRole("button", { name: "Crear pedido" });
    await waitFor(() => expect(submit).toBeEnabled());
    await user.click(submit);
    await waitFor(() => expect(mockCreate).toHaveBeenCalled());
    const { lines } = mockCreate.mock.calls[0][0];
    expect(lines).toHaveLength(2);
    // La mercancía queda intacta (sin portes sumados a su precio).
    expect(lines[0]).toMatchObject({
      description: "Tinta cyan", quantity: 2, unit_price: 40,
    });
    expect(lines[0].is_shipping).toBeUndefined();
    // Y los portes van en su línea, marcados.
    expect(lines[1]).toMatchObject({
      description: "Portes", quantity: 1, unit_price: 19, is_shipping: true,
    });
  });

  it("sin portes no se añade ninguna línea de portes", async () => {
    const user = userEvent.setup();
    render(<NewManualOrderPage />);
    await elegirEmpresaYLinea(user);
    expect(screen.getByText(/Total:/)).toHaveTextContent("80.00 EUR");
    expect(screen.queryByText(/como línea aparte/)).not.toBeInTheDocument();
    const submit = screen.getByRole("button", { name: "Crear pedido" });
    await waitFor(() => expect(submit).toBeEnabled());
    await user.click(submit);
    await waitFor(() => expect(mockCreate).toHaveBeenCalled());
    const { lines } = mockCreate.mock.calls[0][0];
    expect(lines).toHaveLength(1);
    expect(lines[0].description).toBe("Tinta cyan");
  });

  it("portes a 0 tampoco crean línea", async () => {
    const user = userEvent.setup();
    render(<NewManualOrderPage />);
    await elegirEmpresaYLinea(user);
    await user.type(screen.getByLabelText("Portes"), "0");
    const submit = screen.getByRole("button", { name: "Crear pedido" });
    await waitFor(() => expect(submit).toBeEnabled());
    await user.click(submit);
    await waitFor(() => expect(mockCreate).toHaveBeenCalled());
    expect(mockCreate.mock.calls[0][0].lines).toHaveLength(1);
  });
});
