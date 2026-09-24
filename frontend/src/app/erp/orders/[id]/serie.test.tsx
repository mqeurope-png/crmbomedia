import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ErpOrderDetailPage from "./page";
import { changeOrderFactusolSerie, getOrder, waitForQuoteJob } from "../../../lib/erpApi";

/** Lote 7 · P1 — «Cambiar serie» (empresa emisora) desde la ficha de un pedido
 *  manual: el modal avisa de que borra y recrea el albarán y llama al endpoint. */

jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ children, href }: { children: React.ReactNode; href: string }) => (
    <a href={href}>{children}</a>
  ),
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
jest.mock("../../../components/erp/GeneiShipmentSection", () => ({ GeneiShipmentSection: () => null }));
jest.mock("../../../components/erp/FactusolDocumentDetailModal", () => ({
  PDF_LANGS: [{ value: "es", label: "Español" }],
}));
jest.mock("../../../components/erp/InvoiceEmailModal", () => ({ InvoiceEmailModal: () => null }));
jest.mock("../../../components/erp/OrderEmailModal", () => ({ OrderEmailModal: () => null }));
jest.mock("../../../components/erp/CancelOrderModal", () => ({ CancelOrderModal: () => null }));
jest.mock("../../../components/erp/EmitFactusolButton", () => ({ EmitFactusolButton: () => null }));
jest.mock("../../../components/erp/ShippingFilesSection", () => ({
  ShippingFilesSection: () => <div>documentos de envío</div>,
}));
jest.mock("../../../components/erp/OrderFactusolClientPanel", () => ({
  OrderFactusolClientPanel: () => null,
}));
jest.mock("../../../lib/api", () => ({
  getCurrentUser: jest.fn(() => Promise.resolve({ role: "admin" })),
}));
jest.mock("../../../lib/erpApi", () => ({
  ERP_EDIT_ROLES: ["admin", "pedidos"],
  DOMAIN_LABELS: {
    payment: "Pago", preparation: "Preparación", transport: "Transporte", invoice: "Facturación",
  },
  STATUS_LABELS: {},
  FACTUSOL_SERIES: [
    { value: 1, label: "Bomedia" }, { value: 2, label: "MQ Europe" },
    { value: 4, label: "Lambert" }, { value: 5, label: "Streamtec" },
  ],
  factusolSerieLabel: (n: number | null) =>
    (n == null ? "—" : ({ 1: "Bomedia", 2: "MQ Europe", 4: "Lambert", 5: "Streamtec" }[n] ?? String(n))),
  customerLabel: () => "Escola La Muntanyeta",
  resolveOrderCobroStatus: (
    o: { factusol_cobro_status?: string | null },
    live: { status?: string | null } | null | undefined,
  ) => {
    const s = live?.status === "cobrada" || live?.status === "pendiente" ? live.status : null;
    return s ?? o.factusol_cobro_status ?? null;
  },
  getOrder: jest.fn(),
  getOrderTimeline: jest.fn(() => Promise.resolve({ total: 0, items: [] })),
  getFactusolStatus: jest.fn(() => Promise.resolve({ status: "none" })),
  getErpSettings: jest.fn(() => Promise.resolve({ shipping_origins: [] })),
  getOrderFactusolInvoiceRef: jest.fn(),
  getOrderFactusolCobro: jest.fn(() => Promise.resolve({ status: "pendiente", invoice: null })),
  downloadOrderFactusolPedidoPdf: jest.fn(),
  downloadFactusolDocumentPdf: jest.fn(),
  createOrderAlbaran: jest.fn(),
  getQuoteJobStatus: jest.fn(),
  waitForQuoteJob: jest.fn(),
  changeOrderFactusolSerie: jest.fn(),
  fireTransition: jest.fn(),
  saveBlob: jest.fn(),
  updateOrderLanguage: jest.fn(),
  updateOrderSeguimiento: jest.fn(),
  completeOrder: jest.fn(),
  uncompleteOrder: jest.fn(),
  uncancelOrder: jest.fn(),
}));

function detail(over = {}) {
  return {
    id: "o-1", order_number: "MANUAL-000777", contact_name: null,
    company_name: "Escola La Muntanyeta", external_source: "manual",
    store_id: null, contact_id: null, company_id: "c-1", total_amount: 300,
    currency: "EUR", payment_status: "pending", preparation_status: "pending_review",
    transport_status: "not_shipped", invoice_status: "not_invoiced",
    tracking_number: null, factusol_invoice_number: null,
    factusol_albaran_number: "5-500010", factusol_manual_serie: 1,
    factusol_cobro_status: null,
    serial_number: null, whiterip_license: null, shipping_origin: null, language: "es",
    approved_at: null, placed_at: "2026-09-08T10:00:00",
    created_at: "2026-09-08T10:00:00", externally_processed_at: null,
    externally_processed_note: null, externally_processed_by_user_id: null,
    notes: null, packing: null, lines: [],
    status_history: [], exceptions: [], blockers: [],
    available_transitions: { payment: [], invoice: [], preparation: [], transport: [] },
    warnings: [], completed: false, completed_at: null, completed_by_user_id: null,
    completed_by_name: null, cancelled: false, cancelled_at: null, cancelled_reason: null,
    cancelled_by_name: null,
    workflow: {
      queue: "por_revisar", queue_label: "Por revisar",
      next_action: "aprobar", next_action_label: "Aprobar", next_action_hint: "Revisa.",
      blocked: false, regime: "nacional", steps: [], alerts: [],
      company: { id: "c-1", name: "Escola La Muntanyeta", country: "ES",
        factusol_id: "3001", regime: "nacional" },
    },
    ...over,
  };
}

beforeEach(() => {
  (getOrder as jest.Mock).mockReset();
  (getOrder as jest.Mock).mockResolvedValue(detail());
  (changeOrderFactusolSerie as jest.Mock).mockReset();
  (changeOrderFactusolSerie as jest.Mock).mockResolvedValue({
    ...detail(), factusol_serie_job_id: "job-serie", factusol_manual_serie: 2,
  });
  (waitForQuoteJob as jest.Mock).mockReset();
  (waitForQuoteJob as jest.Mock).mockResolvedValue({ status: "finished", result: {} });
});

describe("Ficha · Cambiar serie", () => {
  it("un pedido manual enseña la serie actual y «Cambiar serie»", async () => {
    render(<ErpOrderDetailPage />);
    await screen.findByText("documentos de envío");
    // Serie actual (1 · Bomedia) visible en el panel FACTUSOL.
    expect(screen.getByText("1 · Bomedia")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cambiar serie" })).toBeEnabled();
  });

  it("«Cambiar serie» abre el modal, avisa del albarán y llama al endpoint", async () => {
    const user = userEvent.setup();
    render(<ErpOrderDetailPage />);
    await screen.findByText("documentos de envío");
    await user.click(screen.getByRole("button", { name: "Cambiar serie" }));

    const dialog = await screen.findByRole("dialog", {
      name: "Cambiar serie del pedido MANUAL-000777",
    });
    // Avisa de que borra y recrea el albarán en FACTUSOL.
    expect(dialog).toHaveTextContent("5-500010");
    expect(dialog).toHaveTextContent(/BORRA y lo RE-CREA/);

    await user.selectOptions(screen.getByLabelText("Nueva serie del pedido"), "2");
    await user.click(
      screen.getByRole("button", { name: "Cambiar serie y recrear albarán" }),
    );

    await waitFor(() => expect(changeOrderFactusolSerie).toHaveBeenCalledWith("o-1", 2));
    // Con job (albarán), se espera al worker serial.
    await waitFor(() => expect(waitForQuoteJob).toHaveBeenCalledWith("job-serie"));
  });

  it("con factura emitida «Cambiar serie» está deshabilitado", async () => {
    (getOrder as jest.Mock).mockResolvedValue(detail({
      factusol_invoice_number: "260090", invoice_status: "invoiced_by_erp",
    }));
    render(<ErpOrderDetailPage />);
    await screen.findByText("documentos de envío");
    expect(screen.getByRole("button", { name: "Cambiar serie" })).toBeDisabled();
  });

  it("un pedido web NO ofrece «Cambiar serie»", async () => {
    (getOrder as jest.Mock).mockResolvedValue(detail({
      external_source: "woocommerce", order_number: "FLUXLA-5784",
      factusol_manual_serie: null,
    }));
    render(<ErpOrderDetailPage />);
    await screen.findByText("documentos de envío");
    expect(screen.queryByRole("button", { name: "Cambiar serie" })).toBeNull();
  });
});
