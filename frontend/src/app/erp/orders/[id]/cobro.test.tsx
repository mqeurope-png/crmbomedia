import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ErpOrderDetailPage from "./page";
import { getOrder, getOrderFactusolCobro } from "../../../lib/erpApi";

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
jest.mock("../../../components/erp/FactusolAlbaranPdfButton", () => ({
  FactusolAlbaranPdfButton: () => null,
}));
// El modal es COMPARTIDO con la bandeja y tiene sus propios tests; aquí solo
// importa que la ficha lo abra con el pedido correcto y recoja el resultado.
jest.mock("../../../components/erp/RegistrarCobroModal", () => ({
  RegistrarCobroModal: ({ orderId, onDone }: {
    orderId: string; onDone?: (info: unknown) => void;
  }) => (
    <div role="dialog" aria-label="modal cobro">
      MODAL COBRO {orderId}
      <button type="button" onClick={() => onDone?.({
        order_id: orderId, order_number: "BOPRIN-99930", status: "cobrada",
        invoice: { serie: 1, codigo: 260729, numero: "1-260729" },
        total: 72.6, saldo_pendiente: 0, checked_at: "2026-09-12T10:00:00Z",
      })}>
        done
      </button>
    </div>
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
  getOrderFactusolCobro: jest.fn(),
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
}));

function detail(over = {}) {
  return {
    id: "o-1", order_number: "BOPRIN-99930", contact_name: null, company_name: "Duplicoder",
    external_source: "woocommerce", store_id: null, contact_id: null, company_id: "c-1",
    total_amount: 72.6, currency: "EUR", payment_status: "pending",
    preparation_status: "packed", transport_status: "not_shipped",
    invoice_status: "invoiced_by_erp", tracking_number: null, factusol_invoice_number: "260729",
    factusol_albaran_number: null, factusol_payment: null, factusol_document: null,
    factusol_cobro_status: "pendiente", factusol_invoice_serie: 1,
    factusol_cobro_checked_at: "2026-09-12T09:00:00Z",
    factusol_cobro: {
      numero: "1-260729", serie: 1, codigo: 260729, total: 72.6, total_cobrado: 0,
      saldo_pendiente: 72.6, estfac: "0", cobros: 0, cobrada: false,
      checked_at: "2026-09-12T09:00:00Z", source: "live",
    },
    serial_number: null, whiterip_license: null, shipping_origin: null, language: "es",
    approved_at: null, placed_at: "2026-09-01T10:00:00", created_at: "2026-09-01T10:00:00",
    externally_processed_at: null, externally_processed_note: null,
    externally_processed_by_user_id: null, notes: null, packing: null, lines: [],
    status_history: [], exceptions: [], available_transitions: {}, blockers: [], warnings: [],
    completed: false, completed_at: null, completed_by_user_id: null, completed_by_name: null,
    ...over,
  };
}

beforeEach(() => {
  (getOrder as jest.Mock).mockReset();
  (getOrderFactusolCobro as jest.Mock).mockReset();
  (getOrderFactusolCobro as jest.Mock).mockResolvedValue({
    order_id: "o-1", order_number: "BOPRIN-99930", status: "pendiente",
    invoice: { serie: 1, codigo: 260729, numero: "1-260729" }, saldo_pendiente: 72.6,
  });
  window.history.replaceState({}, "", "/erp/orders/o-1");
});

describe("ERP · Ficha del pedido — «Registrar cobro en FACTUSOL»", () => {
  it("test_registrar_cobro_manual_desde_ficha: factura pendiente → badge + botón habilitado que abre el modal con el pedido; al terminar queda «Cobrado»", async () => {
    (getOrder as jest.Mock).mockResolvedValue(detail());
    const user = userEvent.setup();
    render(<ErpOrderDetailPage />);
    const btn = await screen.findByRole("button", { name: "Registrar cobro en FACTUSOL" });
    expect(btn).toBeEnabled();
    expect(screen.getByText("Pendiente de cobro FACTUSOL")).toBeInTheDocument();
    // El «Pagado» del CRM es otra cosa: el pedido sigue pendiente de pago.
    expect(await screen.findByText(/saldo 72.60 €/)).toBeInTheDocument();
    await user.click(btn);
    expect(await screen.findByRole("dialog", { name: "modal cobro" })).toHaveTextContent("MODAL COBRO o-1");
    // Tras registrar, el backend ya persistió «cobrada» y el re-chequeo en
    // vivo también lo dice: la ficha recarga y queda «Cobrado».
    (getOrder as jest.Mock).mockResolvedValue(detail({ factusol_cobro_status: "cobrada" }));
    (getOrderFactusolCobro as jest.Mock).mockResolvedValue({
      order_id: "o-1", order_number: "BOPRIN-99930", status: "cobrada",
      invoice: { serie: 1, codigo: 260729, numero: "1-260729" }, saldo_pendiente: 0,
    });
    await user.click(screen.getByRole("button", { name: "done" }));
    expect(await screen.findByText("Cobrado FACTUSOL")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "Cobrado en FACTUSOL" })).toBeDisabled());
    expect(document.querySelector(".form-error")).toBeNull();
  });

  it("test_cobro_manual_sin_factura_boton_deshabilitado: sin factura el botón queda deshabilitado con tooltip «emite la factura primero», sin error rojo", async () => {
    (getOrder as jest.Mock).mockResolvedValue(detail({
      invoice_status: "not_invoiced", factusol_invoice_number: null,
      factusol_cobro_status: null, factusol_invoice_serie: null, factusol_cobro: null,
    }));
    render(<ErpOrderDetailPage />);
    const btn = await screen.findByRole("button", { name: "Registrar cobro en FACTUSOL" });
    expect(btn).toBeDisabled();
    expect(btn).toHaveAttribute("title", expect.stringMatching(/Emite la factura primero/));
    expect(screen.getByText("sin factura")).toBeInTheDocument();
    expect(screen.queryByText(/FACTUSOL sin comprobar/)).toBeNull();
    expect(getOrderFactusolCobro).not.toHaveBeenCalled();   // no consulta FACTUSOL
    expect(document.querySelector(".form-error")).toBeNull();
  });

  it("test_cobro_manual_idempotente_no_doble: ya cobrada → «Cobrado FACTUSOL» y el botón no permite un segundo cobro", async () => {
    (getOrder as jest.Mock).mockResolvedValue(detail({
      factusol_cobro_status: "cobrada", payment_status: "pending",
      factusol_cobro: { numero: "1-260729", serie: 1, codigo: 260729, total: 72.6,
        total_cobrado: 72.6, saldo_pendiente: 0, estfac: "2", cobros: 1, cobrada: true,
        checked_at: "2026-09-12T09:00:00Z", source: "manual" },
    }));
    (getOrderFactusolCobro as jest.Mock).mockResolvedValue({
      order_id: "o-1", order_number: "BOPRIN-99930", status: "cobrada",
      invoice: { serie: 1, codigo: 260729, numero: "1-260729" }, saldo_pendiente: 0,
    });
    render(<ErpOrderDetailPage />);
    expect(await screen.findByText("Cobrado FACTUSOL")).toBeInTheDocument();
    const btn = screen.getByRole("button", { name: "Cobrado en FACTUSOL" });
    expect(btn).toBeDisabled();
    expect(btn).toHaveAttribute("title", expect.stringMatching(/ya consta cobrada/));
  });

  it("el estado en vivo manda sobre el persistido (cobrada fuera de BoHub → badge «Cobrado» sin recargar)", async () => {
    (getOrder as jest.Mock).mockResolvedValue(detail({ factusol_cobro_status: null, factusol_cobro: null }));
    (getOrderFactusolCobro as jest.Mock).mockResolvedValue({
      order_id: "o-1", order_number: "BOPRIN-99930", status: "cobrada",
      invoice: { serie: 1, codigo: 260729, numero: "1-260729" }, saldo_pendiente: 0,
    });
    render(<ErpOrderDetailPage />);
    expect(await screen.findByText("Cobrado FACTUSOL")).toBeInTheDocument();
    expect(getOrderFactusolCobro).toHaveBeenCalledWith("o-1");
  });
});
