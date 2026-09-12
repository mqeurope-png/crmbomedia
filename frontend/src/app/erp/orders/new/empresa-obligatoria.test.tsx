import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import NewManualOrderPage from "./page";
import { listContacts } from "../../../lib/api";
import { listCompanies } from "../../../lib/companiesApi";
import {
  createFactusolCustomer,
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

const mockCompanies = listCompanies as jest.Mock;
const mockContacts = listContacts as jest.Mock;
const mockCreate = createOrder as jest.Mock;
const mockCreateCustomer = createFactusolCustomer as jest.Mock;
const mockSearchFac = searchFactusolCustomers as jest.Mock;

const UNLINKED = {
  id: "c-sin", name: "Sin FACTUSOL SL", tax_id: "B11111111",
  address_line: "C Nueva 5", city: "Lleida", postal_code: "25001",
  state: "Lleida", country: "España", factusol_company_id: null,
};
const LINKED = {
  id: "c1", name: "Duplicoder SL", tax_id: "B12345678",
  address_line: "C Aribau 171", city: "Barcelona", postal_code: "08036",
  state: "Barcelona", country: "España", factusol_company_id: "55555",
};

async function fillLine(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText("Descripción línea 1"), "Reparación láser");
  await user.type(screen.getByLabelText("Precio línea 1"), "90");
}

beforeEach(() => {
  push.mockReset();
  mockCompanies.mockReset();
  mockContacts.mockReset();
  mockCreate.mockReset();
  mockCreateCustomer.mockReset();
  mockSearchFac.mockReset();
  mockCompanies.mockResolvedValue({ items: [UNLINKED, LINKED], total: 2 });
  mockContacts.mockResolvedValue({ items: [], total: 0 });
  mockCreate.mockResolvedValue({ id: "new-order-1" });
  mockSearchFac.mockResolvedValue([]);
  (listFactusolQuotes as jest.Mock).mockResolvedValue({ items: [], unlinked: false });
  (searchFactusolArticles as jest.Mock).mockResolvedValue([]);
});

describe("Tarea B · «+ Nuevo pedido manual» exige EMPRESA vinculada a FACTUSOL", () => {
  it("test_pedido_manual_sin_empresa_no_se_crea: sin empresa el botón sigue deshabilitado y lo explica (un contacto solo no basta)", async () => {
    const user = userEvent.setup();
    render(<NewManualOrderPage />);
    await waitFor(() => expect(mockCompanies).toHaveBeenCalled());
    await fillLine(user);
    expect(screen.getByRole("button", { name: "Crear pedido" })).toBeDisabled();
    expect(screen.getByRole("note")).toHaveTextContent(/Elige una empresa de la lista \(obligatoria/);
    expect(screen.getByRole("note")).toHaveTextContent("Un contacto solo no basta");
    expect(mockCreate).not.toHaveBeenCalled();
  });

  it("test_pedido_manual_empresa_no_en_factusol_pide_crear: empresa sin F_CLI → «créala primero», botón «Crear en FACTUSOL», y tras crearla precarga y deja crear el pedido", async () => {
    mockCreateCustomer.mockResolvedValue({
      factusol_codcli: "9001", created: true, crm_type: "company", crm_id: "c-sin",
    });
    mockSearchFac.mockResolvedValue([{
      codcli: "9001", nombre: "SIN FACTUSOL SL", nif: "B11111111", nofcli: "SIN FACTUSOL SL",
      noccli: "SIN FACTUSOL SL", nifcli: "B11111111", domcli: "C Nueva 5 (F_CLI)",
      pobcli: "Lleida", cpocli: "25001", procli: "Lleida", paicli: "724", pais_iso2: "ES",
      emacli: null, telcli: null, crm_link: { type: "company", id: "c-sin", name: "Sin FACTUSOL SL" },
      factusol_matches_crm_id: "c-sin",
    }]);
    const user = userEvent.setup();
    render(<NewManualOrderPage />);
    await waitFor(() => expect(mockCompanies).toHaveBeenCalled());
    await user.type(screen.getByLabelText("Empresa"), "Sin FACTUSOL SL");
    await fillLine(user);
    // Bloqueado con el motivo y el atajo; no se crea nada.
    expect(await screen.findByRole("status")).toHaveTextContent(
      "«Sin FACTUSOL SL» aún no existe en FACTUSOL: créala primero",
    );
    const submit = screen.getByRole("button", { name: "Crear pedido" });
    expect(submit).toBeDisabled();
    expect(screen.getByRole("note")).toHaveTextContent(/vinculada a un cliente de FACTUSOL/);
    // Crear en FACTUSOL (Fase C) → vinculada → precarga (#392) → se puede crear.
    await user.click(screen.getByRole("button", { name: "Crear en FACTUSOL" }));
    await waitFor(() => expect(mockCreateCustomer).toHaveBeenCalledWith(expect.objectContaining({
      crm_type: "company", crm_id: "c-sin", nombre: "Sin FACTUSOL SL", nif: "B11111111",
    })));
    await waitFor(() => expect(mockSearchFac).toHaveBeenCalledWith("9001", "codcli"));
    await waitFor(() => expect(screen.getByLabelText("Dirección de envío")).toHaveValue("C Nueva 5 (F_CLI)"));
    expect(screen.getByRole("status")).toHaveTextContent(/Datos del cliente FACTUSOL nº 9001 cargados/);
    await waitFor(() => expect(submit).toBeEnabled());
    await user.click(submit);
    await waitFor(() => expect(mockCreate).toHaveBeenCalled());
    expect(mockCreate.mock.calls[0][0].company_id).toBe("c-sin");
  });

  it("test_pedido_manual_empresa_vinculada_ok: empresa vinculada → se crea el pedido con esa empresa", async () => {
    const user = userEvent.setup();
    render(<NewManualOrderPage />);
    await waitFor(() => expect(mockCompanies).toHaveBeenCalled());
    await user.type(screen.getByLabelText("Empresa"), "Duplicoder SL");
    await fillLine(user);
    expect(screen.queryByRole("button", { name: "Crear en FACTUSOL" })).toBeNull();
    const submit = screen.getByRole("button", { name: "Crear pedido" });
    await waitFor(() => expect(submit).toBeEnabled());
    await user.click(submit);
    await waitFor(() => expect(mockCreate).toHaveBeenCalled());
    expect(mockCreate.mock.calls[0][0].company_id).toBe("c1");
    await waitFor(() => expect(push).toHaveBeenCalledWith("/erp/orders/new-order-1"));
  });
});
