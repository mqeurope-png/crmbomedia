import { render, screen, waitFor, within } from "@testing-library/react";
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

/** Lote 2 · PR-2 — revisión de diseño §10 «Nuevo pedido manual»: tres pasos
 *  numerados, el requisito FACTUSOL explicado junto a la empresa con «Vincular
 *  ahora», el total (IVA incluido, como lo calcula el backend) siempre
 *  visible, el botón desactivado con su motivo escrito y el atajo de la
 *  dirección de envío «La de la empresa». */

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
  // Lote 7 · P1: el alta manual usa FACTUSOL_SERIES para el selector de serie.
  FACTUSOL_SERIES: [
    { value: 1, label: "Bomedia" }, { value: 2, label: "MQ Europe" },
    { value: 4, label: "Lambert" }, { value: 5, label: "Streamtec" },
  ],
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
const mockCreate = createOrder as jest.Mock;
const mockCreateCustomer = createFactusolCustomer as jest.Mock;
const mockSearchFac = searchFactusolCustomers as jest.Mock;

const LINKED = {
  id: "c1", name: "Duplicoder SL", tax_id: "B12345678",
  address_line: "C Aribau 171", city: "Barcelona", postal_code: "08036",
  state: "Barcelona", country: "España", factusol_company_id: "55555",
};
const UNLINKED = {
  id: "c-sin", name: "Rotulación del Sur SLU", tax_id: "B11111111",
  address_line: "C Nueva 5", city: "Lleida", postal_code: "25001",
  state: "Lleida", country: "España", factusol_company_id: null,
};

/** Ficha F_CLI de la empresa vinculada, con su régimen de IVA (Tarea C). */
function fcli(over = {}) {
  return {
    codcli: "55555", nombre: "Duplicoder", nif: "B12345678",
    nofcli: "DUPLICODER, S.L.", noccli: "Duplicoder", nifcli: "B12345678",
    domcli: "c. Fígols, 19-21", pobcli: "Barcelona", cpocli: "08028",
    procli: "Barcelona", paicli: "724", pais_iso2: "ES", emacli: null, telcli: null,
    regime: "nacional", regime_label: "Nacional",
    crm_link: { type: "company" as const, id: "c1", name: "Duplicoder SL" },
    factusol_matches_crm_id: "c1",
    ...over,
  };
}

async function waitForCompanyOption(name: string) {
  await waitFor(() =>
    expect(document.querySelector(`#erp-new-order-companies option[value="${name}"]`))
      .not.toBeNull(),
  );
}

function totalPanel() {
  return screen.getByRole("region", { name: "Total del pedido" });
}

function submitButton() {
  return screen.getByRole("button", { name: "Crear pedido" });
}

beforeEach(() => {
  push.mockReset();
  mockCompanies.mockReset();
  mockCompanies.mockResolvedValue({ items: [LINKED, UNLINKED], total: 2 });
  (listContacts as jest.Mock).mockReset();
  (listContacts as jest.Mock).mockResolvedValue({ items: [], total: 0 });
  mockCreate.mockReset();
  mockCreate.mockResolvedValue({ id: "new-order-1" });
  mockCreateCustomer.mockReset();
  mockSearchFac.mockReset();
  mockSearchFac.mockResolvedValue([fcli()]);
  (listFactusolQuotes as jest.Mock).mockReset();
  (listFactusolQuotes as jest.Mock).mockResolvedValue({ items: [], unlinked: false });
  (searchFactusolArticles as jest.Mock).mockReset();
  (searchFactusolArticles as jest.Mock).mockResolvedValue([]);
});

describe("Nuevo pedido manual · Lote 2 PR-2 (diseño)", () => {
  it("tres pasos numerados; importar de FACTUSOL plegado en el 1 y facturación/notas en el 3, sin quitar nada", () => {
    render(<NewManualOrderPage />);
    const step1 = screen.getByRole("region", { name: "1 · Empresa" });
    const step2 = screen.getByRole("region", { name: "2 · Líneas" });
    const step3 = screen.getByRole("region", { name: "3 · Envío" });
    expect(step1).toBeInTheDocument();
    expect(step2).toBeInTheDocument();
    expect(step3).toBeInTheDocument();
    // Paso 1: cliente + importador (desplegable, cerrado por defecto).
    expect(within(step1).getByLabelText("Buscar cliente")).toBeInTheDocument();
    const helper = within(step1).getByText("Importar de FACTUSOL").closest("details") as HTMLDetailsElement;
    expect(helper).not.toBeNull();
    expect(helper.open).toBe(false);
    expect(within(helper).getByLabelText("Tipo de documento FACTUSOL")).toBeInTheDocument();
    // Paso 2: la tabla compartida de líneas.
    expect(within(step2).getByRole("table", { name: "Líneas del pedido" })).toBeInTheDocument();
    expect(within(step2).getByLabelText("SKU línea 1")).toBeInTheDocument();
    // Paso 3: portes, atajo de dirección, recogida, nombre de envío, y «Más».
    expect(within(step3).getByLabelText("Portes")).toBeInTheDocument();
    expect(within(step3).getByLabelText("Enviar a")).toHaveValue("company");
    expect(within(step3).getByLabelText("Recogida en tienda")).toBeInTheDocument();
    expect(within(step3).getByLabelText("Nombre de envío")).toBeInTheDocument();
    const more = within(step3).getByText("Más: facturación y notas").closest("details") as HTMLDetailsElement;
    expect(more.open).toBe(false);
    expect(within(more).getByLabelText("Usar dirección de envío")).toBeChecked();
    expect(within(more).getByLabelText("Notas internas")).toBeInTheDocument();
    // Un solo «Crear pedido» en la página (el de la barra de acción).
    expect(screen.getAllByRole("button", { name: "Crear pedido" })).toHaveLength(1);
    expect(screen.getByRole("region", { name: "Crear pedido" })).toBeInTheDocument();
  });

  it("empresa solo-CRM: aviso ámbar junto a la empresa con «Vincular ahora»; el motivo bajo el botón lo repite", async () => {
    mockCreateCustomer.mockResolvedValue({
      factusol_codcli: "9001", created: true, crm_type: "company", crm_id: "c-sin",
    });
    mockSearchFac.mockResolvedValue([fcli({
      codcli: "9001", nombre: "Rotulación del Sur", nofcli: "ROTULACIÓN DEL SUR SLU",
      nif: "B11111111", nifcli: "B11111111", domcli: "C Nueva 5", pobcli: "Lleida",
      cpocli: "25001", procli: "Lleida",
      crm_link: { type: "company" as const, id: "c-sin", name: "Rotulación del Sur SLU" },
      factusol_matches_crm_id: "c-sin",
    })]);
    const user = userEvent.setup();
    render(<NewManualOrderPage />);
    await waitForCompanyOption("Rotulación del Sur SLU");
    await user.type(screen.getByLabelText("Empresa"), "Rotulación del Sur SLU");
    await user.type(screen.getByLabelText("Descripción línea 1"), "Vinilo");

    const step1 = screen.getByRole("region", { name: "1 · Empresa" });
    expect(within(step1).getByText("Solo CRM")).toBeInTheDocument();
    const notice = within(step1).getByRole("status");
    expect(notice).toHaveTextContent("Esta empresa aún no está en FACTUSOL. Sin ella no se puede emitir factura.");
    expect(notice).toHaveClass("is-amber");
    const vincular = within(notice).getByRole("button", { name: "Vincular ahora" });
    // El botón está desactivado CON su motivo, enlazado por aria-describedby.
    expect(submitButton()).toBeDisabled();
    expect(submitButton()).toHaveAttribute("aria-describedby", "erp-new-order-reason");
    expect(document.getElementById("erp-new-order-reason"))
      .toHaveTextContent("Falta vincular la empresa a FACTUSOL.");
    // Sin duplicar el aviso viejo («créala primero») ni el enlace a Empresas.
    expect(screen.queryByRole("note")).not.toBeInTheDocument();

    await user.click(vincular);
    await waitFor(() => expect(mockCreateCustomer).toHaveBeenCalledWith(expect.objectContaining({
      crm_type: "company", crm_id: "c-sin", nombre: "Rotulación del Sur SLU",
    })));
    // Vinculada: desaparece el aviso, la pastilla dice el nº y se puede crear.
    await waitFor(() => expect(within(step1).queryByText("Solo CRM")).not.toBeInTheDocument());
    expect(step1.querySelector(".erp-new-order-pill.is-linked")).toHaveTextContent("FACTUSOL nº 9001");
    expect(screen.queryByRole("button", { name: "Vincular ahora" })).not.toBeInTheDocument();
    await waitFor(() => expect(submitButton()).toBeEnabled());
    expect(document.getElementById("erp-new-order-reason")).toBeNull();
  });

  it("el total lleva el IVA del régimen del cliente (nacional → 21 %) como lo calcula el backend, y cada línea viaja con su tax_rate", async () => {
    const user = userEvent.setup();
    render(<NewManualOrderPage />);
    await waitForCompanyOption("Duplicoder SL");
    // Antes de leer la ficha: IVA general, y lo dice.
    expect(totalPanel()).toHaveTextContent(/IVA 21 %/);
    expect(totalPanel()).toHaveTextContent(/Se ajusta al régimen de la ficha FACTUSOL/);

    await user.type(screen.getByLabelText("Empresa"), "Duplicoder SL");
    await waitFor(() => expect(mockSearchFac).toHaveBeenCalledWith("55555", "codcli"));
    await user.type(screen.getByLabelText("Descripción línea 1"), "Tinta cyan");
    await user.clear(screen.getByLabelText("Cantidad línea 1"));
    await user.type(screen.getByLabelText("Cantidad línea 1"), "3");
    await user.type(screen.getByLabelText("Precio línea 1"), "33.33");
    await user.type(screen.getByLabelText("Portes"), "19");

    // Backend: line_total = round(3 × 33,33, 2) = 99,99; portes 19 → base
    // 118,99; total = round(118,99 × 1,21, 2) = 143,98; IVA = 24,99.
    await waitFor(() => expect(totalPanel()).toHaveTextContent(/Régimen Nacional/));
    expect(totalPanel()).toHaveTextContent(/Artículos\s*99\.99 €/);
    expect(totalPanel()).toHaveTextContent(/Portes\s*19\.00 €/);
    expect(totalPanel()).toHaveTextContent(/IVA 21 %\s*24\.99 €/);
    expect(totalPanel()).toHaveTextContent(/Total\s*143\.98 €/);
    // La barra móvil enseña el mismo total.
    expect(screen.getByRole("region", { name: "Crear pedido" })).toHaveTextContent("143.98 €");

    await waitFor(() => expect(submitButton()).toBeEnabled());
    await user.click(submitButton());
    await waitFor(() => expect(mockCreate).toHaveBeenCalled());
    const { lines } = mockCreate.mock.calls[0][0];
    expect(lines).toEqual([
      expect.objectContaining({ description: "Tinta cyan", quantity: 3, unit_price: 33.33, tax_rate: 21 }),
      expect.objectContaining({ description: "Portes", is_shipping: true, unit_price: 19, tax_rate: 21 }),
    ]);
  });

  it("régimen intracomunitario: «Exento», total sin IVA y tax_rate 0 en todas las líneas", async () => {
    mockSearchFac.mockResolvedValue([fcli({
      regime: "intracomunitario", regime_label: "Intracomunitario (UE)",
      pais_iso2: "BE", pobcli: "Bruxelles",
    })]);
    const user = userEvent.setup();
    render(<NewManualOrderPage />);
    await waitForCompanyOption("Duplicoder SL");
    await user.type(screen.getByLabelText("Empresa"), "Duplicoder SL");
    await waitFor(() => expect(totalPanel()).toHaveTextContent(/Exento/));
    await user.type(screen.getByLabelText("Descripción línea 1"), "Cabezal");
    await user.type(screen.getByLabelText("Precio línea 1"), "250");
    await user.type(screen.getByLabelText("Portes"), "30");

    expect(totalPanel()).toHaveTextContent(/Exento\s*0\.00 €/);
    expect(totalPanel()).toHaveTextContent(/Total\s*280\.00 €/);
    expect(totalPanel()).toHaveTextContent(/Intracomunitario \(UE\)/);
    expect(totalPanel()).not.toHaveTextContent(/IVA 21 %/);

    await waitFor(() => expect(submitButton()).toBeEnabled());
    await user.click(submitButton());
    await waitFor(() => expect(mockCreate).toHaveBeenCalled());
    const { lines } = mockCreate.mock.calls[0][0];
    expect(lines.map((l: { tax_rate: number }) => l.tax_rate)).toEqual([0, 0]);
  });

  it("el botón desactivado siempre lleva su motivo escrito, en el orden de los pasos", async () => {
    mockSearchFac.mockResolvedValue([]);
    const user = userEvent.setup();
    render(<NewManualOrderPage />);
    const reason = () => document.getElementById("erp-new-order-reason");
    expect(submitButton()).toBeDisabled();
    expect(reason()).toHaveTextContent("Falta elegir la empresa.");

    await waitForCompanyOption("Duplicoder SL");
    await user.type(screen.getByLabelText("Empresa"), "Duplicoder SL");
    expect(reason()).toHaveTextContent("Añade al menos una línea.");

    // SKU sin descripción: la línea no vale, y se dice cuál.
    await user.type(screen.getByLabelText("SKU línea 1"), "SKU-1");
    expect(reason()).toHaveTextContent("Línea 1: Indica la descripción.");
    await user.type(screen.getByLabelText("Descripción línea 1"), "Reparación");
    // La dirección viene de la empresa: ya se puede crear (sin motivo).
    await waitFor(() => expect(submitButton()).toBeEnabled());
    expect(reason()).toBeNull();

    await user.clear(screen.getByLabelText("Dirección de envío"));
    await user.clear(screen.getByLabelText("Ciudad de envío"));
    await user.clear(screen.getByLabelText("Código postal de envío"));
    expect(submitButton()).toBeDisabled();
    expect(reason()).toHaveTextContent("Falta la dirección de envío.");
    // Recogida en tienda quita ese requisito.
    await user.click(screen.getByLabelText("Recogida en tienda"));
    await waitFor(() => expect(submitButton()).toBeEnabled());
  });

  it("dirección de envío: «La de la empresa» la rellena de la empresa; editar pasa a «Otra dirección» y el atajo la restaura", async () => {
    const user = userEvent.setup();
    render(<NewManualOrderPage />);
    const enviarA = () => screen.getByLabelText("Enviar a");
    expect(enviarA()).toHaveValue("company");
    expect(screen.getByText(/Elige la empresa y se usará su dirección/)).toBeInTheDocument();

    await waitForCompanyOption("Duplicoder SL");
    await user.type(screen.getByLabelText("Empresa"), "Duplicoder SL");
    // FACTUSOL manda: la dirección de la empresa es la de su ficha F_CLI.
    await waitFor(() => expect(screen.getByLabelText("Dirección de envío")).toHaveValue("c. Fígols, 19-21"));
    expect(screen.getByLabelText("Código postal de envío")).toHaveValue("08028");
    expect(enviarA()).toHaveValue("company");
    expect(screen.queryByText(/Elige la empresa y se usará su dirección/)).not.toBeInTheDocument();

    // Escribir otra dirección: el selector lo refleja solo.
    await user.clear(screen.getByLabelText("Dirección de envío"));
    await user.type(screen.getByLabelText("Dirección de envío"), "Pol. Ind. 4, nave 2");
    expect(enviarA()).toHaveValue("other");

    // Y el atajo vuelve a la de la empresa con un clic.
    await user.selectOptions(enviarA(), "company");
    expect(screen.getByLabelText("Dirección de envío")).toHaveValue("c. Fígols, 19-21");
    expect(screen.getByLabelText("Ciudad de envío")).toHaveValue("Barcelona");

    // «Otra dirección» deja los campos para editarlos y viaja lo escrito.
    await user.selectOptions(enviarA(), "other");
    await user.clear(screen.getByLabelText("Dirección de envío"));
    await user.type(screen.getByLabelText("Dirección de envío"), "Rue de la Paix 12");
    await user.type(screen.getByLabelText("Descripción línea 1"), "Cabezal");
    await waitFor(() => expect(submitButton()).toBeEnabled());
    await user.click(submitButton());
    await waitFor(() => expect(mockCreate).toHaveBeenCalled());
    expect(mockCreate.mock.calls[0][0].shipping_address).toMatchObject({
      address_line: "Rue de la Paix 12", city: "Barcelona", postal_code: "08028",
    });
  });

  it("sin ficha F_CLI la dirección de la empresa es la del CRM", async () => {
    mockSearchFac.mockResolvedValue([]);
    const user = userEvent.setup();
    render(<NewManualOrderPage />);
    await waitForCompanyOption("Duplicoder SL");
    await user.type(screen.getByLabelText("Empresa"), "Duplicoder SL");
    await waitFor(() => expect(screen.getByLabelText("Dirección de envío")).toHaveValue("C Aribau 171"));
    await user.clear(screen.getByLabelText("Dirección de envío"));
    expect(screen.getByLabelText("Enviar a")).toHaveValue("other");
    await user.selectOptions(screen.getByLabelText("Enviar a"), "company");
    expect(screen.getByLabelText("Dirección de envío")).toHaveValue("C Aribau 171");
  });
});
