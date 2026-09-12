import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ErpOrderDetailPage from "./page";
import { downloadOrderFactusolPedidoPdf, getOrder, saveBlob } from "../../../lib/erpApi";

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
  PDF_LANGS: [{ value: "es", label: "Español" }, { value: "en", label: "English" }],
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
  downloadOrderFactusolAlbaranPdf: jest.fn(),
}));

function detail(over = {}) {
  return {
    id: "o-1", order_number: "PRO-004352", contact_name: null, company_name: "Duplicoder",
    external_source: "factusol_proforma", store_id: null, contact_id: null, company_id: "c-1",
    total_amount: 186.34, currency: "EUR", payment_status: "pending",
    preparation_status: "pending_review", transport_status: "not_shipped",
    invoice_status: "not_invoiced", tracking_number: null, factusol_invoice_number: null,
    factusol_albaran_number: "1-100327", factusol_payment: null,
    factusol_document: {
      doc_type: "presupuestos", serie: 1, codigo: 4352, numero: "1-004352",
      label: "presupuesto", by_ref: false,
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
  (downloadOrderFactusolPedidoPdf as jest.Mock).mockReset();
  (saveBlob as jest.Mock).mockReset();
  window.history.replaceState({}, "", "/erp/orders/o-1");
});

describe("ERP · Ficha del pedido — PDF del documento de origen en FACTUSOL", () => {
  it("test_pdf_pedido_desde_proforma: descarga el PDF del presupuesto de origen sin error", async () => {
    (getOrder as jest.Mock).mockResolvedValue(detail());
    const blob = new Blob(["%PDF-"], { type: "application/pdf" });
    (downloadOrderFactusolPedidoPdf as jest.Mock).mockResolvedValue(blob);
    const user = userEvent.setup();
    render(<ErpOrderDetailPage />);
    const btn = await screen.findByRole("button", { name: "PDF del presupuesto (FACTUSOL)" });
    expect(btn).toBeEnabled();
    await user.click(btn);
    await waitFor(() => expect(downloadOrderFactusolPedidoPdf).toHaveBeenCalledWith("o-1", "es"));
    expect(saveBlob).toHaveBeenCalledWith(blob, "Presupuesto_PRO-004352.pdf");
    expect(document.querySelector(".form-error")).toBeNull();
  });

  it("test_pdf_pedido_sin_origen_factusol_no_error: sin documento el botón queda deshabilitado con tooltip, sin banner rojo", async () => {
    (getOrder as jest.Mock).mockResolvedValue(detail({
      external_source: "manual", order_number: "MANUAL-000001",
      factusol_albaran_number: null, factusol_document: null,
    }));
    render(<ErpOrderDetailPage />);
    const btn = await screen.findByRole("button", { name: "PDF del pedido (FACTUSOL)" });
    expect(btn).toBeDisabled();
    expect(btn).toHaveAttribute("title", expect.stringMatching(/Sin documento en FACTUSOL/));
    expect(downloadOrderFactusolPedidoPdf).not.toHaveBeenCalled();
    expect(document.querySelector(".form-error")).toBeNull();
  });

  it("pedido web (F_PCL por referencia) aún no replicado: aviso discreto, no error rojo", async () => {
    (getOrder as jest.Mock).mockResolvedValue(detail({
      external_source: "woocommerce", order_number: "BOPRIN-99917",
      factusol_albaran_number: null,
      factusol_document: {
        doc_type: "pedidos", serie: null, codigo: null, numero: null,
        label: "pedido de cliente", by_ref: true,
      },
    }));
    (downloadOrderFactusolPedidoPdf as jest.Mock).mockRejectedValue(
      Object.assign(new Error("Este pedido aún no existe en FACTUSOL."), {
        status: 404, code: "pedido_not_in_factusol",
      }),
    );
    const user = userEvent.setup();
    render(<ErpOrderDetailPage />);
    await user.click(await screen.findByRole("button", { name: "PDF del pedido (FACTUSOL)" }));
    expect(await screen.findByRole("status")).toHaveTextContent("aún no existe en FACTUSOL");
    expect(document.querySelector(".form-error")).toBeNull();
    expect(saveBlob).not.toHaveBeenCalled();
    // Un fallo real (502) sí va en rojo.
    (downloadOrderFactusolPedidoPdf as jest.Mock).mockRejectedValue(
      Object.assign(new Error("timeout hablando con DELSOL"), { status: 502 }),
    );
    await user.click(screen.getByRole("button", { name: "PDF del pedido (FACTUSOL)" }));
    await waitFor(() => expect(document.querySelector(".form-error")).toHaveTextContent(/timeout/));
  });
});
