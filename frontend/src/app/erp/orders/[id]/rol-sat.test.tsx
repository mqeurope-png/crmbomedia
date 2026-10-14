import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ErpOrderDetailPage from "./page";
import { getFactusolStatus, getOrder, getOrderFactusolCobro } from "../../../lib/erpApi";

/** El rol SAT (taller, 14/10/2026) en la FICHA del pedido.
 *
 *  - En un pedido NORMAL mira y no actúa: ni cobrar, ni facturar, ni correos al
 *    cliente, ni completar o anular, ni el PDF de la factura. Lo único que
 *    queda es «Enviar a SAT» (enviar al taller por email, confirmado por Bart).
 *  - Su MUESTRA la gestiona: completarla y anularla.
 *  - Sin `erp.office.view` ni siquiera pide el cobro ni el estado FACTUSOL.
 *  El usuario es `sat` a secas (sin roles ERP adicionales). */

jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ children, href, className }: {
    children: React.ReactNode; href: string; className?: string;
  }) => <a href={href} className={className}>{children}</a>,
}));
jest.mock("next/navigation", () => ({
  useParams: () => ({ id: "o-1" }),
  useSearchParams: () => new URLSearchParams(""),
}));
jest.mock("../../../components/PageHeader", () => ({
  PageHeader: ({ title, subtitle, actions }: {
    title: string; subtitle?: React.ReactNode; actions?: React.ReactNode;
  }) => (
    <><h1>{title}</h1>{subtitle ? <p data-testid="header-subtitle">{subtitle}</p> : null}{actions}</>
  ),
}));
jest.mock("../../../components/erp/EmbalarModal", () => ({ EmbalarModal: () => null }));
jest.mock("../../../components/erp/GeneiShipmentSection", () => ({
  GeneiShipmentSection: ({ canNotify }: { canNotify?: boolean }) => (
    canNotify ? <button type="button">Enviar aviso al cliente</button> : null
  ),
}));
jest.mock("../../../components/erp/FactusolDocumentDetailModal", () => ({
  PDF_LANGS: [{ value: "es", label: "Español" }],
}));
jest.mock("../../../components/erp/InvoiceEmailModal", () => ({ InvoiceEmailModal: () => null }));
jest.mock("../../../components/erp/OrderEmailModal", () => ({ OrderEmailModal: () => null }));
jest.mock("../../../components/erp/EmitFactusolButton", () => ({
  EmitFactusolButton: () => <button type="button">Emitir factura FACTUSOL</button>,
}));
jest.mock("../../../components/erp/ShippingFilesSection", () => ({
  ShippingFilesSection: () => null,
}));
jest.mock("../../../components/erp/OrderFactusolClientPanel", () => ({
  OrderFactusolClientPanel: () => <div>panel cliente FACTUSOL</div>,
}));
jest.mock("../../../lib/api", () => ({
  getCurrentUser: jest.fn(() => Promise.resolve({ role: "sat", erp_roles: [] })),
}));
jest.mock("../../../lib/erpApi", () => ({
  DOMAIN_LABELS: {
    payment: "Pago", preparation: "Preparación", transport: "Transporte", invoice: "Facturación",
  },
  STATUS_LABELS: {},
  customerHeadline: () => "Duplicoder",
  customerLabel: () => "Duplicoder",
  resolveOrderCobroStatus: (o: { factusol_cobro_status?: string | null }) => o.factusol_cobro_status ?? null,
  getOrder: jest.fn(),
  getOrderTimeline: jest.fn(() => Promise.resolve({ total: 0, items: [] })),
  getFactusolStatus: jest.fn(() => Promise.resolve({ status: "invoiced" })),
  getErpSettings: jest.fn(() => Promise.resolve({ shipping_origins: [] })),
  getOrderFactusolInvoiceRef: jest.fn(),
  getOrderFactusolCobro: jest.fn(() => Promise.resolve({ status: "pendiente", invoice: null })),
  getOrderShipment: jest.fn(() => Promise.resolve({ kind: "genei", transport_status: "not_shipped" })),
  downloadOrderFactusolPedidoPdf: jest.fn(),
  fireTransition: jest.fn(),
  saveBlob: jest.fn(),
  updateOrderLanguage: jest.fn(),
  updateOrderSeguimiento: jest.fn(),
  completeOrder: jest.fn(),
  uncompleteOrder: jest.fn(),
}));

const STEPS = [
  { key: "creado", label: "Creado", state: "done", detail: null },
  { key: "factura", label: "Factura", state: "done", detail: "5-260100" },
  { key: "cobro", label: "Cobro", state: "now", detail: null },
  { key: "factura_enviada", label: "Factura enviada", state: "pending", detail: null, optional: true },
];

function detail(over: Record<string, unknown> = {}) {
  return {
    id: "o-1", order_number: "MAN-000100", contact_name: null, company_name: "Duplicoder",
    external_source: "woocommerce", store_id: null, contact_id: null, company_id: "c-1",
    total_amount: 121, currency: "EUR", payment_status: "pending",
    preparation_status: "in_queue", transport_status: "not_shipped",
    invoice_status: "generated", tracking_number: null, factusol_invoice_number: "5-260100",
    factusol_cobro_status: "pendiente", factusol_albaran_number: null,
    serial_number: null, whiterip_license: null, shipping_origin: null, language: "es",
    approved_at: "2026-10-01T10:00:00", placed_at: "2026-10-01T10:00:00",
    created_at: "2026-10-01T10:00:00", externally_processed_at: null,
    externally_processed_note: null, externally_processed_by_user_id: null, notes: null,
    packing: null, lines: [], status_history: [], exceptions: [], blockers: [], warnings: [],
    available_transitions: { payment: [], invoice: [], preparation: [], transport: [] },
    completed: false, completed_at: null, completed_by_user_id: null, completed_by_name: null,
    order_kind: null,
    workflow: {
      queue: "por_cobrar", queue_label: "Por cobrar",
      next_action: "registrar_cobro", next_action_label: "Registrar cobro",
      next_action_hint: "Registra el cobro de la factura en FACTUSOL.",
      blocked: false, regime: "nacional", steps: STEPS, alerts: [],
      company: { id: "c-1", name: "Duplicoder", country: "ES", factusol_id: "430", regime: "nacional" },
    },
    ...over,
  };
}

function muestra() {
  return detail({
    order_number: "MUESTRA-000007", order_kind: "sample", external_source: "manual",
    company_id: null, invoice_status: "not_invoiced", factusol_invoice_number: null,
    factusol_cobro_status: null,
    workflow: {
      ...detail().workflow, queue: "por_enviar", queue_label: "Por enviar",
      next_action: "marcar_completado", next_action_label: "Marcar completado",
      next_action_hint: "Muestra: prepárala y envíala desde el taller.", company: null,
      steps: [
        { key: "creado", label: "Creado", state: "done", detail: null },
        { key: "cobro", label: "Cobro", state: "skipped", detail: "no facturable" },
        { key: "envio", label: "Envío", state: "now", detail: null },
      ],
    },
  });
}

const OFICINA = [
  "Registrar cobro", "Registrar cobro en FACTUSOL", "Emitir factura FACTUSOL",
  "Enviar factura al cliente", "PDF de la factura", "Enviar aviso al cliente",
  "Marcar completado",
];

beforeEach(() => {
  (getOrder as jest.Mock).mockReset();
  (getFactusolStatus as jest.Mock).mockClear();
  (getOrderFactusolCobro as jest.Mock).mockClear();
});

describe("Ficha del pedido · rol SAT", () => {
  it("pedido normal: ve la ficha pero no hay botones de cobrar, facturar ni correo al cliente", async () => {
    (getOrder as jest.Mock).mockResolvedValue(detail());
    render(<ErpOrderDetailPage />);
    await screen.findByRole("heading", { name: "Pedido MAN-000100" });
    // Lo que sí: «Enviar a SAT» (enviar al taller por email).
    expect(await screen.findByRole("button", { name: "Enviar a SAT" })).toBeInTheDocument();
    for (const nombre of OFICINA) {
      expect(screen.queryByRole("button", { name: nombre })).not.toBeInTheDocument();
    }
    expect(screen.queryByRole("button", { name: /Más acciones del pedido/ })).not.toBeInTheDocument();
    // Ni el panel de cliente FACTUSOL ni el enlace a la ficha de la empresa.
    expect(screen.queryByText("panel cliente FACTUSOL")).not.toBeInTheDocument();
    expect(screen.getByTestId("header-subtitle").querySelector("a")).toBeNull();
    // Y no pide datos de oficina (le darían 403).
    await waitFor(() => expect(getOrder).toHaveBeenCalled());
    expect(getFactusolStatus).not.toHaveBeenCalled();
    expect(getOrderFactusolCobro).not.toHaveBeenCalled();
  });

  it("su muestra: la completa y la puede anular; el cobro sale «No aplica»", async () => {
    (getOrder as jest.Mock).mockResolvedValue(muestra());
    render(<ErpOrderDetailPage />);
    await screen.findByRole("heading", { name: "Pedido MUESTRA-000007" });
    expect(await screen.findByRole("button", { name: "Marcar completado" })).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: /Más acciones del pedido/ }));
    expect(await screen.findByRole("button", { name: /Anular pedido/ })).toBeInTheDocument();
    // Lo de oficina, tampoco en una muestra.
    expect(screen.queryByLabelText("Idioma del pedido")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Vincular documento FACTUSOL" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Enviar a SAT" })).not.toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Pendiente de cobro" })).toHaveTextContent("No aplica");
  });

  it("la oficina (ERP · Pedidos) conserva sus botones en el mismo pedido", async () => {
    const { getCurrentUser } = jest.requireMock("../../../lib/api");
    (getCurrentUser as jest.Mock).mockResolvedValueOnce({ role: "pedidos" });
    (getOrder as jest.Mock).mockResolvedValue(detail());
    render(<ErpOrderDetailPage />);
    expect(await screen.findByRole("button", { name: "Registrar cobro" })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Enviar factura al cliente" }).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "PDF de la factura" })).toBeInTheDocument();
    await waitFor(() => expect(getFactusolStatus).toHaveBeenCalledWith("o-1"));
  });
});
