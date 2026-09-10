import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ErpOrderDetailPage from "./page";
import { completeOrder, getOrder, uncompleteOrder } from "../../../lib/erpApi";

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
  getFactusolStatus: jest.fn(() => Promise.resolve({ status: "none" })),
  getErpSettings: jest.fn(() => Promise.resolve({ shipping_origins: [] })),
  getOrderFactusolInvoiceRef: jest.fn(),
  downloadOrderFactusolPedidoPdf: jest.fn(),
  fireTransition: jest.fn(),
  saveBlob: jest.fn(),
  updateOrderLanguage: jest.fn(),
  updateOrderSeguimiento: jest.fn(),
  completeOrder: jest.fn(),
  uncompleteOrder: jest.fn(),
}));

function detail(over = {}) {
  return {
    id: "o-1", order_number: "BOPRIN-99930", contact_name: null, company_name: "Duplicoder",
    external_source: "woocommerce", store_id: null, contact_id: null, company_id: "c-1",
    total_amount: 4500, currency: "EUR", payment_status: "paid",
    preparation_status: "packed", transport_status: "not_shipped",
    invoice_status: "generated", tracking_number: null, factusol_invoice_number: "5-260100",
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
  (getOrder as jest.Mock).mockResolvedValue(detail());
  (completeOrder as jest.Mock).mockReset();
  (completeOrder as jest.Mock).mockResolvedValue({
    ...detail({ completed: true, completed_at: "2026-09-10T18:00:00", completed_by_name: "Bart" }),
    already_completed: false, completion_avisos: ["el envío no consta como enviado en BoHub"],
  });
  (uncompleteOrder as jest.Mock).mockReset();
  (uncompleteOrder as jest.Mock).mockResolvedValue({ ...detail(), already_uncompleted: false });
  jest.restoreAllMocks();
});

describe("ERP · Ficha del pedido — marcar completado", () => {
  it("«Marcar completado» guarda, enseña el estado con quién y ofrece «Desmarcar»", async () => {
    const confirm = jest.spyOn(window, "confirm");
    const user = userEvent.setup();
    render(<ErpOrderDetailPage />);
    await user.click(await screen.findByRole("button", { name: "Marcar completado" }));
    expect(confirm).not.toHaveBeenCalled();  // facturado: no pregunta
    await waitFor(() => expect(completeOrder).toHaveBeenCalledWith("o-1"));
    expect(await screen.findByRole("status")).toHaveTextContent(/WooCommerce no cambia/);
    expect(screen.getByText(/Marcado como completado el .* por Bart/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Desmarcar completado" }));
    await waitFor(() => expect(uncompleteOrder).toHaveBeenCalledWith("o-1"));
    expect(await screen.findByRole("button", { name: "Marcar completado" })).toBeInTheDocument();
    expect(screen.queryByText(/Marcado como completado el/)).not.toBeInTheDocument();
  });

  it("sin factura: avisa con una confirmación y, si se cancela, no marca", async () => {
    (getOrder as jest.Mock).mockResolvedValue(detail({
      invoice_status: "not_invoiced", factusol_invoice_number: null,
    }));
    const confirm = jest.spyOn(window, "confirm").mockReturnValue(false);
    const user = userEvent.setup();
    render(<ErpOrderDetailPage />);
    await user.click(await screen.findByRole("button", { name: "Marcar completado" }));
    expect(confirm).toHaveBeenCalledWith(expect.stringMatching(/aún no está facturado/));
    expect(completeOrder).not.toHaveBeenCalled();
  });

  it("sin permiso de edición no hay botón", async () => {
    const { getCurrentUser } = jest.requireMock("../../../lib/api");
    (getCurrentUser as jest.Mock).mockResolvedValueOnce({ role: "user" });
    render(<ErpOrderDetailPage />);
    await screen.findByRole("heading", { name: "Pedido BOPRIN-99930" });
    expect(screen.queryByRole("button", { name: "Marcar completado" })).not.toBeInTheDocument();
  });
});
