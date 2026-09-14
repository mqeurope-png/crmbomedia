import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import CompanyDetailPage from "./page";
import {
  fiscalCheck, getCompany, listCompanyContacts, viesRevalidate,
} from "../../lib/companiesApi";
import { listFactusolDocuments, listFactusolQuotes, listOrders } from "../../lib/erpApi";

/** Ficha de empresa (rediseño de flujo, Fase 3): cabecera con NIF + país /
 *  régimen + vínculo FACTUSOL, barra de alerta CRM ≠ FACTUSOL con «Traer
 *  datos», datos fiscales con estado de sincronía, actividad reciente con
 *  estados, y acciones rápidas. Sin perder las pestañas ni la sección FACTUSOL.
 *  Fase VIES: chip «✓ verificado en VIES» / «pendiente» / «VAT no válido»,
 *  «Revalidar en VIES», alerta bloqueante con VAT no válido y comprobación al
 *  cargar cuando está pendiente (VIES caído → «no disponible» sin bloquear). */

const push = jest.fn();
jest.mock("next/navigation", () => ({
  useParams: () => ({ id: "c1" }),
  useRouter: () => ({ push, replace: jest.fn() }),
}));
jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ children, href, className }: { children: React.ReactNode; href: string; className?: string }) => (
    <a href={href} className={className}>{children}</a>
  ),
}));
jest.mock("../../components/PageHeader", () => ({
  PageHeader: ({ title, actions }: { title: string; actions?: React.ReactNode }) => (
    <div><h1>{title}</h1>{actions}</div>
  ),
}));
jest.mock("../../lib/companiesApi", () => ({
  getCompany: jest.fn(),
  listCompanyContacts: jest.fn(),
  listCompanies: jest.fn(() => Promise.resolve({ items: [], total: 0 })),
  mergeCompanies: jest.fn(),
  updateCompany: jest.fn(),
  deleteCompany: jest.fn(),
  fiscalCheck: jest.fn(),
  viesRevalidate: jest.fn(),
}));
jest.mock("../../lib/api", () => ({
  getCurrentUser: jest.fn(() => Promise.resolve({ role: "admin" })),
}));
jest.mock("../../lib/erpApi", () => ({
  ERP_EDIT_ROLES: ["admin", "pedidos"],
  listOrders: jest.fn(),
  listFactusolDocuments: jest.fn(),
  listFactusolQuotes: jest.fn(),
}));
jest.mock("../../lib/dates", () => ({ formatBackendDateTime: () => "—" }));

// La sección FACTUSOL (con sus propios tests) se simula: informa de la
// sincronía a la ficha y refleja las señales que recibe de la cabecera.
const panelProps: Record<string, unknown>[] = [];
jest.mock("../../components/erp/CompanyFactusolPanel", () => ({
  CompanyFactusolPanel: (props: {
    onSync?: (s: unknown) => void; pullSignal?: number; regimeSignal?: number;
    inlineDiffAlert?: boolean;
  }) => {
    panelProps.push(props);
    const { useEffect } = jest.requireActual("react");
    useEffect(() => {
      props.onSync?.(SYNC);
    }, []);
    return (
      <div>
        FACTUSOL PANEL pull:{props.pullSignal ?? 0} regime:{props.regimeSignal ?? 0}
        {" "}inline:{String(props.inlineDiffAlert)}
      </div>
    );
  },
}));
jest.mock("../../components/erp/CompanyQuotesPanel", () => ({
  CompanyQuotesPanel: ({ createSignal }: { createSignal?: number }) => (
    <div>QUOTES PANEL create:{createSignal ?? 0}</div>
  ),
}));

let SYNC: { customer: unknown; diffs: { field: string }[] | null } = { customer: null, diffs: null };

const VIES_OK = {
  applies: true, vat: "FR16339753527", status: "valido", valid: true,
  checked_at: "2026-09-14T10:00:00", name: "SAS LA MAISON DE LA PLAQUE", address: null,
  stale: false,
};
const VIES_KO = { ...VIES_OK, status: "no_valido", valid: false, name: null };
const VIES_PENDIENTE = { ...VIES_KO, status: "pendiente", valid: null, checked_at: null };
const VIES_CAIDO = { ...VIES_KO, status: "desconocido", valid: null, error: "VIES HTTP 500" };

const COMPANY = {
  id: "c1", name: "SAS La Maison de la Plaque", website: null, domain: "maisonplaque.fr",
  tax_id: "FR16339753527", vat: "FR16339753527", country: "FR", region: null,
  state: null, city: "Is-sur-Tille", address_line: "Rue du Chemin Noir",
  postal_code: "21120", sector: null, size_category: null, notes: null,
  source: "manual", factusol_company_id: "2760",
  vies_status: "valido", vies_vat: "FR16339753527", vies_name: "SAS LA MAISON DE LA PLAQUE",
  vies: VIES_OK,
  created_at: "2026-01-01T00:00:00", updated_at: "2026-01-01T00:00:00",
};

const FISCAL = {
  country_iso2: "FR", in_eu: true, regime: "intracomunitario",
  regime_label: "Intracomunitario (exento)",
  regime_reason: "FR (UE) con NIF-IVA FR16339753527 verificado en VIES → intracomunitario",
  vat_normalized: "FR16339753527",
  duplicates: { crm: [], factusol: null, factusol_checked: false, factusol_error: null },
  vies: VIES_OK,
};

beforeEach(() => {
  panelProps.length = 0;
  SYNC = {
    customer: { regime: "intracomunitario", regime_label: "Intracomunitario (exento)" },
    diffs: [],
  };
  (getCompany as jest.Mock).mockReset();
  (getCompany as jest.Mock).mockResolvedValue(COMPANY);
  (listCompanyContacts as jest.Mock).mockReset();
  (listCompanyContacts as jest.Mock).mockResolvedValue([
    { id: "ct1", first_name: "Alexandre", last_name: null, email: "alex@maison.fr",
      phone: null, commercial_status: "cliente", owner_user_id: null },
  ]);
  (fiscalCheck as jest.Mock).mockReset();
  (fiscalCheck as jest.Mock).mockResolvedValue(FISCAL);
  (viesRevalidate as jest.Mock).mockReset();
  (listOrders as jest.Mock).mockReset();
  (listOrders as jest.Mock).mockResolvedValue({
    items: [{
      id: "o1", order_number: "ARTISJ-9544", placed_at: "2026-09-08T10:00:00",
      total_amount: 351.52, currency: "EUR",
      workflow: { queue: "por_facturar", queue_label: "Por facturar" },
    }],
    queue_counts: {}, queue: null,
  });
  (listFactusolDocuments as jest.Mock).mockReset();
  (listFactusolDocuments as jest.Mock).mockResolvedValue({
    items: [
      { numero: "2-526079", fecha: "2026-09-01", total: 120, saldo_pendiente: 0, estado_label: "Cobrada" },
      { numero: "2-526087", fecha: "2026-09-10", total: 351.52, saldo_pendiente: 351.52, estado_label: "Pendiente" },
    ],
    total: 2,
  });
  (listFactusolQuotes as jest.Mock).mockReset();
  (listFactusolQuotes as jest.Mock).mockResolvedValue({
    items: [{ codpre: "2-000071", referencia: "Placas", fecha: "2026-09-04", total: 5059,
              clipre: "2760", cliente_nombre: "LA MAISON", base: 5059, iva: 0 }],
    unlinked: false,
  });
});

describe("Ficha de empresa (rediseño de flujo, Fase 3)", () => {
  it("cabecera: NIF + país/régimen + vínculo FACTUSOL, y las acciones rápidas", async () => {
    render(<CompanyDetailPage />);
    await screen.findByRole("heading", { name: "SAS La Maison de la Plaque" });
    const cabecera = document.querySelector(".company-ficha-id") as HTMLElement;
    expect(cabecera).toHaveTextContent("FR16339753527");
    expect(await screen.findByText("FR · intracomunitario · exento")).toBeInTheDocument();
    expect(cabecera).toHaveTextContent("✓ verificado en VIES");
    expect(cabecera).toHaveTextContent("FACTUSOL nº 2760 ✓");
    expect(screen.getByRole("link", { name: "+ Nuevo pedido" }))
      .toHaveAttribute("href", "/erp/orders/new?company_id=c1");
    expect(screen.getByRole("button", { name: "Nueva proforma" })).toBeEnabled();
    expect(await screen.findByRole("button", { name: "Traer datos" })).toBeInTheDocument();
    await waitFor(() => expect(fiscalCheck).toHaveBeenCalledWith({
      tax_id: "FR16339753527", vat: "FR16339753527", country: "FR", exclude_id: "c1",
    }));
  });

  it("datos fiscales con estado de sincronía y actividad reciente con sus estados", async () => {
    render(<CompanyDetailPage />);
    const fiscales = within(await screen.findByRole("region", { name: "Datos fiscales" }));
    expect(await fiscales.findByText("sincronizado")).toBeInTheDocument();
    expect(fiscales.getByText("Intracomunitario (exento)")).toBeInTheDocument();
    expect(fiscales.getByText(/Rue du Chemin Noir · 21120 Is-sur-Tille/)).toBeInTheDocument();
    expect(fiscales.getByText("VIES").parentElement)
      .toHaveTextContent("✓ verificado en VIES · SAS LA MAISON DE LA PLAQUE");
    expect(screen.queryByRole("alert")).toBeNull();   // sin diferencias, sin barra
    expect(viesRevalidate).not.toHaveBeenCalled();     // válido y reciente: no se repite

    const act = within(screen.getByRole("region", { name: "Actividad reciente" }));
    expect(await act.findByRole("link", { name: "Pedido ARTISJ-9544" }))
      .toHaveAttribute("href", "/erp/orders/o1");
    expect(act.getByText("por facturar")).toBeInTheDocument();
    expect(act.getByText("Factura 2-526079").parentElement?.parentElement).toHaveTextContent("cobrada");
    expect(act.getByText("Factura 2-526087").parentElement?.parentElement).toHaveTextContent("pend. cobro");
    expect(act.getByText("Proforma 2-000071")).toBeInTheDocument();
    expect(act.getByText("Contactos").parentElement).toHaveTextContent("1");
    await waitFor(() => expect(listOrders).toHaveBeenCalledWith(
      expect.objectContaining({ company_id: "c1" }),
    ));
    expect(listFactusolDocuments).toHaveBeenCalledWith("facturas", expect.objectContaining({ codcli: "2760" }));
  });

  it("CRM ≠ FACTUSOL: barra de alerta arriba con «Traer datos de FACTUSOL» que abre la previsualización de la sección", async () => {
    SYNC = { customer: { regime: "intracomunitario" }, diffs: [{ field: "Dirección" }, { field: "CP" }] };
    const user = userEvent.setup();
    render(<CompanyDetailPage />);
    const alerta = await screen.findByRole("alert", { name: "Alertas de la empresa" });
    expect(alerta).toHaveTextContent("Los datos del CRM no coinciden con FACTUSOL (Dirección, CP)");
    expect(alerta).toHaveTextContent("FACTUSOL es la fuente de verdad");
    const fiscales = within(screen.getByRole("region", { name: "Datos fiscales" }));
    expect(fiscales.getByText("difiere de FACTUSOL")).toBeInTheDocument();
    // La sección de abajo NO repite la alerta (solo la tabla de diferencias).
    expect(screen.getByText(/inline:false/)).toBeInTheDocument();
    await user.click(within(alerta).getByRole("button", { name: "Traer datos de FACTUSOL" }));
    expect(await screen.findByText(/pull:1/)).toBeInTheDocument();
  });

  it("sin vincular: aviso en cabecera y barra, «Nueva proforma» deshabilitada, y el enlace lleva a la sección FACTUSOL", async () => {
    (getCompany as jest.Mock).mockResolvedValue({ ...COMPANY, factusol_company_id: null });
    SYNC = { customer: null, diffs: null };
    render(<CompanyDetailPage />);
    await screen.findByRole("heading", { name: "SAS La Maison de la Plaque" });
    expect(screen.getByText("Sin vincular a FACTUSOL")).toBeInTheDocument();
    const alerta = screen.getByRole("alert", { name: "Alertas de la empresa" });
    expect(alerta).toHaveTextContent("sin cliente F_CLI no hay albarán, factura ni proforma");
    expect(within(alerta).getByRole("link", { name: "Vincular o crear en FACTUSOL" }))
      .toHaveAttribute("href", "#factusol");
    expect(screen.getByRole("button", { name: "Nueva proforma" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Traer datos" })).toBeNull();
    const fiscales = within(screen.getByRole("region", { name: "Datos fiscales" }));
    expect(fiscales.getByText("sin vincular")).toBeInTheDocument();
    // Sin cliente FACTUSOL no se leen facturas ni proformas.
    expect(listFactusolDocuments).not.toHaveBeenCalled();
  });

  it("«Nueva proforma» abre la pestaña de proformas con el alta lanzada; «⋯» conserva Comprobar régimen, Fusionar y Borrar", async () => {
    const user = userEvent.setup();
    render(<CompanyDetailPage />);
    await screen.findByRole("heading", { name: "SAS La Maison de la Plaque" });
    await user.click(screen.getByRole("button", { name: "Nueva proforma" }));
    expect(await screen.findByText("QUOTES PANEL create:1")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Más acciones de la empresa" }));
    expect(screen.getByRole("button", { name: "Fusionar" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Borrar" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Comprobar régimen de IVA" }));
    expect(await screen.findByText(/regime:1/)).toBeInTheDocument();
  });

  it("VAT no válido en VIES: chip, alerta bloqueante y «Revalidar en VIES»; si VIES ya lo da por bueno, se quita la alerta y vuelve a intracomunitario", async () => {
    (getCompany as jest.Mock).mockResolvedValue({ ...COMPANY, vies_status: "no_valido", vies: VIES_KO });
    (fiscalCheck as jest.Mock).mockResolvedValue({
      ...FISCAL, regime: "nacional", regime_label: "Nacional (con IVA)",
      regime_reason: "FR (UE) con NIF-IVA FR16339753527 NO válido en VIES → nacional (no se puede eximir)",
      vies: VIES_KO,
    });
    // Al cargar se pide (sin forzar) por si el veredicto negativo ha cambiado:
    // sigue siendo no válido. Al pulsar «Revalidar» (forzado) VIES ya lo da por bueno.
    (viesRevalidate as jest.Mock)
      .mockResolvedValueOnce({
        vies: VIES_KO, regime: "nacional", regime_label: "Nacional (con IVA)",
        regime_reason: "FR (UE) con NIF-IVA FR16339753527 NO válido en VIES → nacional (no se puede eximir)",
        company: { ...COMPANY, vies_status: "no_valido", vies_name: null },
      })
      .mockResolvedValue({
        vies: VIES_OK, regime: "intracomunitario", regime_label: "Intracomunitario (exento)",
        regime_reason: FISCAL.regime_reason, company: COMPANY,
      });
    const user = userEvent.setup();
    render(<CompanyDetailPage />);
    await screen.findByRole("heading", { name: "SAS La Maison de la Plaque" });
    const cabecera = document.querySelector(".company-ficha-id") as HTMLElement;
    expect(cabecera).toHaveTextContent("VAT no válido en VIES");
    const fiscales = within(screen.getByRole("region", { name: "Datos fiscales" }));
    expect(await fiscales.findByText("Nacional (con IVA)")).toBeInTheDocument();
    expect(fiscales.getByText(/NO válido en VIES → nacional/)).toBeInTheDocument();
    const alerta = screen.getByRole("alert", { name: "Alertas de la empresa" });
    expect(alerta).toHaveTextContent("El NIF-IVA FR16339753527 NO es válido en VIES");
    expect(alerta).toHaveTextContent("no se puede eximir de IVA");
    expect(alerta.className).toContain("is-blocking");
    await waitFor(() => expect(viesRevalidate).toHaveBeenCalledWith("c1", { force: false }));
    expect(viesRevalidate).toHaveBeenCalledTimes(1);
    expect(cabecera).toHaveTextContent("VAT no válido en VIES");     // sigue igual
    await user.click(within(alerta).getByRole("button", { name: "Revalidar en VIES" }));
    await waitFor(() => expect(viesRevalidate).toHaveBeenLastCalledWith("c1", { force: true }));
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
    expect(cabecera).toHaveTextContent("✓ verificado en VIES");
    expect(screen.getByText("FR · intracomunitario · exento")).toBeInTheDocument();
    expect(fiscales.getByText("Intracomunitario (exento)")).toBeInTheDocument();
    expect(viesRevalidate).toHaveBeenCalledTimes(2);
  });

  it("pendiente: la ficha pide la validación al cargar sin forzar; VIES caído → «no disponible» sin bloquear; el botón de Datos fiscales fuerza", async () => {
    (getCompany as jest.Mock).mockResolvedValue({ ...COMPANY, vies_status: null, vies: VIES_PENDIENTE });
    (fiscalCheck as jest.Mock).mockResolvedValue({ ...FISCAL, vies: VIES_PENDIENTE });
    (viesRevalidate as jest.Mock).mockResolvedValue({
      vies: VIES_CAIDO, regime: "intracomunitario", regime_label: "Intracomunitario (exento)",
      regime_reason: "FR (UE) con NIF-IVA FR16339753527 → intracomunitario",
      company: { ...COMPANY, vies_status: "desconocido", vies_name: null },
    });
    const user = userEvent.setup();
    render(<CompanyDetailPage />);
    await screen.findByRole("heading", { name: "SAS La Maison de la Plaque" });
    await waitFor(() => expect(viesRevalidate).toHaveBeenCalledWith("c1", { force: false }));
    const cabecera = document.querySelector(".company-ficha-id") as HTMLElement;
    await waitFor(() => expect(cabecera).toHaveTextContent("VIES no disponible · pendiente"));
    expect(screen.queryByRole("alert")).toBeNull();          // no bloquea
    expect(screen.getByText("FR · intracomunitario · exento")).toBeInTheDocument();
    expect(viesRevalidate).toHaveBeenCalledTimes(1);          // una sola vez al cargar
    const fiscales = within(screen.getByRole("region", { name: "Datos fiscales" }));
    await user.click(fiscales.getByRole("button", { name: "Revalidar en VIES" }));
    await waitFor(() => expect(viesRevalidate).toHaveBeenLastCalledWith("c1", { force: true }));
    // También en «⋯» (además del de Datos fiscales).
    await user.click(screen.getByRole("button", { name: "Más acciones de la empresa" }));
    expect(screen.getAllByRole("button", { name: "Revalidar en VIES" })).toHaveLength(2);
  });

  it("España / fuera de la UE: VIES no aplica — sin chip, sin fila y sin consultar", async () => {
    (getCompany as jest.Mock).mockResolvedValue({
      ...COMPANY, country: "ES", vat: null, tax_id: "B12345678", vies_status: null,
      vies: { ...VIES_PENDIENTE, applies: false, vat: null, status: null },
    });
    (fiscalCheck as jest.Mock).mockResolvedValue({
      ...FISCAL, country_iso2: "ES", regime: "nacional", regime_label: "Nacional (con IVA)",
      regime_reason: "España → nacional", vat_normalized: null,
      vies: { ...VIES_PENDIENTE, applies: false, vat: null, status: null },
    });
    render(<CompanyDetailPage />);
    await screen.findByRole("heading", { name: "SAS La Maison de la Plaque" });
    const fiscales = within(screen.getByRole("region", { name: "Datos fiscales" }));
    expect(await fiscales.findByText("Nacional (con IVA)")).toBeInTheDocument();
    expect(screen.queryByText(/VIES/)).toBeNull();
    expect(viesRevalidate).not.toHaveBeenCalled();
  });

  it("no se pierde nada: pestañas Datos (con Guardar), Contactos y Proformas siguen ahí", async () => {
    const user = userEvent.setup();
    render(<CompanyDetailPage />);
    await screen.findByRole("heading", { name: "SAS La Maison de la Plaque" });
    expect(screen.getByRole("button", { name: /Guardar/ })).toBeInTheDocument();
    expect(screen.getByDisplayValue("Rue du Chemin Noir")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Contactos \(1\)/ }));
    expect(screen.getByRole("link", { name: /Alexandre/ })).toHaveAttribute("href", "/contacts/ct1");
    await user.click(screen.getByRole("button", { name: /Proformas FACTUSOL/ }));
    expect(screen.getByText("QUOTES PANEL create:0")).toBeInTheDocument();
    expect(screen.getByText(/FACTUSOL PANEL/)).toBeInTheDocument();
  });
});
