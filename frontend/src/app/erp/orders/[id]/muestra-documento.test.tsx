import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ErpOrderDetailPage from "./page";
import { getOrder } from "../../../lib/erpApi";

/** Rev. 30/09/2026 — MUESTRA-000003 ↔ 2-526110: la ficha de una muestra ofrece
 *  «Vincular documento FACTUSOL» (y «Reprocesar vínculo» si ya apunta a uno sin
 *  haber cargado sus datos), el menú «⋯» con «Anular pedido» (antes quedaba
 *  atascada), el badge «muestra», y «Desvincular» en cada documento vinculado. */

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
jest.mock("../../../components/erp/VincularDocumentoModal", () => ({
  VincularDocumentoModal: ({ orderNumber, initial }: {
    orderNumber: string; initial?: { doc_type: string; serie: number; codigo: number } | null;
  }) => (
    <div role="dialog" aria-label={`Vincular documento FACTUSOL a ${orderNumber}`}>
      {initial ? `preseleccionado ${initial.doc_type} ${initial.serie}-${initial.codigo}` : "sin preselección"}
    </div>
  ),
}));
jest.mock("../../../components/erp/DesvincularDocumentoModal", () => ({
  DesvincularDocumentoModal: ({ document, bornAsSample }: {
    document: { label: string }; bornAsSample: boolean;
  }) => (
    <div role="dialog" aria-label={`Desvincular ${document.label}`}>
      {bornAsSample ? "nació como muestra" : "pedido normal"}
    </div>
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

const MUESTRA = {
  order_number: "MUESTRA-000003", external_source: "manual", order_kind: "sample",
  born_as_sample: true, total_amount: 0, company_id: null, company_name: null,
  factusol_albaran_number: null, linked_documents: [], sample_pending_link: null,
  workflow: {
    queue: "por_enviar", queue_label: "Por enviar",
    next_action: "marcar_completado", next_action_label: "Marcar completado",
    next_action_hint: "Muestra.", blocked: false, regime: null, steps: [], alerts: [],
    company: null,
  },
};
const FACTURA = {
  kind: "factura", doc_type: "facturas", serie: 2, codigo: 526110,
  numero: "2-526110", label: "factura 2-526110",
};

beforeEach(() => {
  (getOrder as jest.Mock).mockReset();
});

describe("Ficha · muestra con documento FACTUSOL", () => {
  it("muestra pura: badge, «Vincular documento FACTUSOL» y «Anular pedido» en «⋯»", async () => {
    (getOrder as jest.Mock).mockResolvedValue(detail(MUESTRA));
    const user = userEvent.setup();
    render(<ErpOrderDetailPage />);
    expect(await screen.findByText("Muestra · no facturable")).toBeInTheDocument();
    const aviso = screen.getByRole("note", { name: "Muestra y documentos FACTUSOL" });
    await user.click(within(aviso).getByRole("button", { name: "Vincular documento FACTUSOL" }));
    expect(await screen.findByRole("dialog", {
      name: "Vincular documento FACTUSOL a MUESTRA-000003",
    })).toHaveTextContent("sin preselección");
    await user.click(screen.getByRole("button", { name: "Más acciones del pedido" }));
    await user.click(screen.getByRole("button", { name: "Anular pedido" }));
    expect(await screen.findByRole("dialog", { name: "Anular pedido MUESTRA-000003" }))
      .toBeInTheDocument();
  });

  it("muestra con factura apuntada sin datos: «Reprocesar vínculo» la preselecciona", async () => {
    (getOrder as jest.Mock).mockResolvedValue(detail({
      ...MUESTRA, factusol_invoice_number: "526110", invoice_status: "invoiced_by_erp",
      linked_documents: [FACTURA], sample_pending_link: FACTURA,
    }));
    const user = userEvent.setup();
    render(<ErpOrderDetailPage />);
    const aviso = await screen.findByRole("note", { name: "Muestra y documentos FACTUSOL" });
    expect(aviso).toHaveTextContent("apunta a factura 2-526110 pero no cargó sus datos");
    await user.click(within(aviso).getByRole("button", { name: "Reprocesar vínculo" }));
    expect(await screen.findByRole("dialog", {
      name: "Vincular documento FACTUSOL a MUESTRA-000003",
    })).toHaveTextContent("preseleccionado facturas 2-526110");
  });

  it("muestra convertida: badge «Muestra» y «Desvincular» de su factura", async () => {
    (getOrder as jest.Mock).mockResolvedValue(detail({
      ...MUESTRA, order_kind: "sample_converted", external_source: "factusol_factura",
      total_amount: 5059, company_id: "premo", company_name: "PREMO B.V.",
      factusol_invoice_number: "526110", factusol_invoice_serie: 2,
      invoice_status: "invoiced_by_erp", linked_documents: [FACTURA],
    }));
    const user = userEvent.setup();
    render(<ErpOrderDetailPage />);
    const badge = await screen.findByText("Muestra", { selector: ".erp-badge-muestra" });
    expect(badge).toHaveAttribute("title", "Nació como muestra; tiene un documento FACTUSOL vinculado");
    expect(screen.queryByText("Muestra · no facturable")).toBeNull();
    expect(screen.queryByRole("note", { name: "Muestra y documentos FACTUSOL" })).toBeNull();
    const vinculados = screen.getByRole("list", { name: "Documentos vinculados" });
    await user.click(within(vinculados).getByRole("button", { name: "Desvincular factura 2-526110" }));
    expect(await screen.findByRole("dialog", { name: "Desvincular factura 2-526110" }))
      .toHaveTextContent("nació como muestra");
  });
});
