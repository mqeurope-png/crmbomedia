import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import NewManualOrderPage from "./page";
import { listContacts } from "../../../lib/api";
import { listCompanies } from "../../../lib/companiesApi";
import { createOrder } from "../../../lib/erpApi";

/** Lote 7 · P1 — el alta manual lleva un selector de SERIE (empresa emisora)
 *  que viaja en el payload como `factusol_serie`. */

const push = jest.fn();
const refresh = jest.fn();
jest.mock("next/navigation", () => ({
  useRouter: () => ({ push, refresh }),
  useSearchParams: () => new URLSearchParams(""),
}));
jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ children, href }: { children: React.ReactNode; href: string }) => (
    <a href={href}>{children}</a>
  ),
}));
jest.mock("../../../lib/api", () => ({ listContacts: jest.fn() }));
jest.mock("../../../lib/companiesApi", () => ({
  listCompanies: jest.fn(),
  createCompany: jest.fn(),
  getCompany: jest.fn(),
}));
jest.mock("../../../lib/erpApi", () => {
  const actual = jest.requireActual("../../../lib/erpApi");
  return {
    FACTUSOL_SERIES: actual.FACTUSOL_SERIES,
    createOrder: jest.fn(),
    createFactusolCustomer: jest.fn(),
    createFactusolCustomerAndLink: jest.fn(),
    linkFactusolCustomer: jest.fn(),
    searchFactusolCustomers: jest.fn(() => Promise.resolve([])),
    listFactusolQuotes: jest.fn(() => Promise.resolve({ items: [], unlinked: false })),
    getFactusolQuote: jest.fn(),
    searchFactusolArticles: jest.fn(() => Promise.resolve([])),
    previewOrderFromFactusol: jest.fn(),
    searchFactusolQuotes: jest.fn(() => Promise.resolve([])),
    listFactusolDocuments: jest.fn(() => Promise.resolve({ items: [], total: 0 })),
    getContrapartidas: jest.fn(() => Promise.resolve([])),
    getFactusolFormasPago: jest.fn(() => Promise.resolve([])),
  };
});

const COMPANY = {
  id: "c1", name: "Duplicoder SL", tax_id: "B12345678",
  address_line: "C Aribau 171", city: "Barcelona", postal_code: "08036",
  state: "Barcelona", country: "España", factusol_company_id: "55555",
};

beforeEach(() => {
  push.mockReset();
  (listCompanies as jest.Mock).mockReset();
  (listCompanies as jest.Mock).mockResolvedValue({ items: [COMPANY], total: 1 });
  (listContacts as jest.Mock).mockReset();
  (listContacts as jest.Mock).mockResolvedValue({ items: [], total: 0 });
  (createOrder as jest.Mock).mockReset();
  (createOrder as jest.Mock).mockResolvedValue({ id: "new-order-1" });
});

async function fillManual(user: ReturnType<typeof userEvent.setup>) {
  await waitFor(() => expect(listCompanies).toHaveBeenCalled());
  await user.type(screen.getByLabelText("Empresa"), "Duplicoder SL");
  await user.type(screen.getByLabelText("SKU línea 1"), "SKU-1");
  await user.type(screen.getByLabelText("Descripción línea 1"), "Artículo 1");
  await user.type(screen.getByLabelText("Precio línea 1"), "100");
}

describe("Nuevo pedido manual · selector de serie", () => {
  it("muestra el selector de serie con las 4 empresas emisoras", async () => {
    render(<NewManualOrderPage />);
    const select = await screen.findByLabelText("Serie del pedido");
    expect(select).toBeInTheDocument();
    const options = Array.from((select as HTMLSelectElement).options).map((o) => o.value);
    expect(options).toEqual(["1", "2", "4", "5"]);
    // Por defecto Streamtec (5), el default de resolve_serie.
    expect((select as HTMLSelectElement).value).toBe("5");
  });

  it("envía la serie elegida como factusol_serie en el alta manual", async () => {
    const user = userEvent.setup();
    render(<NewManualOrderPage />);
    await fillManual(user);
    await user.selectOptions(screen.getByLabelText("Serie del pedido"), "2");

    const submit = screen.getByRole("button", { name: "Crear pedido" });
    await waitFor(() => expect(submit).toBeEnabled());
    await user.click(submit);

    await waitFor(() => expect(createOrder).toHaveBeenCalled());
    expect((createOrder as jest.Mock).mock.calls[0][0].factusol_serie).toBe(2);
  });

  it("por defecto envía la serie 5 si no se cambia", async () => {
    const user = userEvent.setup();
    render(<NewManualOrderPage />);
    await fillManual(user);
    const submit = screen.getByRole("button", { name: "Crear pedido" });
    await waitFor(() => expect(submit).toBeEnabled());
    await user.click(submit);
    await waitFor(() => expect(createOrder).toHaveBeenCalled());
    expect((createOrder as jest.Mock).mock.calls[0][0].factusol_serie).toBe(5);
  });
});
