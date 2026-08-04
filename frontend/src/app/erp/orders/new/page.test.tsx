import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import NewManualOrderPage from "./page";
import { listContacts } from "../../../lib/api";
import { createCompany, listCompanies } from "../../../lib/companiesApi";
import {
  createFactusolCustomerAndLink,
  createOrder,
  linkFactusolCustomer,
  searchFactusolCustomers,
} from "../../../lib/erpApi";

const push = jest.fn();
const refresh = jest.fn();
jest.mock("next/navigation", () => ({
  useRouter: () => ({ push, refresh }),
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
}));
jest.mock("../../../lib/erpApi", () => ({
  createOrder: jest.fn(),
  createFactusolCustomer: jest.fn(),
  createFactusolCustomerAndLink: jest.fn(),
  linkFactusolCustomer: jest.fn(),
  searchFactusolCustomers: jest.fn(),
}));

const mockCompanies = listCompanies as jest.Mock;
const mockContacts = listContacts as jest.Mock;
const mockCreate = createOrder as jest.Mock;
const mockCreateCompany = createCompany as jest.Mock;
const mockCreateAndLink = createFactusolCustomerAndLink as jest.Mock;
const mockLink = linkFactusolCustomer as jest.Mock;
const mockSearchFac = searchFactusolCustomers as jest.Mock;

const COMPANY = {
  id: "c1", name: "Duplicoder SL", tax_id: "B12345678",
  address_line: "C Aribau 171", city: "Barcelona", postal_code: "08036",
  state: "Barcelona", country: "España", factusol_company_id: null,
};

/** Cliente FACTUSOL SIN empresa en el CRM (el caso que arregla C-3-fix2). */
const FAC_SIN_CRM = {
  codcli: "1", nombre: "LABORATORIOS PORTA S.L.", nif: "B64113590",
  nofcli: "LABORATORIOS PORTA S.L.", noccli: "LABORATORIOS PORTA S.L.",
  nifcli: "B64113590", domcli: "c. Fígols, 19-21", pobcli: "Barcelona",
  cpocli: "08028", procli: "Barcelona", paicli: "724",
  emacli: null, telcli: null, crm_link: null, factusol_matches_crm_id: null,
};

beforeEach(() => {
  push.mockReset();
  refresh.mockReset();
  mockCompanies.mockReset();
  mockContacts.mockReset();
  mockCreate.mockReset();
  mockCompanies.mockResolvedValue({ items: [COMPANY], total: 1 });
  mockContacts.mockResolvedValue({ items: [], total: 0 });
  mockCreate.mockResolvedValue({ id: "new-order-1" });
  mockCreateCompany.mockReset();
  mockLink.mockReset();
  mockSearchFac.mockReset();
  mockSearchFac.mockResolvedValue([]);
  mockLink.mockResolvedValue({ linked: true });
  mockCreateAndLink.mockReset();
  mockCreateAndLink.mockResolvedValue({
    company_id: "new-c", factusol_codcli: "1", created: true,
  });
});

describe("NewManualOrderPage", () => {
  it("renderiza el formulario con 1 línea y el origen manual", async () => {
    render(<NewManualOrderPage />);
    expect(screen.getByText("Nuevo pedido manual")).toBeInTheDocument();
    expect(screen.getByText(/Origen:/)).toHaveTextContent("manual");
    expect(screen.getByLabelText("SKU línea 1")).toBeInTheDocument();
    expect(screen.queryByLabelText("SKU línea 2")).not.toBeInTheDocument();
    // Sin cliente ni dirección el submit está deshabilitado.
    expect(screen.getByRole("button", { name: "Crear pedido" })).toBeDisabled();
  });

  it("añade y elimina líneas", async () => {
    const user = userEvent.setup();
    render(<NewManualOrderPage />);
    await user.click(screen.getByRole("button", { name: "+ Añadir línea" }));
    expect(screen.getByLabelText("SKU línea 2")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Eliminar línea 2" }));
    expect(screen.queryByLabelText("SKU línea 2")).not.toBeInTheDocument();
  });

  it("al elegir empresa autocompleta NIF y dirección de envío", async () => {
    const user = userEvent.setup();
    render(<NewManualOrderPage />);
    await waitFor(() => expect(mockCompanies).toHaveBeenCalled());
    await user.type(screen.getByLabelText("Empresa"), "Duplicoder SL");
    await waitFor(() =>
      expect(screen.getByLabelText("NIF / CIF")).toHaveValue("B12345678"));
    expect(screen.getByLabelText("Dirección de envío")).toHaveValue("C Aribau 171");
    expect(screen.getByLabelText("Ciudad de envío")).toHaveValue("Barcelona");
  });

  it("envía el pedido y redirige a la ficha nueva", async () => {
    const user = userEvent.setup();
    render(<NewManualOrderPage />);
    await waitFor(() => expect(mockCompanies).toHaveBeenCalled());
    await user.type(screen.getByLabelText("Empresa"), "Duplicoder SL");
    await user.type(screen.getByLabelText("SKU línea 1"), "SKU-1");
    await user.type(screen.getByLabelText("Precio línea 1"), "100");

    const submit = screen.getByRole("button", { name: "Crear pedido" });
    await waitFor(() => expect(submit).toBeEnabled());
    await user.click(submit);

    await waitFor(() => expect(mockCreate).toHaveBeenCalled());
    const payload = mockCreate.mock.calls[0][0];
    expect(payload.company_id).toBe("c1");
    expect(payload.lines).toHaveLength(1);
    expect(payload.lines[0]).toMatchObject({ product_sku: "SKU-1", unit_price: 100 });
    expect(payload.shipping_address.city).toBe("Barcelona");
    await waitFor(() => expect(push).toHaveBeenCalledWith("/erp/orders/new-order-1"));
  });

  it("con «Recogida en tienda» no exige dirección de envío", async () => {
    const user = userEvent.setup();
    render(<NewManualOrderPage />);
    await waitFor(() => expect(mockContacts).toHaveBeenCalled());
    await user.type(screen.getByLabelText("Empresa"), "Duplicoder SL");
    await user.click(screen.getByLabelText("Recogida en tienda"));
    expect(screen.queryByLabelText("Dirección de envío")).not.toBeInTheDocument();
    await user.type(screen.getByLabelText("SKU línea 1"), "SKU-1");
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Crear pedido" })).toBeEnabled());
  });

  // --- C-3-fix2: vinculación real desde el propio formulario ---------------

  /** Elige el primer resultado FACTUSOL del autocomplete. */
  async function pickFactusol(user: ReturnType<typeof userEvent.setup>) {
    await user.type(screen.getByLabelText("Buscar cliente"), "labora");
    await user.click(await screen.findByText("LABORATORIOS PORTA S.L."));
  }

  it("elegir un cliente FACTUSOL sin CRM ofrece las 2 acciones de vinculación", async () => {
    mockSearchFac.mockResolvedValue([FAC_SIN_CRM]);
    const user = userEvent.setup();
    render(<NewManualOrderPage />);
    await pickFactusol(user);

    expect(await screen.findByRole("button", {
      name: /Crear empresa CRM con estos datos y vincular/,
    })).toBeInTheDocument();
    expect(screen.getByRole("button", {
      name: /Vincular a empresa CRM existente/,
    })).toBeInTheDocument();
  });

  it("«Crear empresa CRM…» usa el endpoint ATÓMICO (una sola llamada)", async () => {
    mockSearchFac.mockResolvedValue([FAC_SIN_CRM]);
    const user = userEvent.setup();
    render(<NewManualOrderPage />);
    await pickFactusol(user);
    await user.click(await screen.findByRole("button", {
      name: /Crear empresa CRM con estos datos y vincular/,
    }));

    await waitFor(() => expect(mockCreateAndLink).toHaveBeenCalledTimes(1));
    const payload = mockCreateAndLink.mock.calls[0][0];
    expect(payload.factusol_codcli).toBe("1");
    expect(payload.factusol_customer_data.nombre).toBe("LABORATORIOS PORTA S.L.");
    expect(payload.factusol_customer_data.nif).toBe("B64113590");
    expect(payload.factusol_customer_data.direccion).toBe("c. Fígols, 19-21");
    // Ya NO se usan las 2 llamadas separadas (dejaban empresas huérfanas).
    expect(mockCreateCompany).not.toHaveBeenCalled();
    expect(mockLink).not.toHaveBeenCalled();

    expect(await screen.findByText(/creada y vinculada a FACTUSOL nº 1/))
      .toBeInTheDocument();
  });

  it("muestra el detail del backend cuando el 409 dice que ya está vinculado", async () => {
    mockSearchFac.mockResolvedValue([FAC_SIN_CRM]);
    mockCreateAndLink.mockRejectedValue(new Error(
      'El cliente FACTUSOL 1 ya está vinculado a company «PORTA CRM» (id: c9).',
    ));
    const user = userEvent.setup();
    render(<NewManualOrderPage />);
    await pickFactusol(user);
    await user.click(await screen.findByRole("button", {
      name: /Crear empresa CRM con estos datos y vincular/,
    }));
    expect(await screen.findByText(/ya está vinculado a company «PORTA CRM»/))
      .toBeInTheDocument();
  });

  it("deshabilita el botón mientras la petición está en curso (anti doble-click)", async () => {
    mockSearchFac.mockResolvedValue([FAC_SIN_CRM]);
    let resolve: (v: unknown) => void = () => {};
    mockCreateAndLink.mockReturnValue(new Promise((r) => { resolve = r; }));
    const user = userEvent.setup();
    render(<NewManualOrderPage />);
    await pickFactusol(user);
    const btn = await screen.findByRole("button", {
      name: /Crear empresa CRM con estos datos y vincular/,
    });
    await user.click(btn);

    // Con la promesa pendiente el botón queda bloqueado: un 2º click no dispara
    // otra creación (el bug de prod fueron 3 clicks → 2 empresas huérfanas).
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /Creando…/ })).toBeDisabled());
    await user.click(screen.getByRole("button", { name: /Creando…/ }));
    expect(mockCreateAndLink).toHaveBeenCalledTimes(1);

    resolve({ company_id: "new-c", factusol_codcli: "1", created: true });
    await waitFor(() => expect(mockCreateAndLink).toHaveBeenCalledTimes(1));
  });
  it("«Vincular a empresa CRM existente…» abre el buscador de empresas", async () => {
    mockSearchFac.mockResolvedValue([FAC_SIN_CRM]);
    const user = userEvent.setup();
    render(<NewManualOrderPage />);
    await pickFactusol(user);
    await user.click(await screen.findByRole("button", {
      name: /Vincular a empresa CRM existente/,
    }));
    expect(await screen.findByLabelText("Empresa CRM a vincular")).toBeInTheDocument();
  });

  it("elegir una empresa existente y pulsar «Vincular» llama al endpoint", async () => {
    mockSearchFac.mockResolvedValue([FAC_SIN_CRM]);
    const user = userEvent.setup();
    render(<NewManualOrderPage />);
    await pickFactusol(user);
    await user.click(await screen.findByRole("button", {
      name: /Vincular a empresa CRM existente/,
    }));
    await user.type(
      await screen.findByLabelText("Empresa CRM a vincular"), "Duplicoder SL",
    );
    const vincular = await screen.findByRole("button", { name: "Vincular" });
    await waitFor(() => expect(vincular).toBeEnabled());
    await user.click(vincular);

    await waitFor(() => expect(mockLink).toHaveBeenCalledWith({
      crm_type: "company", crm_id: "c1", factusol_codcli: "1",
    }));
    expect(await screen.findByText(/vinculada a FACTUSOL nº 1/)).toBeInTheDocument();
  });

  it("un cliente FACTUSOL YA vinculado no muestra las acciones", async () => {
    mockSearchFac.mockResolvedValue([{
      ...FAC_SIN_CRM,
      crm_link: { type: "company", id: "c9", name: "Porta CRM" },
      factusol_matches_crm_id: "c9",
    }]);
    const user = userEvent.setup();
    render(<NewManualOrderPage />);
    await pickFactusol(user);
    expect(await screen.findByText(/ya vinculado a «Porta CRM»/)).toBeInTheDocument();
    expect(screen.queryByRole("button", {
      name: /Crear empresa CRM con estos datos/,
    })).not.toBeInTheDocument();
  });
});
