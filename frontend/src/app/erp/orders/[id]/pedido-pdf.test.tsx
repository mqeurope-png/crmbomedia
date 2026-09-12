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

const LABEL = "PDF del pedido (FACTUSOL)";

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
      label: "presupuesto", by_ref: false, ref: null,
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

const WEB = {
  external_source: "woocommerce", order_number: "FLUXLA-5789", store_id: "store-flux",
  factusol_albaran_number: null,
  factusol_document: {
    doc_type: "pedidos", serie: null, codigo: null, numero: null,
    label: "pedido de cliente", by_ref: true, ref: "FLE-005789",
  },
};

beforeEach(() => {
  (getOrder as jest.Mock).mockReset();
  (downloadOrderFactusolPedidoPdf as jest.Mock).mockReset();
  (saveBlob as jest.Mock).mockReset();
  window.history.replaceState({}, "", "/erp/orders/o-1");
});

describe("ERP · Ficha del pedido — «PDF del pedido (FACTUSOL)»", () => {
  it("pedido web: la etiqueta es «PDF del pedido (FACTUSOL)», está habilitado y descarga el PDF de su F_PCL", async () => {
    (getOrder as jest.Mock).mockResolvedValue(detail(WEB));
    const blob = new Blob(["%PDF-"], { type: "application/pdf" });
    (downloadOrderFactusolPedidoPdf as jest.Mock).mockResolvedValue(blob);
    const user = userEvent.setup();
    render(<ErpOrderDetailPage />);
    const btn = await screen.findByRole("button", { name: LABEL });
    expect(btn).toBeEnabled();
    // La ficha no comprueba en FACTUSOL si el F_PCL existe: el botón siempre
    // intenta la descarga (el tooltip solo informa de la referencia).
    expect(btn).toHaveAttribute("title", expect.stringMatching(/FLE-005789/));
    await user.click(btn);
    await waitFor(() => expect(downloadOrderFactusolPedidoPdf).toHaveBeenCalledWith("o-1", "es"));
    expect(saveBlob).toHaveBeenCalledWith(blob, "Pedido_FLUXLA-5789.pdf");
    expect(document.querySelector(".form-error")).toBeNull();
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("proforma y pedido de cliente: misma etiqueta fija (nunca «PDF del presupuesto») y descargan su documento de origen", async () => {
    (getOrder as jest.Mock).mockResolvedValue(detail());
    const blob = new Blob(["%PDF-"], { type: "application/pdf" });
    (downloadOrderFactusolPedidoPdf as jest.Mock).mockResolvedValue(blob);
    const user = userEvent.setup();
    const { unmount } = render(<ErpOrderDetailPage />);
    const btn = await screen.findByRole("button", { name: LABEL });
    expect(btn).toBeEnabled();
    expect(screen.queryByRole("button", { name: /PDF del presupuesto/ })).toBeNull();
    expect(btn).toHaveAttribute("title", expect.stringMatching(/presupuesto/));
    await user.click(btn);
    await waitFor(() => expect(downloadOrderFactusolPedidoPdf).toHaveBeenCalledWith("o-1", "es"));
    expect(saveBlob).toHaveBeenCalledWith(blob, "Pedido_PRO-004352.pdf");
    expect(document.querySelector(".form-error")).toBeNull();
    unmount();

    (getOrder as jest.Mock).mockResolvedValue(detail({
      external_source: "factusol_pedido", order_number: "PCL-5-000123",
      factusol_albaran_number: null,
      factusol_document: {
        doc_type: "pedidos", serie: 5, codigo: 123, numero: "5-000123",
        label: "pedido de cliente", by_ref: false, ref: null,
      },
    }));
    render(<ErpOrderDetailPage />);
    const btn2 = await screen.findByRole("button", { name: LABEL });
    expect(btn2).toBeEnabled();
    expect(btn2).toHaveAttribute("title", expect.stringMatching(/pedido de cliente.*5-000123/));
    await user.click(btn2);
    await waitFor(() => expect(saveBlob).toHaveBeenCalledWith(blob, "Pedido_PCL-5-000123.pdf"));
    expect(document.querySelector(".form-error")).toBeNull();
  });

  it("sin documento en FACTUSOL: botón deshabilitado con tooltip, sin banner rojo", async () => {
    (getOrder as jest.Mock).mockResolvedValue(detail({
      external_source: "manual", order_number: "MANUAL-000001",
      factusol_albaran_number: null, factusol_document: null,
    }));
    render(<ErpOrderDetailPage />);
    const btn = await screen.findByRole("button", { name: LABEL });
    expect(btn).toBeDisabled();
    expect(btn).toHaveAttribute("title", expect.stringMatching(/Sin documento en FACTUSOL/));
    expect(downloadOrderFactusolPedidoPdf).not.toHaveBeenCalled();
    expect(document.querySelector(".form-error")).toBeNull();
  });

  it("pedido web cuyo F_PCL aún no existe: 404 controlado → aviso discreto con la referencia buscada, no error rojo", async () => {
    (getOrder as jest.Mock).mockResolvedValue(detail(WEB));
    (downloadOrderFactusolPedidoPdf as jest.Mock).mockRejectedValue(
      Object.assign(new Error(
        "Este pedido aún no existe en FACTUSOL: ningún pedido de cliente con referencia FLU-005789 (ejercicio 2026). Sí existe FLE-005789: si es este pedido, configura el prefijo de referencia «FLE» de la tienda Flux Lasers en Ajustes ERP (Serie de facturación → Prefijo referencia FACTUSOL).",
      ), { status: 404, code: "pedido_not_in_factusol" }),
    );
    const user = userEvent.setup();
    render(<ErpOrderDetailPage />);
    await user.click(await screen.findByRole("button", { name: LABEL }));
    const notice = await screen.findByRole("status");
    expect(notice).toHaveTextContent("aún no existe en FACTUSOL");
    expect(notice).toHaveTextContent("FLU-005789");
    expect(notice).toHaveTextContent("configura el prefijo de referencia «FLE»");
    expect(document.querySelector(".form-error")).toBeNull();
    expect(saveBlob).not.toHaveBeenCalled();
    // Un fallo real (502) sí va en rojo.
    (downloadOrderFactusolPedidoPdf as jest.Mock).mockRejectedValue(
      Object.assign(new Error("timeout hablando con DELSOL"), { status: 502 }),
    );
    await user.click(screen.getByRole("button", { name: LABEL }));
    await waitFor(() => expect(document.querySelector(".form-error")).toHaveTextContent(/timeout/));
  });
});
