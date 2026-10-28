import { render, screen, within } from "@testing-library/react";
import ErpOrderDetailPage from "./page";
import { getOrder } from "../../../lib/erpApi";

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
jest.mock("../../../components/erp/GeneiShipmentSection", () => ({ GeneiShipmentSection: () => null }));
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
jest.mock("../../../components/erp/OrderFactusolClientPanel", () => ({
  OrderFactusolClientPanel: () => null,
}));
jest.mock("../../../lib/api", () => ({
  getCurrentUser: jest.fn(() => Promise.resolve({ role: "pedidos" })),
}));
jest.mock("../../../lib/erpApi", () => ({
  customerHeadline: () => "Escola de Ponent",
  customerLabel: () => "Escola de Ponent",
  resolveOrderCobroStatus: () => null,
  getOrder: jest.fn(),
  getOrderTimeline: jest.fn(() => Promise.resolve({ total: 0, items: [] })),
  getFactusolStatus: jest.fn(() => Promise.resolve({ status: "pending" })),
  getErpSettings: jest.fn(() => Promise.resolve({ shipping_origins: [] })),
  getOrderFactusolInvoiceRef: jest.fn(),
  getOrderFactusolCobro: jest.fn(() => Promise.resolve(null)),
  downloadOrderFactusolPedidoPdf: jest.fn(),
  fireTransition: jest.fn(),
  saveBlob: jest.fn(),
  updateOrderLanguage: jest.fn(),
  updateOrderSeguimiento: jest.fn(),
  completeOrder: jest.fn(),
  uncompleteOrder: jest.fn(),
}));

const ENLAZADO = {
  codigo: "enlazado",
  mensaje: "El pedido de cliente 2026/5873 de FACTUSOL (FLE-005873) es este pedido: mismo "
    + "cliente (B12345678) e importe. BoHub lo enlaza por la referencia. No se crea nada en FACTUSOL.",
  referencia: "FLE-005873",
};

/** El 5873 de fluxlasers.es traído a mano (on-hold: aún sin cobrar). */
function detail(over = {}) {
  return {
    id: "o-1", order_number: "FLUXLA-5873", contact_name: "Núria Vidal",
    company_name: "Escola de Ponent", external_source: "woocommerce", store_id: "3",
    contact_id: null, company_id: "c-1", total_amount: 44.77, currency: "EUR",
    payment_status: "pending", preparation_status: "pending_review",
    transport_status: "not_shipped", invoice_status: "not_invoiced", tracking_number: null,
    factusol_invoice_number: null, factusol_albaran_number: null, factusol_payment: null,
    factusol_document: null, serial_number: null, whiterip_license: null,
    shipping_origin: null, language: "es", approved_at: null,
    placed_at: "2026-10-23T09:18:59", created_at: "2026-10-28T10:15:00",
    externally_processed_at: null, externally_processed_note: null,
    externally_processed_by_user_id: null, notes: null, packing: null, lines: [],
    status_history: [], exceptions: [], available_transitions: {}, blockers: [], warnings: [],
    completed: false, completed_at: null, completed_by_user_id: null, completed_by_name: null,
    traido_a_mano: {
      at: "2026-10-28T10:15:00+00:00", por: "Bart", factusol: ENLAZADO, diferencias: null,
    },
    ...over,
  };
}

beforeEach(() => {
  (getOrder as jest.Mock).mockReset();
  window.history.replaceState({}, "", "/erp/orders/o-1");
});

describe("ERP · Ficha del pedido — pedido web traído a mano", () => {
  it("sin cobrar: dice cuándo y quién lo trajo, que aún no está cobrado y qué dice FACTUSOL", async () => {
    (getOrder as jest.Mock).mockResolvedValue(detail());
    render(<ErpOrderDetailPage />);
    const aviso = await screen.findByRole("note", { name: "Traído a mano de la web" });
    expect(aviso).toHaveTextContent(
      "Traído a mano de la web el 28/10/2026 por Bart: aún sin cobrar en la tienda",
    );
    expect(within(aviso).getByText("Al traerlo, FACTUSOL: estaba enlazado")).toBeInTheDocument();
    expect(aviso).toHaveTextContent("BoHub lo enlaza por la referencia. No se crea nada en FACTUSOL.");
    expect(within(aviso).queryByRole("list", { name: "Diferencias con la web" })).not.toBeInTheDocument();
  });

  it("cobrado: ya no dice «sin cobrar», y enseña lo que cambió en la web", async () => {
    (getOrder as jest.Mock).mockResolvedValue(detail({
      payment_status: "paid",
      traido_a_mano: {
        at: "2026-10-28T10:15:00+00:00", por: null,
        factusol: {
          codigo: "no_encontrado",
          mensaje: "FACTUSOL aún no tiene el pedido de cliente de este pedido (FLE-005873).",
          referencia: "FLE-005873",
        },
        diferencias: ["Total: 44.77 → 50.77", "Línea nueva: Pack 50 Láminas A4 (1)"],
      },
    }));
    render(<ErpOrderDetailPage />);
    const aviso = await screen.findByRole("note", { name: "Traído a mano de la web" });
    expect(aviso).toHaveTextContent("Traído a mano de la web el 28/10/2026.");
    expect(aviso).not.toHaveTextContent("aún sin cobrar");
    expect(within(aviso).getByText("Al traerlo, FACTUSOL: aún no estaba")).toBeInTheDocument();
    const lista = within(aviso).getByRole("list", { name: "Diferencias con la web" });
    expect(within(lista).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "Total: 44.77 → 50.77", "Línea nueva: Pack 50 Láminas A4 (1)",
    ]);
  });

  it("lo de FACTUSOL es de cuando se trajo: en pasado, separado de la frase y remite a la sección FACTUSOL", async () => {
    (getOrder as jest.Mock).mockResolvedValue(detail({
      payment_status: "paid", invoice_status: "invoiced",
      traido_a_mano: {
        at: "2026-10-28T10:15:00+00:00", por: "Bart",
        factusol: {
          codigo: "sin_factusol",
          mensaje: "FACTUSOL no contestaba: no se ha podido comprobar.",
          referencia: null,
        },
        diferencias: null,
      },
    }));
    render(<ErpOrderDetailPage />);
    const aviso = await screen.findByRole("note", { name: "Traído a mano de la web" });
    // La etiqueta y la frase, con su espacio (lector de pantalla y copiar).
    expect(aviso).toHaveTextContent(
      "Al traerlo, FACTUSOL: sin comprobar FACTUSOL no contestaba: no se ha podido comprobar. "
      + "El enlace de ahora se comprueba en vivo en la sección FACTUSOL de esta ficha.",
    );
  });

  it("anulado: no dice «aún sin cobrar» en ámbar, dice que está anulado", async () => {
    (getOrder as jest.Mock).mockResolvedValue(detail({
      cancelled: true, cancelled_at: "2026-10-29T09:00:00", cancelled_reason: "Cancelado en la tienda",
    }));
    render(<ErpOrderDetailPage />);
    const aviso = await screen.findByRole("note", { name: "Traído a mano de la web" });
    expect(aviso).toHaveTextContent("Traído a mano de la web el 28/10/2026 por Bart: está anulado.");
    expect(aviso).not.toHaveTextContent("aún sin cobrar");
    expect(aviso.querySelector(".pedido-traido-sin-cobrar")).toBeNull();
  });

  it("un pedido que entró solo no lleva el aviso", async () => {
    (getOrder as jest.Mock).mockResolvedValue(detail({ traido_a_mano: null }));
    render(<ErpOrderDetailPage />);
    await screen.findByRole("heading", { name: /FLUXLA-5873/ });
    expect(screen.queryByRole("note", { name: "Traído a mano de la web" })).not.toBeInTheDocument();
  });
});
