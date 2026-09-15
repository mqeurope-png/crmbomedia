import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ErpOrderDetailPage from "./page";
import { createOrderAlbaran, getOrder, getQuoteJobStatus } from "../../../lib/erpApi";

/** ERP · Ficha del pedido — el albarán FACTUSOL vive en UN solo sitio
 *  («Documentos de envío»): nº + PDF, o «Crear albarán en FACTUSOL» si falta
 *  (con el polling del job del worker serie). El pago apuntado al convertir
 *  (Fase 2, opción B) se ve en el resumen económico; el cobro es manual. */

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
jest.mock("../../../components/erp/EmitFactusolButton", () => ({
  EmitFactusolButton: () => <span>emitir</span>,
}));
jest.mock("../../../components/erp/OrderStatusMachine", () => ({
  OrderStatusMachine: () => null,
}));
jest.mock("../../../components/erp/FactusolAlbaranPdfButton", () => ({
  FactusolAlbaranPdfButton: ({ numero }: { numero: string }) => (
    <button type="button">PDF del albarán (FACTUSOL)<span hidden>{numero}</span></button>
  ),
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
  getOrderFactusolCobro: jest.fn(() => Promise.resolve({ status: "sin_factura", invoice: null })),
  downloadOrderFactusolPedidoPdf: jest.fn(),
  fireTransition: jest.fn(),
  saveBlob: jest.fn(),
  updateOrderLanguage: jest.fn(),
  updateOrderSeguimiento: jest.fn(),
  completeOrder: jest.fn(),
  uncompleteOrder: jest.fn(),
  createOrderAlbaran: jest.fn(),
  getQuoteJobStatus: jest.fn(),
  downloadOrderFactusolAlbaranPdf: jest.fn(),
  // «Documentos de envío» real: ficheros subidos a mano / de Woo.
  listShippingFiles: jest.fn(() => Promise.resolve([])),
  uploadShippingFile: jest.fn(),
  fetchAlbaranFromWoo: jest.fn(),
  openShippingFile: jest.fn(),
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

describe("ERP · Ficha del pedido — albarán FACTUSOL en «Documentos de envío» y pago apuntado", () => {
  it("test_ficha_no_duplica_albaran_ni_acciones: el albarán sale UNA vez (con su PDF) y el pago apuntado en el resumen", async () => {
    (getOrder as jest.Mock).mockResolvedValue(detail({
      factusol_albaran_number: "5-500008", factusol_payment: PAGADO, payment_status: "paid",
    }));
    render(<ErpOrderDetailPage />);
    expect(await screen.findByText("Albarán FACTUSOL 5-500008")).toBeInTheDocument();
    // Una sola vez: en «Documentos de envío». La tarjeta duplicada ya no existe.
    expect(screen.getAllByText("Albarán FACTUSOL 5-500008")).toHaveLength(1);
    expect(screen.queryByText("Albarán y pago FACTUSOL")).not.toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "PDF del albarán (FACTUSOL)" })).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "Crear albarán en FACTUSOL" })).not.toBeInTheDocument();
    // El bloque FACTUSOL de arriba solo informa del nº (no repite el PDF).
    expect(screen.getByRole("region", { name: "FACTUSOL" })).toHaveTextContent("5-500008");
    // El pago apuntado (forma de pago + cuenta) está en el resumen económico.
    const resumen = screen.getByRole("region", { name: "Resumen económico" });
    expect(resumen).toHaveTextContent("Transferencia · Streamtec Sabadell");
    expect(resumen).toHaveTextContent("Pagado (apuntado)");
    expect(resumen).toHaveTextContent("fecha 2026-09-11");
    // Y las acciones de cabecera no están repetidas abajo.
    expect(screen.getAllByRole("button", { name: /PDF del pedido \(FACTUSOL\)/ })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: "Marcar completado" })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: "Enviar por email" })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: /Registrar cobro/ })).toHaveLength(1);
  });

  it("sin albarán ofrece crearlo en «Documentos de envío»: encola, hace polling del job y recarga con el nº", async () => {
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
    expect(screen.queryByRole("button", { name: "PDF del albarán (FACTUSOL)" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Crear albarán en FACTUSOL" }));
    await waitFor(() => expect(createOrderAlbaran).toHaveBeenCalledWith("o-1"));
    await waitFor(() => expect(getQuoteJobStatus).toHaveBeenCalledWith("job-7"));
    expect(await screen.findByText("Albarán FACTUSOL 5-500009 creado.")).toBeInTheDocument();
    expect(await screen.findByText("Albarán FACTUSOL 5-500009")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "PDF del albarán (FACTUSOL)" })).toBeInTheDocument();
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

  it("un pedido web no ofrece crear albarán (lo crea WooCommerce)", async () => {
    (getOrder as jest.Mock).mockResolvedValue(detail({
      external_source: "woocommerce", order_number: "BOPRIN-99917",
    }));
    render(<ErpOrderDetailPage />);
    expect(await screen.findByText("Pedido BOPRIN-99917")).toBeInTheDocument();
    expect(await screen.findByRole("region", { name: "Documentos de envío" }))
      .toHaveTextContent(/lo crea WooCommerce/);
    expect(screen.queryByRole("button", { name: "Crear albarán en FACTUSOL" })).not.toBeInTheDocument();
    expect(screen.queryByText("Sin albarán en FACTUSOL")).not.toBeInTheDocument();
  });

  it("test_crear_albaran_manual: un pedido MANUAL sin albarán ofrece crearlo desde sus líneas y recarga con el nº", async () => {
    (getOrder as jest.Mock)
      .mockResolvedValueOnce(detail({
        external_source: "manual", order_number: "MANUAL-000010",
        factusol_albaran_number: null, factusol_payment: null, factusol_document: null,
      }))
      .mockResolvedValue(detail({
        external_source: "manual", order_number: "MANUAL-000010",
        factusol_albaran_number: "5-500004", factusol_payment: null, factusol_document: null,
      }));
    (createOrderAlbaran as jest.Mock).mockResolvedValue({ job_id: "job-m", order_id: "o-1", status: "queued" });
    (getQuoteJobStatus as jest.Mock).mockResolvedValue({
      status: "finished", result: { numero: "5-500004", status: "created", standalone: true },
    });
    const user = userEvent.setup();
    render(<ErpOrderDetailPage />);
    expect(await screen.findByText("Sin albarán en FACTUSOL")).toBeInTheDocument();
    const btn = screen.getByRole("button", { name: "Crear albarán en FACTUSOL" });
    expect(btn).toHaveAttribute("title", expect.stringMatching(/desde las líneas de este pedido manual/));
    await user.click(btn);
    await waitFor(() => expect(createOrderAlbaran).toHaveBeenCalledWith("o-1"));
    expect(await screen.findByText("Albarán FACTUSOL 5-500004 creado.")).toBeInTheDocument();
    expect(await screen.findByText("Albarán FACTUSOL 5-500004")).toBeInTheDocument();
    // Ya con nº: el PDF del albarán (#396) está disponible y no se ofrece crear otro.
    expect(screen.queryByRole("button", { name: "Crear albarán en FACTUSOL" })).not.toBeInTheDocument();
  });

  it("«sin pago» al convertir: el resumen enseña la forma de pago y el cobro sigue siendo manual", async () => {
    (getOrder as jest.Mock).mockResolvedValue(detail({
      factusol_albaran_number: "5-500008",
      factusol_payment: { ...PAGADO, paid: false, contrapartida: null, contrapartida_nombre: null },
    }));
    render(<ErpOrderDetailPage />);
    const resumen = await screen.findByRole("region", { name: "Resumen económico" });
    expect(resumen).toHaveTextContent("Transferencia");
    expect(resumen).toHaveTextContent("Sin pago");
    // Sin factura: el cobro (manual) espera a que exista.
    const cobro = screen.getByRole("button", { name: "Registrar cobro en FACTUSOL" });
    expect(cobro).toBeDisabled();
    expect(cobro).toHaveAttribute("title", expect.stringMatching(/Emite la factura primero/));
  });
});
