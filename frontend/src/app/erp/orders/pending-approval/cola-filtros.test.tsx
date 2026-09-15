import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import PendingApprovalPage from "./page";
import { approveOrder, listPendingApproval } from "../../../lib/erpApi";

/** ERP · Cola PEDIDOS (Lote B8) — filtros ligeros: buscar (local), tienda y
 *  orden por fecha (backend). Las tarjetas y sus acciones siguen igual. */

jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ children, href }: { children: React.ReactNode; href: string }) => (
    <a href={href}>{children}</a>
  ),
}));
jest.mock("../../../components/PageHeader", () => ({
  PageHeader: ({ title }: { title: string }) => <h1>{title}</h1>,
}));
jest.mock("../../../components/erp/MarkExternalModal", () => ({
  MarkExternalModal: () => <div role="dialog" aria-label="modal externo" />,
}));
// La tarjeta tiene sus propios tests: aquí importa que la cola la pinte con
// el pedido correcto y le pase la aprobación.
jest.mock("../../../components/erp/OrderApprovalCard", () => ({
  OrderApprovalCard: ({ order, canApprove, onApprove }: {
    order: { id: string; order_number: string; company_name: string | null };
    canApprove: boolean; onApprove: (id: string) => void;
  }) => (
    <div data-approval-card={order.order_number}>
      <span>{order.order_number}</span>
      <span>{order.company_name}</span>
      {canApprove ? (
        <button type="button" onClick={() => onApprove(order.id)}>Aprobar {order.order_number}</button>
      ) : null}
    </div>
  ),
}));
jest.mock("../../../lib/api", () => ({
  getCurrentUser: jest.fn(() => Promise.resolve({ role: "pedidos" })),
}));
jest.mock("../../../lib/erpApi", () => ({
  ERP_EDIT_ROLES: ["admin", "pedidos"],
  customerLabel: (o: { contact_name?: string | null; company_name?: string | null }) =>
    [o.contact_name, o.company_name].filter(Boolean).join(" · "),
  listPendingApproval: jest.fn(),
  approveOrder: jest.fn(),
  markExternallyProcessed: jest.fn(),
  bulkMarkExternallyProcessed: jest.fn(),
  getErpSettings: jest.fn(() => Promise.resolve({
    woocommerce_stores: [
      { slug: "artisjet", label: "artisJet" },
      { slug: "boprint", label: "BoPrint" },
      { slug: "fluxlasers", label: "Flux Lasers" },
    ],
  })),
}));

function order(over = {}) {
  return {
    id: "o-1", order_number: "BOPRIN-1", contact_name: null, company_name: "Grabados FG",
    external_source: "woocommerce", store_id: null, contact_id: null, company_id: "c-1",
    total_amount: 100, currency: "EUR", payment_status: "paid",
    preparation_status: "pending_review", transport_status: "not_shipped",
    invoice_status: "not_invoiced", tracking_number: null, factusol_invoice_number: null,
    approved_at: null, placed_at: "2026-09-01T10:00:00", created_at: "2026-09-01T10:00:00",
    externally_processed_at: null, blockers: [], warnings: [],
    ...over,
  };
}

const ROWS = [
  order(),
  order({ id: "o-2", order_number: "ARTISJ-2", company_name: "La Maison de la Plaque" }),
  order({ id: "o-3", order_number: "FLUXLA-3", contact_name: "Ándrés Pérez", company_name: null }),
];

beforeEach(() => {
  (listPendingApproval as jest.Mock).mockReset();
  (listPendingApproval as jest.Mock).mockResolvedValue(ROWS);
  (approveOrder as jest.Mock).mockReset();
  (approveOrder as jest.Mock).mockResolvedValue({});
});

describe("ERP · Cola PEDIDOS — filtros ligeros (Lote B8)", () => {
  it("por defecto pide la cola sin tienda y de más antiguo a más reciente", async () => {
    render(<PendingApprovalPage />);
    await screen.findByText("BOPRIN-1");
    expect(listPendingApproval).toHaveBeenCalledWith({ store_slug: undefined, sort: "placed_asc" });
    expect(screen.getByText("3 de 3 pedido(s)")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Limpiar filtros" })).toBeNull();
  });

  it("la búsqueda filtra en local por nº o cliente, sin acentos ni mayúsculas", async () => {
    const user = userEvent.setup();
    render(<PendingApprovalPage />);
    await screen.findByText("BOPRIN-1");
    const calls = (listPendingApproval as jest.Mock).mock.calls.length;
    await user.type(screen.getByRole("searchbox", { name: "Buscar pedido" }), "maison");
    expect(screen.getByText("ARTISJ-2")).toBeInTheDocument();
    expect(screen.queryByText("BOPRIN-1")).toBeNull();
    expect(screen.getByText("1 de 3 pedido(s)")).toBeInTheDocument();
    await user.clear(screen.getByRole("searchbox", { name: "Buscar pedido" }));
    await user.type(screen.getByRole("searchbox", { name: "Buscar pedido" }), "andres");
    expect(screen.getByText("FLUXLA-3")).toBeInTheDocument();
    expect(screen.queryByText("ARTISJ-2")).toBeNull();
    await user.clear(screen.getByRole("searchbox", { name: "Buscar pedido" }));
    await user.type(screen.getByRole("searchbox", { name: "Buscar pedido" }), "zzz");
    expect(screen.getByText(/Ningún pedido de la cola casa con «zzz»/)).toBeInTheDocument();
    // Buscar no vuelve a pedir la cola al backend.
    expect((listPendingApproval as jest.Mock).mock.calls.length).toBe(calls);
  });

  it("tienda y orden se piden al backend; «Limpiar filtros» vuelve al estado inicial", async () => {
    const user = userEvent.setup();
    render(<PendingApprovalPage />);
    await screen.findByText("BOPRIN-1");
    const tienda = screen.getByRole("combobox", { name: "Filtro tienda" });
    await waitFor(() => expect(tienda).toHaveTextContent("BoPrint"));
    await user.selectOptions(tienda, "boprint");
    await waitFor(() => expect(listPendingApproval).toHaveBeenLastCalledWith(
      { store_slug: "boprint", sort: "placed_asc" },
    ));
    await user.click(screen.getByRole("button", { name: "Orden ascendente" }));
    await waitFor(() => expect(listPendingApproval).toHaveBeenLastCalledWith(
      { store_slug: "boprint", sort: "placed_desc" },
    ));
    expect(screen.getByRole("button", { name: "Orden descendente" })).toHaveTextContent("Fecha ↓");
    await user.click(screen.getByRole("button", { name: "Limpiar filtros" }));
    await waitFor(() => expect(listPendingApproval).toHaveBeenLastCalledWith(
      { store_slug: undefined, sort: "placed_asc" },
    ));
    expect(screen.getByRole("button", { name: "Orden ascendente" })).toBeInTheDocument();
  });

  it("las tarjetas siguen ahí con «Aprobar», también con un filtro puesto", async () => {
    const user = userEvent.setup();
    render(<PendingApprovalPage />);
    await screen.findByText("BOPRIN-1");
    await user.type(screen.getByRole("searchbox", { name: "Buscar pedido" }), "BOPRIN");
    await user.click(screen.getByRole("button", { name: "Aprobar BOPRIN-1" }));
    await waitFor(() => expect(approveOrder).toHaveBeenCalledWith("o-1"));
    // Sale de la cola (y el contador lo refleja: quedan 2, ninguno casa).
    await waitFor(() => expect(screen.queryByText("BOPRIN-1")).toBeNull());
    expect(screen.getByText("0 de 2 pedido(s)")).toBeInTheDocument();
  });
});
