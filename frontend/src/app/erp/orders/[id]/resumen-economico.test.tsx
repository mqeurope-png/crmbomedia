import { render, screen, within } from "@testing-library/react";
import ErpOrderDetailPage from "./page";
import { getOrder } from "../../../lib/erpApi";

/** Resumen económico de un pedido web: envío y comisiones VISIBLES, IVA real
 *  (nunca fabricado), y el total cuadra leyendo los componentes — caso
 *  ARTISJ-9552. El régimen exento sigue mandando (IVA 0). */

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
  DOMAIN_LABELS: { payment: "Pago", preparation: "Preparación", transport: "Transporte", invoice: "Facturación" },
  STATUS_LABELS: {},
  REGIME_TEXT: { nacional: "nacional", intracomunitario: "intracomunitario", exportacion: "exportación" },
  customerLabel: () => "Artisjet Cliente",
  resolveOrderCobroStatus: () => null,
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
  fireTransition: jest.fn(),
  saveBlob: jest.fn(),
  updateOrderLanguage: jest.fn(),
  updateOrderSeguimiento: jest.fn(),
  completeOrder: jest.fn(),
  uncompleteOrder: jest.fn(),
  uncancelOrder: jest.fn(),
}));

function line(over = {}) {
  return {
    id: "l", position: 0, product_sku: "", product_codart: null,
    description: "", quantity: 1, unit_price: 0, tax_rate: 0, line_total: 0,
    notes: null, is_shipping: false, line_kind: null, ...over,
  };
}

function detail(over = {}) {
  return {
    id: "o-1", order_number: "ARTISJ-9552", contact_name: null,
    company_name: "Artisjet Cliente", external_source: "woocommerce",
    store_id: "st-1", contact_id: null, company_id: "c-1", total_amount: 113.36,
    currency: "EUR", payment_status: "paid", preparation_status: "in_queue",
    transport_status: "not_shipped", invoice_status: "not_invoiced",
    tracking_number: null, factusol_invoice_number: null,
    factusol_albaran_number: null, factusol_cobro_status: null,
    serial_number: null, whiterip_license: null, shipping_origin: null, language: "es",
    approved_at: null, placed_at: "2026-09-10T10:00:00",
    created_at: "2026-09-10T10:00:00", externally_processed_at: null,
    externally_processed_note: null, externally_processed_by_user_id: null,
    notes: null, packing: null,
    lines: [
      line({ id: "p", position: 0, product_sku: "4785", description: "Encoder Strip Sensor",
             unit_price: 90, tax_rate: 0, line_total: 90 }),
      line({ id: "s", position: 1, product_sku: "woo-shipping-2", description: "Envío: Flexible Shipping",
             unit_price: 19, tax_rate: 0, line_total: 19, is_shipping: true, line_kind: "shipping" }),
      line({ id: "f", position: 2, product_sku: "woo-fee-3", description: "PayPal cost 4%",
             unit_price: 4.36, tax_rate: 0, line_total: 4.36, line_kind: "fee" }),
    ],
    status_history: [], exceptions: [], blockers: [],
    available_transitions: { payment: [], invoice: [], preparation: [], transport: [] },
    warnings: [], completed: false, completed_at: null, completed_by_user_id: null,
    completed_by_name: null, cancelled: false, cancelled_at: null, cancelled_reason: null,
    cancelled_by_name: null,
    workflow: {
      queue: "por_facturar", queue_label: "Por facturar",
      next_action: "emitir", next_action_label: "Emitir factura", next_action_hint: "",
      blocked: false, regime: "nacional", steps: [], alerts: [],
      company: { id: "c-1", name: "Artisjet Cliente", country: "ES", factusol_id: "3001", regime: "nacional" },
    },
    ...over,
  };
}

beforeEach(() => {
  (getOrder as jest.Mock).mockReset();
  (getOrder as jest.Mock).mockResolvedValue(detail());
});

describe("Resumen económico · pedido web", () => {
  it("ARTISJ-9552: envío y comisión visibles, IVA 0, total cuadra sin ajuste", async () => {
    render(<ErpOrderDetailPage />);
    const eco = within(await screen.findByRole("region", { name: "Resumen económico" }));
    expect(eco.getByText("Base imponible").nextSibling).toHaveTextContent("90.00 EUR");
    expect(eco.getByText("Envío").nextSibling).toHaveTextContent("19.00 EUR");
    expect(eco.getByText("Comisiones y otros cargos").nextSibling).toHaveTextContent("4.36 EUR");
    expect(eco.getByText(/^IVA/).nextSibling).toHaveTextContent("0.00 EUR");
    expect(eco.getByText("Total").nextSibling).toHaveTextContent("113.36 EUR");
    // Cuadra: no hay línea de «Ajuste» (antes salía «Portes y otros cargos»
    // inventado como resto).
    expect(eco.queryByText("Ajuste")).toBeNull();
  });

  it("régimen intracomunitario: IVA exento aunque las líneas trajeran impuesto", async () => {
    (getOrder as jest.Mock).mockResolvedValue(detail({
      total_amount: 121,
      lines: [
        line({ id: "p", product_sku: "P", description: "Art", unit_price: 100, tax_rate: 21, line_total: 100 }),
      ],
      workflow: { ...detail().workflow, regime: "intracomunitario" },
    }));
    render(<ErpOrderDetailPage />);
    const eco = within(await screen.findByRole("region", { name: "Resumen económico" }));
    expect(eco.getByText(/^IVA/).nextSibling).toHaveTextContent("0.00 EUR · exento");
  });
});
