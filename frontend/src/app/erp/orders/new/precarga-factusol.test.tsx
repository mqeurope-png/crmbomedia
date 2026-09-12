import { act, render, screen, waitFor } from "@testing-library/react";
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

const mockCompanies = listCompanies as jest.Mock;
const mockSearchFac = searchFactusolCustomers as jest.Mock;
const mockCreate = createOrder as jest.Mock;

/** El caso de producción: empresa CRM de prueba («Eduard Test», NIF viejo)
 *  vinculada al cliente F_CLI 3187, que en FACTUSOL es Laboratorios Porta. */
const EDUARD = {
  id: "c1", name: "Eduard Test", tax_id: "B00000000",
  address_line: "Calle Vieja 9", city: "Lleida", postal_code: "25001",
  state: "Lleida", country: "España", factusol_company_id: "3187",
};
const OTRA = {
  id: "c2", name: "Otra SL", tax_id: "B22222222",
  address_line: "C Otra 2", city: "Girona", postal_code: "17001",
  state: "Girona", country: "España", factusol_company_id: "4020",
};

const PORTA_3187 = {
  codcli: "3187", nombre: "Laboratorios Porta", nif: "B64113590",
  nofcli: "LABORATORIOS PORTA S.L.", noccli: "Laboratorios Porta", nifcli: "B64113590",
  domcli: "c. Fígols, 19-21", pobcli: "Barcelona", cpocli: "08028", procli: "Barcelona",
  paicli: "724", pais_iso2: "ES", emacli: null, telcli: null,
  crm_link: { type: "company" as const, id: "c1", name: "Eduard Test" },
  factusol_matches_crm_id: "c1",
};
const OTRA_4020 = {
  ...PORTA_3187, codcli: "4020", nombre: "Otra", nif: "B99999999", nofcli: "OTRA S.L.",
  noccli: "Otra", nifcli: "B99999999", domcli: "Av. Nova 4", pobcli: "Girona",
  cpocli: "17001", procli: "Girona",
  crm_link: { type: "company" as const, id: "c2", name: "Otra SL" },
  factusol_matches_crm_id: "c2",
};

async function waitForCompanyOption(name: string) {
  await waitFor(() =>
    expect(document.querySelector(`#erp-new-order-companies option[value="${name}"]`))
      .not.toBeNull(),
  );
}

beforeEach(() => {
  push.mockReset();
  mockCompanies.mockReset();
  mockCompanies.mockResolvedValue({ items: [EDUARD, OTRA], total: 2 });
  (listContacts as jest.Mock).mockReset();
  (listContacts as jest.Mock).mockResolvedValue({ items: [], total: 0 });
  mockCreate.mockReset();
  mockCreate.mockResolvedValue({ id: "new-order-1" });
  mockSearchFac.mockReset();
  mockSearchFac.mockResolvedValue([PORTA_3187]);
  (listFactusolQuotes as jest.Mock).mockReset();
  (listFactusolQuotes as jest.Mock).mockResolvedValue({ items: [], unlinked: false });
  (searchFactusolArticles as jest.Mock).mockReset();
  (searchFactusolArticles as jest.Mock).mockResolvedValue([]);
});

describe("Alta de pedido manual — la precarga desde FACTUSOL rellena los campos VISIBLES", () => {
  it("test_alta_pedido_precarga_rellena_campos_visibles: empresa vinculada → NIF, dirección y nombre fiscal de F_CLI pisan lo previo (CRM viejo o tecleado) y el aviso dice qué se cargó", async () => {
    const user = userEvent.setup();
    render(<NewManualOrderPage />);
    await waitForCompanyOption("Eduard Test");
    // Valores previos de prueba, como en la captura: NIF basura y dirección tecleada.
    await user.type(screen.getByLabelText("NIF / CIF"), "fsadfas");
    await user.type(screen.getByLabelText("Dirección de envío"), "Basura 1");
    await user.type(screen.getByPlaceholderText("Buscar empresa…"), "Eduard Test");
    await waitFor(() => expect(mockSearchFac).toHaveBeenCalledWith("3187", "codcli"));

    // Los inputs muestran los datos de FACTUSOL, no los previos.
    await waitFor(() => expect(screen.getByLabelText("NIF / CIF")).toHaveValue("B64113590"));
    expect(screen.getByLabelText("Dirección de envío")).toHaveValue("c. Fígols, 19-21");
    expect(screen.getByLabelText("Ciudad de envío")).toHaveValue("Barcelona");
    expect(screen.getByLabelText("Código postal de envío")).toHaveValue("08028");
    expect(screen.getByLabelText("Provincia de envío")).toHaveValue("Barcelona");
    expect(screen.getByLabelText("País de envío")).toHaveValue("España");
    expect(screen.queryByDisplayValue("fsadfas")).not.toBeInTheDocument();
    expect(screen.queryByDisplayValue("Basura 1")).not.toBeInTheDocument();
    expect(screen.queryByDisplayValue("Calle Vieja 9")).not.toBeInTheDocument();
    // El nombre fiscal de FACTUSOL queda a la vista, en su campo (solo lectura);
    // «Empresa» sigue siendo la del CRM, a la que queda el pedido.
    expect(screen.getByLabelText("Nombre fiscal FACTUSOL")).toHaveValue("LABORATORIOS PORTA S.L.");
    expect(screen.getByLabelText("Nombre fiscal FACTUSOL")).toHaveAttribute("readonly");
    expect(screen.getByLabelText("NIF FACTUSOL")).toHaveValue("B64113590");
    expect(screen.getByText("Nombre fiscal (FACTUSOL nº 3187)")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("Buscar empresa…")).toHaveValue("Eduard Test");
    // El aviso enumera exactamente lo volcado y señala el nombre fiscal distinto.
    expect(screen.getByRole("status")).toHaveTextContent(
      "Datos del cliente FACTUSOL nº 3187 cargados en el pedido: NIF B64113590 · "
      + "dirección c. Fígols, 19-21, 08028 Barcelona, España. "
      + "Nombre fiscal en FACTUSOL: «LABORATORIOS PORTA S.L.» (empresa del CRM: «Eduard Test»).",
    );

    // Y el pedido se crea con esos datos.
    await user.type(screen.getByLabelText("Descripción línea 1"), "Reparación láser");
    await user.type(screen.getByLabelText("Precio línea 1"), "90");
    const submit = screen.getByRole("button", { name: "Crear pedido" });
    await waitFor(() => expect(submit).toBeEnabled());
    await user.click(submit);
    await waitFor(() => expect(mockCreate).toHaveBeenCalled());
    const body = mockCreate.mock.calls[0][0];
    expect(body.company_id).toBe("c1");
    expect(body.tax_id).toBe("B64113590");
    expect(body.shipping_address.address_line).toBe("c. Fígols, 19-21");
    expect(body.shipping_address.postal_code).toBe("08028");
  });

  it("el aviso no miente: sin NIF ni dirección en F_CLI no dice «cargados» y el formulario se queda como estaba", async () => {
    mockSearchFac.mockResolvedValue([{
      ...PORTA_3187, nif: "", nifcli: "", domcli: "", pobcli: "", cpocli: "", procli: "",
    }]);
    const user = userEvent.setup();
    render(<NewManualOrderPage />);
    await waitForCompanyOption("Eduard Test");
    await user.type(screen.getByLabelText("NIF / CIF"), "fsadfas");
    await user.type(screen.getByPlaceholderText("Buscar empresa…"), "Eduard Test");
    await waitFor(() => expect(mockSearchFac).toHaveBeenCalledWith("3187", "codcli"));
    expect(await screen.findByRole("status")).toHaveTextContent(
      "El cliente FACTUSOL nº 3187 no tiene NIF ni dirección: no se ha cargado nada en el pedido",
    );
    expect(screen.getByRole("status")).not.toHaveTextContent(/cargados en el pedido/);
    expect(screen.getByLabelText("NIF / CIF")).toHaveValue("fsadfas");
    // La dirección del CRM (autocompletada al elegir la empresa) no se borra con vacíos.
    expect(screen.getByLabelText("Dirección de envío")).toHaveValue("Calle Vieja 9");
    // El nombre fiscal sí se enseña (existe); el NIF de FACTUSOL, vacío.
    expect(screen.getByLabelText("Nombre fiscal FACTUSOL")).toHaveValue("LABORATORIOS PORTA S.L.");
    expect(screen.getByLabelText("NIF FACTUSOL")).toHaveValue("");
  });

  it("solo NIF en F_CLI (sin dirección): se carga el NIF y el aviso lo dice; la dirección tecleada se respeta", async () => {
    mockSearchFac.mockResolvedValue([{
      ...PORTA_3187, domcli: "", pobcli: "", cpocli: "", procli: "",
    }]);
    const user = userEvent.setup();
    render(<NewManualOrderPage />);
    await waitForCompanyOption("Eduard Test");
    await user.type(screen.getByLabelText("Dirección de envío"), "Tecleada 7");
    await user.type(screen.getByPlaceholderText("Buscar empresa…"), "Eduard Test");
    await waitFor(() => expect(screen.getByLabelText("NIF / CIF")).toHaveValue("B64113590"));
    expect(screen.getByLabelText("Dirección de envío")).toHaveValue("Tecleada 7");
    expect(screen.getByRole("status")).toHaveTextContent(
      "Datos del cliente FACTUSOL nº 3187 cargados en el pedido: NIF B64113590.",
    );
    expect(screen.getByRole("status")).not.toHaveTextContent(/dirección/);
  });

  it("cliente no encontrado o lectura fallida → aviso claro, nada cargado, sin campo de nombre fiscal", async () => {
    mockSearchFac.mockResolvedValue([]);
    const user = userEvent.setup();
    render(<NewManualOrderPage />);
    await waitForCompanyOption("Eduard Test");
    await user.type(screen.getByLabelText("NIF / CIF"), "fsadfas");
    await user.type(screen.getByPlaceholderText("Buscar empresa…"), "Eduard Test");
    expect(await screen.findByRole("status")).toHaveTextContent(
      "No se encontró el cliente FACTUSOL nº 3187: no se ha cargado nada en el pedido.",
    );
    expect(screen.getByLabelText("NIF / CIF")).toHaveValue("fsadfas");
    expect(screen.queryByLabelText("Nombre fiscal FACTUSOL")).not.toBeInTheDocument();

    mockSearchFac.mockRejectedValue(new Error("FACTUSOL caído"));
    await user.clear(screen.getByPlaceholderText("Buscar empresa…"));
    await user.type(screen.getByPlaceholderText("Buscar empresa…"), "Otra SL");
    expect(await screen.findByRole("status")).toHaveTextContent(
      "FACTUSOL caído: no se ha cargado nada en el pedido (se mantienen los datos del CRM).",
    );
    expect(screen.getByLabelText("NIF / CIF")).toHaveValue("fsadfas");
  });

  it("una respuesta tardía de la empresa anterior no pisa los datos de la elegida después", async () => {
    let resolveEduard: (hits: unknown[]) => void = () => undefined;
    mockSearchFac.mockImplementation((q: string) =>
      q === "3187"
        ? new Promise<unknown[]>((resolve) => { resolveEduard = resolve; })
        : Promise.resolve([OTRA_4020]));
    const user = userEvent.setup();
    render(<NewManualOrderPage />);
    await waitForCompanyOption("Eduard Test");
    const empresa = screen.getByPlaceholderText("Buscar empresa…");
    await user.type(empresa, "Eduard Test");
    await waitFor(() => expect(mockSearchFac).toHaveBeenCalledWith("3187", "codcli"));
    await user.clear(empresa);
    await user.type(empresa, "Otra SL");
    await waitFor(() => expect(screen.getByLabelText("NIF / CIF")).toHaveValue("B99999999"));
    // Ahora llega (tarde) la respuesta de Eduard Test: se ignora.
    await act(async () => { resolveEduard([PORTA_3187]); });
    expect(screen.getByLabelText("NIF / CIF")).toHaveValue("B99999999");
    expect(screen.getByLabelText("Nombre fiscal FACTUSOL")).toHaveValue("OTRA S.L.");
    expect(screen.getByLabelText("Dirección de envío")).toHaveValue("Av. Nova 4");
    expect(screen.getByRole("status")).toHaveTextContent(/FACTUSOL nº 4020 cargados/);
  });
});
