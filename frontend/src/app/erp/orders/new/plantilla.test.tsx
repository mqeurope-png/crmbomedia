import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import NewManualOrderPage from "./page";
import { listContacts } from "../../../lib/api";
import { listCompanies } from "../../../lib/companiesApi";
import {
  createOrder,
  getPlantillaLineas,
  getPlantillasMeta,
  listFactusolQuotes,
  listPlantillaCarpetas,
  listPlantillaEtiquetas,
  listPlantillas,
  searchFactusolArticles,
  searchFactusolCustomers,
  type PlantillaDocumento,
  type PlantillaLineaPedido,
} from "../../../lib/erpApi";

/** Plantillas de documento en el alta de pedido: «Cargar plantilla…» abre el
 *  selector (destino «pedido») y vuelca las líneas en la forma del pedido
 *  (SKU, precio NETO, descripción); con `?plantilla=` («Usar en un pedido»
 *  desde ERP · Plantillas) se cargan al entrar. Al crear, el CODART de la
 *  plantilla viaja como `product_codart`. La plantilla solo aporta las líneas. */

const push = jest.fn();
const refresh = jest.fn();
let mockSearch = "";
jest.mock("next/navigation", () => ({
  useRouter: () => ({ push, refresh }),
  useSearchParams: () => new URLSearchParams(mockSearch),
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
  // Plantillas de documento: el PlantillaSelector real y las líneas.
  getPlantillaLineas: jest.fn(),
  getPlantillasMeta: jest.fn(),
  listPlantillaCarpetas: jest.fn(),
  listPlantillaEtiquetas: jest.fn(),
  listPlantillas: jest.fn(),
}));

const mockCreate = createOrder as jest.Mock;
const mockLineas = getPlantillaLineas as jest.Mock;
const mockList = listPlantillas as jest.Mock;

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

const KIT: PlantillaDocumento = {
  id: "pl-1", nombre: "Kit CD impresos", descripcion: null, activa: true, idioma: "es",
  carpeta_id: null, carpeta: null, etiquetas: [], origen_tipo: "presupuesto",
  origen_serie: 1, origen_codigo: 77, origen_order_id: null, origen_numero: "1-000077",
  num_lineas: 3, created_at: null, updated_at: null,
};

/** Forma «pedido»: precio ya NETO (el descuento de la plantilla aplicado). */
const LINEAS_PEDIDO: PlantillaLineaPedido[] = [
  { product_sku: "CDR80WPT", product_codart: "00001", description: "CD TQ 700 MB white Thermal WPT",
    quantity: 100, unit_price: 0.75, tax_rate: 21 },
  { product_sku: "", product_codart: null, description: "Montaje en tienda",
    quantity: 1, unit_price: 60, tax_rate: 21 },
  // CODART que no cabe en F_ART (más de 13): no se manda como tal.
  { product_sku: "TINTA-ECO", product_codart: "TINTAECOSOLV-1L", description: "Tinta ecosolvente 1 L",
    quantity: 2, unit_price: 12.5, tax_rate: 21 },
];
const AVISO = "El descuento del 5 % de la línea 1 va incluido en el precio (neto).";

beforeEach(() => {
  mockSearch = "";
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
  (getPlantillasMeta as jest.Mock).mockReset();
  (getPlantillasMeta as jest.Mock).mockResolvedValue({
    idiomas: ["es", "en", "de", "fr", "nl"], destinos: ["oferta", "proforma", "pedido"],
    max_profundidad: 3, carpeta_defecto: null, gestiona_acceso: false, acceso_restringido: false,
  });
  (listPlantillaCarpetas as jest.Mock).mockReset();
  (listPlantillaCarpetas as jest.Mock).mockResolvedValue({ items: [], carpeta_defecto: null });
  (listPlantillaEtiquetas as jest.Mock).mockReset();
  (listPlantillaEtiquetas as jest.Mock).mockResolvedValue([]);
  mockList.mockReset();
  mockList.mockResolvedValue({ items: [KIT], total: 1, idioma_cliente: null, idioma_fuente: null });
  mockLineas.mockReset();
  mockLineas.mockResolvedValue({ plantilla: KIT, destino: "pedido", lineas: LINEAS_PEDIDO, avisos: [AVISO] });
});

afterEach(() => {
  jest.restoreAllMocks();
});

async function elegirEmpresa(user: ReturnType<typeof userEvent.setup>) {
  await waitFor(() =>
    expect(document.querySelector('#erp-new-order-companies option[value="Duplicoder SL"]'))
      .not.toBeNull(),
  );
  await user.type(screen.getByPlaceholderText("Buscar empresa…"), "Duplicoder SL");
  // La ficha F_CLI rellena NIF y dirección (lo que pide el alta para crear).
  await waitFor(() => expect(screen.getByLabelText("Dirección de envío")).toHaveValue("C Aribau 171"));
}

/** El selector de plantillas del alta de pedido (destino «pedido»). */
function selector() {
  return screen.getByRole("dialog", { name: /^Elegir plantilla para .*pedido$/ });
}

async function usarKit(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "Cargar plantilla…" }));
  const dialog = await waitFor(selector);
  await user.click(await within(dialog).findByRole("button", { name: "Usar la plantilla Kit CD impresos" }));
}

async function crear(user: ReturnType<typeof userEvent.setup>) {
  const submit = screen.getByRole("button", { name: "Crear pedido" });
  await waitFor(() => expect(submit).toBeEnabled());
  await user.click(submit);
  await waitFor(() => expect(mockCreate).toHaveBeenCalled());
  return mockCreate.mock.calls[0][0] as { lines: Record<string, unknown>[] };
}

describe("Alta de pedido — plantillas de documento", () => {
  it("«Cargar plantilla…» abre el selector para el pedido y vuelca las líneas (SKU, precio neto, descripción); al crear, el CODART viaja como product_codart", async () => {
    const user = userEvent.setup();
    render(<NewManualOrderPage />);
    await elegirEmpresa(user);
    await usarKit(user);

    // El selector conoce al cliente (primero las plantillas de su idioma) y
    // pide las líneas en la forma del PEDIDO.
    expect(mockList).toHaveBeenCalledWith(expect.objectContaining({ company_id: "c1", activas: true }));
    await waitFor(() => expect(mockLineas).toHaveBeenCalledWith("pl-1", "pedido"));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: /^Elegir plantilla/ })).toBeNull());

    expect(screen.getByLabelText("SKU línea 1")).toHaveValue("CDR80WPT");
    expect(screen.getByLabelText("Descripción línea 1")).toHaveValue("CD TQ 700 MB white Thermal WPT");
    expect(screen.getByLabelText("Cantidad línea 1")).toHaveValue(100);
    expect(screen.getByLabelText("Precio línea 1")).toHaveValue(0.75);       // neto
    expect(screen.getByLabelText("SKU línea 2")).toHaveValue("");
    expect(screen.getByLabelText("Descripción línea 2")).toHaveValue("Montaje en tienda");
    expect(screen.getByLabelText("Precio línea 2")).toHaveValue(60);
    expect(screen.getByLabelText("SKU línea 3")).toHaveValue("TINTA-ECO");
    expect(screen.queryByLabelText("SKU línea 4")).not.toBeInTheDocument();
    // Aviso de lo que no cabe tal cual (el descuento ya va en el precio).
    expect(screen.getByText(/^Plantilla «Kit CD impresos»: 3 líneas cargadas\./))
      .toHaveTextContent(AVISO);
    // La plantilla solo aporta las líneas: la empresa sigue siendo la elegida.
    expect(screen.getByLabelText("Empresa")).toHaveValue("Duplicoder SL");

    const { lines } = await crear(user);
    expect(lines).toHaveLength(3);
    expect(lines[0]).toEqual({
      product_sku: "CDR80WPT", product_codart: "00001",
      description: "CD TQ 700 MB white Thermal WPT", quantity: 100, unit_price: 0.75, tax_rate: 21,
    });
    // Sin artículo: sin product_codart.
    expect(lines[1]).toMatchObject({ product_sku: "", description: "Montaje en tienda", quantity: 1, unit_price: 60 });
    expect(lines[1]).not.toHaveProperty("product_codart");
    // Un CODART de más de 13 caracteres no cabe en F_ART: viaja solo el SKU.
    expect(lines[2]).toMatchObject({ product_sku: "TINTA-ECO", quantity: 2, unit_price: 12.5 });
    expect(lines[2]).not.toHaveProperty("product_codart");
  });

  it("con líneas ya escritas pregunta: «Cancelar» las AÑADE a las que hay; «Aceptar» las REEMPLAZA", async () => {
    const confirm = jest.spyOn(window, "confirm").mockReturnValue(false);
    const user = userEvent.setup();
    render(<NewManualOrderPage />);
    await elegirEmpresa(user);
    await user.type(screen.getByLabelText("Descripción línea 1"), "Tinta cyan");
    await user.type(screen.getByLabelText("Precio línea 1"), "40");

    await usarKit(user);
    await waitFor(() => expect(confirm).toHaveBeenCalledWith(expect.stringContaining(
      "Aceptar = REEMPLAZARLAS por las de la plantilla «Kit CD impresos».",
    )));
    // Cancelar = añadirlas detrás de la que había.
    await waitFor(() => expect(screen.getByLabelText("SKU línea 4")).toHaveValue("TINTA-ECO"));
    expect(screen.getByLabelText("Descripción línea 1")).toHaveValue("Tinta cyan");
    expect(screen.getByLabelText("SKU línea 2")).toHaveValue("CDR80WPT");
    expect(screen.getByText(/^Plantilla «Kit CD impresos»: 3 líneas añadidas\./)).toBeInTheDocument();

    // Aceptar = reemplazarlas todas.
    confirm.mockReturnValue(true);
    await usarKit(user);
    await waitFor(() => expect(screen.queryByLabelText("SKU línea 4")).not.toBeInTheDocument());
    expect(screen.getByLabelText("SKU línea 1")).toHaveValue("CDR80WPT");
    expect(screen.queryByDisplayValue("Tinta cyan")).not.toBeInTheDocument();
    expect(screen.getByText(/^Plantilla «Kit CD impresos»: 3 líneas cargadas\./)).toBeInTheDocument();
  });

  it("con ?plantilla= en la URL las líneas se cargan al entrar (sin selector ni pregunta) y al crear viaja el CODART", async () => {
    mockSearch = "plantilla=pl-1";
    const confirm = jest.spyOn(window, "confirm");
    const user = userEvent.setup();
    render(<NewManualOrderPage />);
    await waitFor(() => expect(mockLineas).toHaveBeenCalledWith("pl-1", "pedido"));
    await waitFor(() => expect(screen.getByLabelText("SKU línea 1")).toHaveValue("CDR80WPT"));
    expect(screen.getByLabelText("Precio línea 1")).toHaveValue(0.75);
    expect(screen.getByLabelText("Descripción línea 2")).toHaveValue("Montaje en tienda");
    expect(screen.getByText(/^Plantilla «Kit CD impresos»: 3 líneas cargadas\./)).toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: /^Elegir plantilla/ })).toBeNull();
    expect(mockList).not.toHaveBeenCalled();
    expect(confirm).not.toHaveBeenCalled();
    expect(mockLineas).toHaveBeenCalledTimes(1);

    // El cliente se elige después, como siempre; las líneas no se pierden.
    await elegirEmpresa(user);
    expect(screen.getByLabelText("SKU línea 1")).toHaveValue("CDR80WPT");
    const { lines } = await crear(user);
    expect(lines.map((l) => l.product_codart)).toEqual(["00001", undefined, undefined]);
    expect(lines[0]).toMatchObject({ product_sku: "CDR80WPT", unit_price: 0.75 });
  });

  it("con ?plantilla= que no se puede cargar, lo dice y deja el alta en blanco", async () => {
    mockSearch = "plantilla=pl-x";
    mockLineas.mockRejectedValue(new Error("La plantilla ya no existe."));
    render(<NewManualOrderPage />);
    expect(await screen.findByText("La plantilla ya no existe.")).toBeInTheDocument();
    expect(screen.getByLabelText("Descripción línea 1")).toHaveValue("");
    expect(screen.queryByLabelText("Descripción línea 2")).not.toBeInTheDocument();
  });

  it("«Cancelar» en el selector lo cierra sin tocar las líneas", async () => {
    const user = userEvent.setup();
    render(<NewManualOrderPage />);
    await user.type(screen.getByLabelText("Descripción línea 1"), "Tinta cyan");
    await user.click(screen.getByRole("button", { name: "Cargar plantilla…" }));
    const dialog = await waitFor(selector);
    await within(dialog).findByRole("button", { name: "Usar la plantilla Kit CD impresos" });
    await user.click(within(dialog).getByRole("button", { name: "Cancelar" }));
    expect(screen.queryByRole("dialog", { name: /^Elegir plantilla/ })).toBeNull();
    expect(screen.getByLabelText("Descripción línea 1")).toHaveValue("Tinta cyan");
    expect(mockLineas).not.toHaveBeenCalled();
    expect(mockCreate).not.toHaveBeenCalled();
  });

  // El título y la etiqueta del selector llevan el artículo del destino.
  it("el selector del pedido habla «el pedido» (no «la pedido»)", async () => {
    const user = userEvent.setup();
    render(<NewManualOrderPage />);
    await user.click(screen.getByRole("button", { name: "Cargar plantilla…" }));
    const dialog = await screen.findByRole("dialog", { name: "Elegir plantilla para el pedido" });
    expect(within(dialog).getByRole("heading", { name: "Cargar plantilla en el pedido" })).toBeInTheDocument();
  });
});
