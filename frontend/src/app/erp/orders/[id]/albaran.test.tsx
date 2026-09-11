import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ErpOrderDetailPage from "./page";
import { createOrderAlbaran, getOrder, getQuoteJobStatus } from "../../../lib/erpApi";

jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ children }: { children: React.ReactNode }) => <span>{children}</span>,
}));
jest.mock("next/navigation", () => ({
  useParams: () => ({ id: "o-1" }),
}));
jest.mock("../../../components/PageHeader", () => ({
  PageHeader: ({ title }: { title: string }) => <h1>{title}</h1>,
}));
jest.mock("../../../components/erp/EmbalarModal", () => ({ EmbalarModal: () => null }));
jest.mock("../../../components/erp/FactusolDocumentDetailModal", () => ({
  PDF_LANGS: [{ value: "es", label: "Español" }],
}));
jest.mock("../../../components/erp/InvoiceEmailModal", () => ({ InvoiceEmailModal: () => null }));
jest.mock("../../../components/erp/EmitFactusolButton", () => ({
  EmitFactusolButton: () => <span>emitir</span>,
}));
jest.mock("../../../components/erp/OrderStatusMachine", () => ({
  OrderStatusMachine: () => null,
}));
jest.mock("../../../components/erp/ShippingFilesSection", () => ({
  ShippingFilesSection: () => null,
}));
jest.mock("../../../lib/api", () => ({
  getCurrentUser: jest.fn(() => Promise.resolve({ role: "admin" })),
}));
jest.mock("../../../lib/erpApi", () => ({
  ERP_EDIT_ROLES: ["admin", "pedidos"],
  customerLabel: () => "Duplicoder",
  getOrder: jest.fn(),
  getOrderTimeline: jest.fn(() => Promise.resolve({ total: 0, items: [] })),
  getFactusolStatus: jest.fn(() => Promise.resolve({ status: "pending" })),
  getErpSettings: jest.fn(() => Promise.resolve({ shipping_origins: [] })),
  getOrderFactusolInvoiceRef: jest.fn(),
  downloadOrderFactusolPedidoPdf: jest.fn(),
  fireTransition: jest.fn(),
  saveBlob: jest.fn(),
  updateOrderLanguage: jest.fn(),
  updateOrderSeguimiento: jest.fn(),
  completeOrder: jest.fn(),
  uncompleteOrder: jest.fn(),
  createOrderAlbaran: jest.fn(),
  getQuoteJobStatus: jest.fn(),
}));

function detail(over = {}) {
  return {
    id: "o-1", order_number: "PRO-000027", contact_name: null, company_name: "Duplicoder",
    external_source: "factusol_proforma", store_id: null, contact_id: null, company_id: "c-1",
    total_amount: 186.34, currency: "EUR", payment_status: "pending",
    preparation_status: "pending_review", transport_status: "not_shipped",
    invoice_status: "not_invoiced", tracking_number: null, factusol_invoice_number: null,
    factusol_albaran_number: null, factusol_payment: null,
    serial_number: null, whiterip_license: null, shipping_origin: null, language: "es",
    approved_at: null, placed_at: "2026-09-01T10:00:00", created_at: "2026-09-01T10:00:00",
    externally_processed_at: null, externally_processed_note: null,
    externally_processed_by_user_id: null, notes: null, packing: null, lines: [],
    status_history: [], exceptions: [], available_transitions: {}, blockers: [], warnings: [],
    completed: false, completed_at: null, completed_by_user_id: null, completed_by_name: null,
    ...over,
  };
}

const PAGADO = {
  paid: true, forma_pago: "002", forma_pago_nombre: "Transferencia",
  contrapartida: "8", contrapartida_nombre: "Streamtec Sabadell", fecha: "2026-09-11",
  recorded_at: "2026-09-11T10:00:00", cobro: null,
};

beforeEach(() => {
  (getOrder as jest.Mock).mockReset();
  (createOrderAlbaran as jest.Mock).mockReset();
  (getQuoteJobStatus as jest.Mock).mockReset();
  window.history.replaceState({}, "", "/erp/orders/o-1");
});

describe("ERP · Ficha del pedido — albarán FACTUSOL y pago (Fase 2)", () => {
  it("muestra el albarán creado y el pago apuntado con el cobro pendiente de factura", async () => {
    (getOrder as jest.Mock).mockResolvedValue(detail({
      factusol_albaran_number: "5-500008", factusol_payment: PAGADO, payment_status: "paid",
    }));
    render(<ErpOrderDetailPage />);
    expect(await screen.findByText("Albarán FACTUSOL 5-500008")).toBeInTheDocument();
    expect(screen.getByText("Pagado (apuntado)")).toBeInTheDocument();
    expect(screen.getByText(/cuenta Streamtec Sabadell · fecha 2026-09-11/)).toBeInTheDocument();
    expect(screen.getByText(/pendiente de factura/)).toBeInTheDocument();
    expect(screen.getByText(/No se ha emitido ninguna factura/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Crear albarán en FACTUSOL" })).not.toBeInTheDocument();
  });

  it("con el cobro ya registrado lo dice; «sin pago» queda pendiente", async () => {
    (getOrder as jest.Mock).mockResolvedValueOnce(detail({
      factusol_albaran_number: "5-500008", factusol_invoice_number: "1",
      factusol_payment: {
        ...PAGADO,
        cobro: { registered: true, status: "registered", numero: "5-000001", linlco: 1,
                 importe: 186.34, fecha: "2026-09-11", motivo: null, at: null },
      },
    }));
    const { unmount } = render(<ErpOrderDetailPage />);
    expect(await screen.findByText(/Cobro registrado en FACTUSOL para la factura 5-000001/))
      .toBeInTheDocument();
    unmount();
    (getOrder as jest.Mock).mockResolvedValueOnce(detail({
      factusol_albaran_number: "5-500008",
      factusol_payment: { ...PAGADO, paid: false, contrapartida: null, contrapartida_nombre: null },
    }));
    render(<ErpOrderDetailPage />);
    expect(await screen.findByText("Sin pago")).toBeInTheDocument();
    expect(screen.getByText(/forma de pago Transferencia/)).toBeInTheDocument();
    expect(screen.getByText(/No se registra ningún cobro/)).toBeInTheDocument();
  });

  it("sin albarán ofrece crearlo: encola, hace polling del job y recarga con el nº", async () => {
    (getOrder as jest.Mock)
      .mockResolvedValueOnce(detail())
      .mockResolvedValue(detail({ factusol_albaran_number: "5-500009" }));
    (createOrderAlbaran as jest.Mock).mockResolvedValue({ job_id: "job-7", order_id: "o-1", status: "queued" });
    (getQuoteJobStatus as jest.Mock).mockResolvedValue({
      status: "finished", result: { numero: "5-500009", status: "created" },
    });
    const user = userEvent.setup();
    render(<ErpOrderDetailPage />);
    expect(await screen.findByText("Sin albarán en FACTUSOL")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Crear albarán en FACTUSOL" }));
    await waitFor(() => expect(createOrderAlbaran).toHaveBeenCalledWith("o-1"));
    await waitFor(() => expect(getQuoteJobStatus).toHaveBeenCalledWith("job-7"));
    expect(await screen.findByText("Albarán FACTUSOL 5-500009 creado.")).toBeInTheDocument();
    expect(await screen.findByText("Albarán FACTUSOL 5-500009")).toBeInTheDocument();
  });

  it("al llegar del alta con ?albaran_job= hace polling y enseña el error si el job falla", async () => {
    window.history.replaceState({}, "", "/erp/orders/o-1?albaran_job=job-9");
    (getOrder as jest.Mock).mockResolvedValue(detail());
    (getQuoteJobStatus as jest.Mock).mockResolvedValue({
      status: "failed", error: "El esquema real de F_ALB/F_LAL no cuadra",
    });
    render(<ErpOrderDetailPage />);
    await waitFor(() => expect(getQuoteJobStatus).toHaveBeenCalledWith("job-9"));
    expect(await screen.findByText(/El albarán no se creó en FACTUSOL: El esquema real/))
      .toBeInTheDocument();
  });

  it("un pedido web no tiene tarjeta de albarán (lo crea WooCommerce)", async () => {
    (getOrder as jest.Mock).mockResolvedValue(detail({
      external_source: "woocommerce", order_number: "BOPRIN-99917",
    }));
    render(<ErpOrderDetailPage />);
    expect(await screen.findByText("Pedido BOPRIN-99917")).toBeInTheDocument();
    expect(screen.queryByText("Albarán y pago FACTUSOL")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Crear albarán en FACTUSOL" })).not.toBeInTheDocument();
  });
});
