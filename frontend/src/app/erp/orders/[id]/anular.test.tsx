import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ErpOrderDetailPage from "./page";
import { getOrder, uncancelOrder } from "../../../lib/erpApi";

/** Lote ERP · «Anular pedido» desde la ficha: solo pedidos manuales /
 *  FACTUSOL (los web se anulan en WooCommerce), con modal de aviso previo;
 *  el anulado enseña su banner y ofrece «Restaurar». Distinto de «quitar». */

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
jest.mock("../../../components/erp/CancelOrderModal", () => ({
  CancelOrderModal: ({ orderNumber }: { orderNumber: string }) => (
    <div role="dialog" aria-label={`Anular pedido ${orderNumber}`}>modal anular {orderNumber}</div>
  ),
}));
jest.mock("../../../components/erp/EmitFactusolButton", () => ({
  EmitFactusolButton: () => null,
}));
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
    factusol_albaran_number: "5-500010", factusol_cobro_status: null,
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
  (uncancelOrder as jest.Mock).mockReset();
});

describe("Ficha · Anular pedido", () => {
  it("un pedido manual ofrece «Anular pedido» en «⋯» y abre el modal de aviso", async () => {
    const user = userEvent.setup();
    render(<ErpOrderDetailPage />);
    await screen.findByText("documentos de envío");
    await user.click(screen.getByRole("button", { name: "Más acciones del pedido" }));
    await user.click(screen.getByRole("button", { name: "Anular pedido" }));
    expect(await screen.findByRole("dialog", { name: "Anular pedido MANUAL-000777" }))
      .toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Restaurar pedido" })).toBeNull();
  });

  it("un pedido web TAMBIÉN ofrece «Anular pedido» (Parte A: antes se bloqueaba)", async () => {
    (getOrder as jest.Mock).mockResolvedValue(detail({
      external_source: "woocommerce", order_number: "FLUXLA-5749",
    }));
    const user = userEvent.setup();
    render(<ErpOrderDetailPage />);
    await screen.findByText("documentos de envío");
    await user.click(screen.getByRole("button", { name: "Más acciones del pedido" }));
    await user.click(screen.getByRole("button", { name: "Anular pedido" }));
    expect(await screen.findByRole("dialog", { name: "Anular pedido FLUXLA-5749" }))
      .toBeInTheDocument();
  });

  it("el anulado enseña el banner con quién/motivo y «Restaurar pedido» revierte", async () => {
    (getOrder as jest.Mock).mockResolvedValue(detail({
      cancelled: true, cancelled_at: "2026-09-15T10:00:00", cancelled_reason: "se echa atrás",
      cancelled_by_name: "Bart",
      workflow: { ...detail().workflow, queue: "listo", queue_label: "Listo",
        next_action: "ninguna", next_action_label: "ninguna", next_action_hint: "Pedido anulado." },
    }));
    (uncancelOrder as jest.Mock).mockResolvedValue(detail());
    const user = userEvent.setup();
    render(<ErpOrderDetailPage />);
    const banner = await screen.findByText(/Pedido anulado el/);
    expect(banner).toHaveTextContent("por Bart");
    expect(banner).toHaveTextContent("se echa atrás");
    await user.click(screen.getByRole("button", { name: "Más acciones del pedido" }));
    expect(screen.queryByRole("button", { name: "Anular pedido" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Restaurar pedido" }));
    await waitFor(() => expect(uncancelOrder).toHaveBeenCalledWith("o-1"));
  });

  it("un pedido web reembolsado se dice «Reembolsado», no «anulado»", async () => {
    // Mismo sello técnico que la anulación (`cancelled`), estado PROPIO: se
    // llama por lo que es y el Seguimiento lo sigue enseñando.
    (getOrder as jest.Mock).mockResolvedValue(detail({
      external_source: "woocommerce", order_number: "ARTISJ-9557",
      cancelled: true, refunded: true, cancelled_at: "2026-09-15T10:00:00",
      cancelled_reason: "Reembolsado en WooCommerce (reembolso total)",
      workflow: { ...detail().workflow, queue: "listo", queue_label: "Listo",
        next_action: "ninguna", next_action_label: "ninguna",
        next_action_hint: "Pedido reembolsado." },
    }));
    render(<ErpOrderDetailPage />);
    const banner = await screen.findByText(/Pedido reembolsado el/);
    expect(banner).toHaveTextContent("Reembolsado");
    expect(banner).toHaveTextContent("en Seguimiento se sigue viendo");
    expect(screen.queryByText(/Pedido anulado el/)).toBeNull();
  });
});
