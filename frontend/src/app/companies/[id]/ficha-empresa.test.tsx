import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import CompanyDetailPage from "./page";
import {
  archiveCompany, fiscalCheck, getCompany, listCompanyContacts, restoreCompany, viesRevalidate,
} from "../../lib/companiesApi";
import { listFactusolDocuments, listFactusolQuotes, listOrders } from "../../lib/erpApi";

/** Ficha de empresa (rediseño de flujo, Fase 3 · Lote 2 PR-2): cabecera con
 *  NIF + localidad y tres pastillas (estado, vínculo FACTUSOL, régimen) más el
 *  chip VIES; banda gris de archivada con motivo, fecha y «Reactivar» como
 *  primario (el resto de acciones desactivado con el porqué); barra de alerta
 *  CRM ≠ FACTUSOL con «Traer datos»; «Datos fiscales» como panel de pares con
 *  la fila VIES siempre presente («No aplica» o estado + fecha + «Volver a
 *  comprobar»); «Actividad reciente» en una sola tabla con filtro por tipo; y
 *  acciones rápidas. Sin perder las pestañas ni la sección FACTUSOL.
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
  archiveCompany: jest.fn(),
  restoreCompany: jest.fn(),
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
// Fecha formateada reconocible en las aserciones (la real depende del locale).
jest.mock("../../lib/dates", () => ({
  formatBackendDateTime: (iso?: string | null) => (iso ? `fecha(${iso})` : "—"),
}));

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

const ARCHIVED_HINT = "Empresa archivada: pulsa «Reactivar» para volver a operar con ella.";

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

/** Filas del cuerpo de la tabla de actividad. */
function activityRows(): HTMLElement[] {
  const act = screen.getByRole("region", { name: "Actividad reciente" });
  return Array.from(within(act).getByRole("table").querySelectorAll<HTMLElement>("tbody tr"));
}
/** La `dd` de una etiqueta del panel «Datos fiscales». */
function fiscalValue(label: string): HTMLElement {
  const fiscales = screen.getByRole("region", { name: "Datos fiscales" });
  return within(fiscales).getByText(label).nextElementSibling as HTMLElement;
}

describe("Ficha de empresa (rediseño de flujo, Fase 3 · Lote 2 PR-2)", () => {
  it("cabecera: NIF + localidad y tres pastillas (Activa · En FACTUSOL · régimen) con el chip VIES; acciones rápidas", async () => {
    render(<CompanyDetailPage />);
    await screen.findByRole("heading", { name: "SAS La Maison de la Plaque" });
    const cabecera = document.querySelector(".company-ficha-id") as HTMLElement;
    expect(cabecera).toHaveTextContent("FR16339753527");
    expect(cabecera).toHaveTextContent("Is-sur-Tille, FR");
    expect(await within(cabecera).findByText("FR · intracomunitario · exento")).toBeInTheDocument();
    const pills = Array.from(cabecera.querySelectorAll(".company-ficha-pills .erp-flow-pill"))
      .map((p) => p.textContent);
    expect(pills).toEqual(["Activa", "En FACTUSOL · CLI-2760", "FR · intracomunitario · exento"]);
    expect(cabecera).toHaveTextContent("✓ verificado en VIES");
    expect(screen.queryByRole("status", { name: "Empresa archivada" })).toBeNull();
    expect(screen.getByRole("link", { name: "+ Nuevo pedido" }))
      .toHaveAttribute("href", "/erp/orders/new?company_id=c1");
    expect(screen.getByRole("button", { name: "Nueva proforma" })).toBeEnabled();
    expect(await screen.findByRole("button", { name: "Traer datos" })).toBeEnabled();
    await waitFor(() => expect(fiscalCheck).toHaveBeenCalledWith({
      tax_id: "FR16339753527", vat: "FR16339753527", country: "FR", exclude_id: "c1",
    }));
  });

  it("datos fiscales como panel de pares (VIES con fecha y «Volver a comprobar») y actividad unificada en una tabla con filtro por tipo", async () => {
    const user = userEvent.setup();
    render(<CompanyDetailPage />);
    const fiscales = within(await screen.findByRole("region", { name: "Datos fiscales" }));
    expect(await fiscales.findByText("sincronizado")).toBeInTheDocument();
    expect(fiscales.getByText("Intracomunitario (exento)")).toBeInTheDocument();
    expect(fiscales.getByText(/Rue du Chemin Noir · 21120 Is-sur-Tille/)).toBeInTheDocument();
    // Pares etiqueta/valor (dl.erp-kv-grid), nunca tabla.
    expect(screen.getByRole("region", { name: "Datos fiscales" }).querySelector("dl.erp-kv-grid")).not.toBeNull();
    expect(fiscales.queryByRole("table")).toBeNull();
    expect(fiscalValue("NIF / VAT")).toHaveClass("mono");
    const viesRow = fiscalValue("VIES");
    expect(viesRow).toHaveTextContent("✓ verificado en VIES");
    expect(viesRow).toHaveTextContent("SAS LA MAISON DE LA PLAQUE");
    expect(viesRow).toHaveTextContent("comprobado el fecha(2026-09-14T10:00:00)");
    expect(within(viesRow).getByRole("button", { name: "Volver a comprobar" })).toBeEnabled();
    expect(screen.queryByRole("alert")).toBeNull();   // sin diferencias, sin barra
    expect(viesRevalidate).not.toHaveBeenCalled();     // válido y reciente: no se repite

    const act = within(screen.getByRole("region", { name: "Actividad reciente" }));
    expect(await act.findByRole("link", { name: "ARTISJ-9544" }))
      .toHaveAttribute("href", "/erp/orders/o1");
    expect(act.getAllByRole("columnheader").map((h) => h.textContent))
      .toEqual(["Documento", "Tipo", "Fecha", "Importe", "Estado"]);
    // Una sola tabla, por fecha descendente, con los tres orígenes.
    await waitFor(() => expect(activityRows()).toHaveLength(4));
    const [factura, pedido, proforma, cobrada] = activityRows();
    expect(factura).toHaveTextContent("2-526087");
    expect(factura).toHaveTextContent("Factura");
    expect(factura).toHaveTextContent("Por cobrar");
    expect(within(factura).queryByRole("link")).toBeNull();     // sin página propia
    expect(factura.querySelector("td.num")).toHaveTextContent("351,52 €");
    expect(pedido).toHaveTextContent("Pedido");
    expect(pedido).toHaveTextContent("Por facturar");
    expect(within(proforma).getByRole("link", { name: "2-000071" })).toHaveAttribute("href", "/erp/proformas");
    expect(proforma).toHaveTextContent("Proforma");
    expect(proforma).toHaveTextContent("Placas");
    expect(cobrada).toHaveTextContent("2-526079");
    expect(cobrada).toHaveTextContent("Cobrada");
    expect(pedido.querySelectorAll("td[data-label]")).toHaveLength(5);
    // Filtro por tipo.
    await user.click(act.getByRole("button", { name: /^Facturas/ }));
    expect(activityRows().map((r) => r.textContent)).toEqual([
      expect.stringContaining("2-526087"), expect.stringContaining("2-526079"),
    ]);
    await user.click(act.getByRole("button", { name: /^Pedidos/ }));
    expect(activityRows()).toHaveLength(1);
    expect(act.getByRole("button", { name: /^Pedidos/ })).toHaveAttribute("aria-pressed", "true");
    await user.click(act.getByRole("button", { name: /^Proformas/ }));
    expect(activityRows()).toHaveLength(1);
    await user.click(act.getByRole("button", { name: /^Todo/ }));
    expect(activityRows()).toHaveLength(4);
    expect(act.getByText("Contactos").parentElement).toHaveTextContent("1");
    await waitFor(() => expect(listOrders).toHaveBeenCalledWith(
      expect.objectContaining({ company_id: "c1", limit: 20 }),
    ));
    expect(listFactusolDocuments).toHaveBeenCalledWith(
      "facturas", expect.objectContaining({ codcli: "2760", limit: 20 }),
    );
  });

  it("FACTUSOL caído: la tabla lo dice y los pedidos de BoHub siguen", async () => {
    (listFactusolDocuments as jest.Mock).mockRejectedValue(new Error("502"));
    (listFactusolQuotes as jest.Mock).mockRejectedValue(new Error("502"));
    render(<CompanyDetailPage />);
    const act = within(await screen.findByRole("region", { name: "Actividad reciente" }));
    expect(await act.findByRole("status")).toHaveTextContent("FACTUSOL no responde: faltan las facturas y proformas.");
    expect(await act.findByRole("link", { name: "ARTISJ-9544" })).toBeInTheDocument();
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

  it("sin vincular: pastilla «Solo CRM» y barra, «Nueva proforma» deshabilitada, y el enlace lleva a la sección FACTUSOL", async () => {
    (getCompany as jest.Mock).mockResolvedValue({ ...COMPANY, factusol_company_id: null });
    SYNC = { customer: null, diffs: null };
    render(<CompanyDetailPage />);
    await screen.findByRole("heading", { name: "SAS La Maison de la Plaque" });
    const cabecera = document.querySelector(".company-ficha-id") as HTMLElement;
    expect(within(cabecera).getByText("Solo CRM")).toHaveClass("erp-flow-pill");
    expect(cabecera).not.toHaveTextContent("En FACTUSOL");
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
    expect(within(cabecera).getByText("FR · intracomunitario · exento")).toBeInTheDocument();
    expect(fiscales.getByText("Intracomunitario (exento)")).toBeInTheDocument();
    expect(viesRevalidate).toHaveBeenCalledTimes(2);
  });

  it("pendiente: la ficha pide la validación al cargar sin forzar; VIES caído → «no disponible» sin bloquear; «Volver a comprobar» fuerza", async () => {
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
    expect(within(cabecera).getByText("FR · intracomunitario · exento")).toBeInTheDocument();
    expect(viesRevalidate).toHaveBeenCalledTimes(1);          // una sola vez al cargar
    const viesRow = fiscalValue("VIES");
    expect(viesRow).toHaveTextContent("VIES no disponible · pendiente");
    // La fecha del último intento se ve aunque VIES no respondiera.
    expect(viesRow).toHaveTextContent("comprobado el fecha(2026-09-14T10:00:00)");
    expect(viesRow).toHaveTextContent("se reintenta solo en segundo plano");
    await user.click(within(viesRow).getByRole("button", { name: "Volver a comprobar" }));
    await waitFor(() => expect(viesRevalidate).toHaveBeenLastCalledWith("c1", { force: true }));
    // También en «⋯» (además del de Datos fiscales).
    await user.click(screen.getByRole("button", { name: "Más acciones de la empresa" }));
    expect(screen.getByRole("button", { name: "Revalidar en VIES" })).toBeEnabled();
  });

  it("España / fuera de la UE: la fila VIES sigue ahí y dice «No aplica» — sin chip, sin botón y sin consultar", async () => {
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
    const cabecera = document.querySelector(".company-ficha-id") as HTMLElement;
    expect(cabecera).not.toHaveTextContent("VIES");
    const viesRow = fiscalValue("VIES");
    expect(within(viesRow).getByText("No aplica")).toHaveClass("erp-flow-pill");
    expect(fiscales.queryByRole("button", { name: "Volver a comprobar" })).toBeNull();
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

  it("archivada: banda gris con motivo y fecha, «Reactivar» primario (en la banda y en «⋯»), sin «Archivar»", async () => {
    (getCompany as jest.Mock).mockResolvedValue({
      ...COMPANY, factusol_company_id: null, is_archived: true,
      archived_at: "2026-08-30T09:00:00",
      archived_reason: "no en FACTUSOL y sin negocio vivo",
    });
    (restoreCompany as jest.Mock).mockResolvedValue({ ...COMPANY, is_archived: false });
    const user = userEvent.setup();
    render(<CompanyDetailPage />);
    await screen.findByRole("heading", { name: "SAS La Maison de la Plaque" });
    const banner = screen.getByRole("status", { name: "Empresa archivada" });
    expect(banner).toHaveClass("company-archived-band");
    expect(banner).toHaveTextContent("Empresa archivada el fecha(2026-08-30T09:00:00)");
    expect(banner).toHaveTextContent("Motivo: no en FACTUSOL y sin negocio vivo");
    expect(banner).toHaveTextContent("No se ha borrado nada");
    // La banda va arriba del todo, antes de la cabecera de NIF y pastillas.
    const cabecera = document.querySelector(".company-ficha-id") as HTMLElement;
    expect(banner.compareDocumentPosition(cabecera) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(within(cabecera).getByText("Archivada")).toHaveClass("erp-flow-pill");
    expect(cabecera).not.toHaveTextContent("Activa");
    // Sin cliente F_CLI PERO archivada: no debe salir la alerta de «sin vincular».
    expect(screen.queryByText(/sin cliente F_CLI no hay albarán/)).toBeNull();
    // «Reactivar» es el primario de la pantalla: el «+ Nuevo pedido» se queda
    // desactivado (con el porqué) y ya no es un enlace.
    const reactivar = within(banner).getByRole("button", { name: "Reactivar" });
    expect(reactivar).toHaveClass("button");
    expect(reactivar).not.toHaveClass("secondary");
    expect(screen.queryByRole("link", { name: "+ Nuevo pedido" })).toBeNull();
    const nuevoPedido = screen.getByRole("button", { name: "+ Nuevo pedido" });
    expect(nuevoPedido).toBeDisabled();
    expect(nuevoPedido).toHaveAttribute("title", ARCHIVED_HINT);
    expect(screen.getByRole("button", { name: "Nueva proforma" })).toBeDisabled();
    // En «⋯»: «Reactivar», nunca «Archivar» ni «Restaurar».
    await user.click(screen.getByRole("button", { name: "Más acciones de la empresa" }));
    expect(screen.getAllByRole("button", { name: "Reactivar" })).toHaveLength(2);
    expect(screen.queryByRole("button", { name: "Archivar" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Restaurar" })).toBeNull();
    // El botón de la banda reactiva (mismo endpoint de siempre).
    await user.click(reactivar);
    await waitFor(() => expect(restoreCompany).toHaveBeenCalledWith("c1"));
    await waitFor(() => expect(screen.queryByRole("status", { name: "Empresa archivada" })).toBeNull());
    expect(screen.getByRole("link", { name: "+ Nuevo pedido" })).toBeInTheDocument();
  });

  it("archivada y vinculada: las demás acciones se quedan, desactivadas y con el porqué; Fusionar y Borrar siguen", async () => {
    (getCompany as jest.Mock).mockResolvedValue({
      ...COMPANY, is_archived: true, archived_at: null, archived_reason: null,
    });
    const user = userEvent.setup();
    render(<CompanyDetailPage />);
    await screen.findByRole("heading", { name: "SAS La Maison de la Plaque" });
    const banner = screen.getByRole("status", { name: "Empresa archivada" });
    expect(banner).toHaveTextContent("Motivo: sin indicar");
    expect(banner).toHaveTextContent("no se pueden crear pedidos ni proformas");
    const cabecera = document.querySelector(".company-ficha-id") as HTMLElement;
    expect(cabecera).toHaveTextContent("Archivada");
    expect(cabecera).toHaveTextContent("En FACTUSOL · CLI-2760");
    const proforma = screen.getByRole("button", { name: "Nueva proforma" });
    expect(proforma).toBeDisabled();
    expect(proforma).toHaveAttribute("title", ARCHIVED_HINT);
    const traer = await screen.findByRole("button", { name: "Traer datos" });
    expect(traer).toBeDisabled();
    expect(traer).toHaveAttribute("title", ARCHIVED_HINT);
    const fiscales = within(screen.getByRole("region", { name: "Datos fiscales" }));
    const volver = fiscales.getByRole("button", { name: "Volver a comprobar" });
    expect(volver).toBeDisabled();
    expect(volver).toHaveAttribute("title", ARCHIVED_HINT);
    await user.click(screen.getByRole("button", { name: "Más acciones de la empresa" }));
    const regimen = screen.getByRole("button", { name: "Comprobar régimen de IVA" });
    expect(regimen).toBeDisabled();
    expect(regimen).toHaveAttribute("title", ARCHIVED_HINT);
    expect(screen.getByRole("button", { name: "Revalidar en VIES" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Fusionar" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Borrar" })).toBeEnabled();
    expect(screen.getAllByRole("button", { name: "Reactivar" })).toHaveLength(2);
    // Archivada: tampoco las alertas de flujo (VIES / CRM ≠ FACTUSOL).
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("no archivada: «Archivar» en «⋯» archiva y muestra la banda", async () => {
    (archiveCompany as jest.Mock).mockResolvedValue({
      ...COMPANY, is_archived: true, archived_at: "2026-09-15T08:00:00", archived_reason: "archivada a mano",
    });
    const confirmSpy = jest.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();
    render(<CompanyDetailPage />);
    await screen.findByRole("heading", { name: "SAS La Maison de la Plaque" });
    await user.click(screen.getByRole("button", { name: "Más acciones de la empresa" }));
    await user.click(screen.getByRole("button", { name: "Archivar" }));
    await waitFor(() => expect(archiveCompany).toHaveBeenCalledWith("c1"));
    expect(confirmSpy.mock.calls[0][0]).toContain("«Reactivar»");
    const banner = await screen.findByRole("status", { name: "Empresa archivada" });
    expect(banner).toHaveTextContent("archivada a mano");
    expect(banner).toHaveTextContent("fecha(2026-09-15T08:00:00)");
    confirmSpy.mockRestore();
  });
});
