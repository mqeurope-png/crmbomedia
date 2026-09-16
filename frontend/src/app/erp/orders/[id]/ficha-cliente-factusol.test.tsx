import { render, screen } from "@testing-library/react";
import ErpOrderDetailPage from "./page";
import { getOrder, getOrderFactusolCustomer } from "../../../lib/erpApi";
import { getCompany } from "../../../lib/companiesApi";

/** ERP · Lote 4 — cliente FACTUSOL también en la ficha de los pedidos WEB.
 *
 *  Con EMPRESA CRM vinculada se REUTILIZA el `CompanyFactusolPanel` de la ficha
 *  de empresa, resuelto por `company_id` (no se regresa ese flujo). Sin empresa,
 *  el cliente EXISTE igual: el backend lo resuelve por el CLIFAC de la factura /
 *  el CLIALB del albarán y la ficha lo pinta; si no hay nada por lo que
 *  resolverlo (`found:false`), un aviso «sin cliente» discreto. Se prueba de
 *  punta a punta con la ficha real (envoltorio + paneles reales). */

jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ children }: { children: React.ReactNode }) => <span>{children}</span>,
}));
jest.mock("next/navigation", () => ({
  useParams: () => ({ id: "o-1" }),
  useSearchParams: () => new URLSearchParams(""),
}));
jest.mock("../../../components/PageHeader", () => ({
  PageHeader: ({ title, actions }: { title: string; actions?: React.ReactNode }) => (
    <><h1>{title}</h1>{actions}</>
  ),
}));
jest.mock("../../../components/erp/EmbalarModal", () => ({ EmbalarModal: () => null }));
jest.mock("../../../components/erp/FactusolDocumentDetailModal", () => ({
  PDF_LANGS: [{ value: "es", label: "Español" }],
}));
jest.mock("../../../components/erp/InvoiceEmailModal", () => ({ InvoiceEmailModal: () => null }));
jest.mock("../../../components/erp/OrderEmailModal", () => ({ OrderEmailModal: () => null }));
jest.mock("../../../components/erp/RegistrarCobroModal", () => ({ RegistrarCobroModal: () => null }));
jest.mock("../../../components/erp/EmitFactusolButton", () => ({
  EmitFactusolButton: () => <span>emitir</span>,
}));
jest.mock("../../../components/erp/OrderStatusMachine", () => ({ OrderStatusMachine: () => null }));
jest.mock("../../../components/erp/ShippingFilesSection", () => ({ ShippingFilesSection: () => null }));
// OJO: aquí NO se mockea OrderFactusolClientPanel ni CompanyFactusolPanel: se
// prueban de verdad. Solo la lectura de la empresa (companiesApi) es un mock.
jest.mock("../../../lib/companiesApi", () => ({
  getCompany: jest.fn(),
}));
jest.mock("../../../lib/api", () => ({
  getCurrentUser: jest.fn(() => Promise.resolve({ role: "admin" })),
}));
jest.mock("../../../lib/erpApi", () => ({
  ERP_EDIT_ROLES: ["admin", "pedidos"],
  customerLabel: () => "Duplicoder",
  getOrder: jest.fn(),
  getOrderTimeline: jest.fn(() => Promise.resolve({ total: 0, items: [] })),
  getFactusolStatus: jest.fn(() => Promise.resolve({ status: "none" })),
  getErpSettings: jest.fn(() => Promise.resolve({ shipping_origins: [] })),
  getOrderFactusolInvoiceRef: jest.fn(),
  getOrderFactusolCobro: jest.fn(() => Promise.resolve({ status: "pendiente", invoice: null })),
  downloadOrderFactusolPedidoPdf: jest.fn(),
  fireTransition: jest.fn(),
  saveBlob: jest.fn(),
  updateOrderLanguage: jest.fn(),
  updateOrderSeguimiento: jest.fn(),
  completeOrder: jest.fn(),
  uncompleteOrder: jest.fn(),
  resolveOrderCobroStatus: (
    o: { factusol_cobro_status?: string | null },
    live: { status?: string | null } | null | undefined,
  ) => {
    const s = live?.status === "cobrada" || live?.status === "pendiente" ? live.status : null;
    return s ?? o.factusol_cobro_status ?? null;
  },
  // Lo consume el CompanyFactusolPanel REAL al montar (detecta divergencias
  // leyendo el cliente F_CLI por su CÓDIGO). Aquí devuelve el cliente vinculado.
  searchFactusolCustomers: jest.fn(() => Promise.resolve([
    {
      codcli: "1043", nofcli: "Duplicoder S.L.", nifcli: "B12345678",
      domcli: "C/ Mayor 1", pobcli: "Sabadell", cpocli: "08201", procli: "Barcelona",
    },
  ])),
  // Sin empresa CRM: el panel resuelve el cliente por CODCLI/CLIFAC contra el
  // backend (cada test fija su respuesta).
  getOrderFactusolCustomer: jest.fn(),
  completeOrderFactusolCustomer: jest.fn(),
}));

function detail(over = {}) {
  return {
    id: "o-1", order_number: "BOPRIN-99930", contact_name: null, company_name: "Duplicoder",
    external_source: "woocommerce", store_id: null, contact_id: null, company_id: "c-1",
    total_amount: 72.6, currency: "EUR", payment_status: "paid",
    preparation_status: "packed", transport_status: "not_shipped",
    invoice_status: "not_invoiced", tracking_number: null, factusol_invoice_number: null,
    factusol_albaran_number: null, factusol_payment: null, factusol_document: null,
    factusol_cobro_status: null, factusol_invoice_serie: null, factusol_cobro: null,
    serial_number: null, whiterip_license: null, shipping_origin: null, language: "es",
    approved_at: null, placed_at: "2026-09-01T10:00:00", created_at: "2026-09-01T10:00:00",
    externally_processed_at: null, externally_processed_note: null,
    externally_processed_by_user_id: null, notes: null, packing: null, lines: [],
    status_history: [], exceptions: [], available_transitions: {}, blockers: [], warnings: [],
    completed: false, completed_at: null, completed_by_user_id: null, completed_by_name: null,
    workflow: {
      queue: "por_facturar", queue_label: "Por facturar",
      next_action: "emitir_factura", next_action_label: "Emitir factura",
      next_action_hint: "Emite la factura en FACTUSOL.", blocked: false,
      regime: "nacional", steps: [], alerts: [],
      company: { id: "c-1", name: "Duplicoder", country: "ES", factusol_id: "1043", regime: "nacional" },
    },
    ...over,
  };
}

function company(over = {}) {
  return {
    id: "c-1", name: "Duplicoder S.L.", website: null, domain: null,
    tax_id: "B12345678", vat: "ESB12345678", country: "ES", region: null, state: "Barcelona",
    city: "Sabadell", address_line: "C/ Mayor 1", postal_code: "08201", sector: null,
    size_category: null, notes: null, source: "manual", is_active: true,
    factusol_company_id: "1043", external_references: {}, custom_fields: {},
    contacts_count: 0, created_at: "2026-01-01T00:00:00", updated_at: "2026-01-01T00:00:00",
    ...over,
  };
}

beforeEach(() => {
  (getOrder as jest.Mock).mockReset();
  (getCompany as jest.Mock).mockReset();
  (getOrderFactusolCustomer as jest.Mock).mockReset();
  window.history.replaceState({}, "", "/erp/orders/o-1");
});

describe("ERP · Ficha del pedido — cliente FACTUSOL en pedidos WEB (Lote 4)", () => {
  it("un pedido web con company_id lee la empresa y pinta el panel del cliente FACTUSOL", async () => {
    (getOrder as jest.Mock).mockResolvedValue(detail());
    (getCompany as jest.Mock).mockResolvedValue(company());
    render(<ErpOrderDetailPage />);
    // El panel REAL de la ficha de empresa, reutilizado: enseña el nº F_CLI.
    expect(await screen.findByText("Cliente FACTUSOL nº 1043")).toBeInTheDocument();
    expect(getCompany).toHaveBeenCalledWith("c-1");
    expect(getOrderFactusolCustomer).not.toHaveBeenCalled();
  });

  it("un pedido web SIN empresa pero con factura resuelve y pinta el cliente por CLIFAC", async () => {
    (getOrder as jest.Mock).mockResolvedValue(detail({
      company_id: null,
      workflow: { ...detail().workflow, company: null },
    }));
    (getOrderFactusolCustomer as jest.Mock).mockResolvedValue({
      found: true, codcli: "260090", source: "factura", missing: [],
      company_id: null,
      cliente: {
        codcli: "260090", nombre: "Escola La Muntanyeta", nofcli: "Escola La Muntanyeta",
        nif: "G12345678", regime_label: "Nacional",
      },
    });
    render(<ErpOrderDetailPage />);
    expect(await screen.findByText("Cliente FACTUSOL nº 260090")).toBeInTheDocument();
    expect(screen.getByText("Escola La Muntanyeta")).toBeInTheDocument();
    expect(getOrderFactusolCustomer).toHaveBeenCalledWith("o-1");
    expect(getCompany).not.toHaveBeenCalled();
  });

  it("un pedido web sin cliente resoluble (found:false) muestra el aviso «sin cliente»", async () => {
    (getOrder as jest.Mock).mockResolvedValue(detail({
      company_id: null,
      workflow: { ...detail().workflow, company: null },
    }));
    (getOrderFactusolCustomer as jest.Mock).mockResolvedValue({
      found: false, codcli: null, source: null, cliente: null, missing: [], company_id: null,
    });
    render(<ErpOrderDetailPage />);
    expect(await screen.findByText(/Sin cliente FACTUSOL/)).toBeInTheDocument();
    expect(getCompany).not.toHaveBeenCalled();
    expect(document.querySelector(".form-error")).toBeNull();
  });
});
