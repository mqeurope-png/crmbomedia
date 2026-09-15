import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SatQueuePage from "./page";
import type { SatHistoryRow, SatQueueItem } from "../../lib/erpApi";
import {
  findSatOrderByNumber,
  getErpSettings,
  getSatHistory,
  getSatQueue,
  markPickedUp,
  satEnqueueOrder,
} from "../../lib/erpApi";
import { getCurrentUser } from "../../lib/api";

jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ children, href, className }: { children: React.ReactNode; href: string; className?: string }) => (
    <a href={href} className={className}>{children}</a>
  ),
}));

jest.mock("../../lib/api", () => ({
  getCurrentUser: jest.fn(),
}));

jest.mock("../../lib/erpApi", () => ({
  // Helpers puros y catálogos: los reales.
  customerLabel: jest.requireActual("../../lib/erpApi").customerLabel,
  STATUS_LABELS: jest.requireActual("../../lib/erpApi").STATUS_LABELS,
  ERP_EDIT_ROLES: jest.requireActual("../../lib/erpApi").ERP_EDIT_ROLES,
  getSatQueue: jest.fn(),
  getSatHistory: jest.fn(),
  getErpSettings: jest.fn(),
  findSatOrderByNumber: jest.fn(),
  satEnqueueOrder: jest.fn(),
  // Dependencias de las cards / filas (no se disparan en estos tests salvo
  // «Marcar recogido»).
  downloadOrderFactusolAlbaranPdf: jest.fn(),
  fetchAlbaranFromWoo: jest.fn(),
  fireTransition: jest.fn(),
  listShippingFiles: jest.fn().mockResolvedValue([]),
  markPickedUp: jest.fn(),
  openShippingFile: jest.fn(),
  saveBlob: jest.fn(),
}));

const mockQueue = getSatQueue as jest.Mock;
const mockHistory = getSatHistory as jest.Mock;
const mockSettings = getErpSettings as jest.Mock;
const mockFind = findSatOrderByNumber as jest.Mock;
const mockEnqueue = satEnqueueOrder as jest.Mock;
const mockUser = getCurrentUser as jest.Mock;
const mockPicked = markPickedUp as jest.Mock;

function item(over: Partial<SatQueueItem> = {}): SatQueueItem {
  return {
    id: "o1", order_number: "BOP-1", contact_name: "Ana Pi", company_name: "Duplicoder SL",
    preparation_status: "in_queue", transport_status: "not_shipped", payment_status: "paid",
    total_amount: 100, currency: "EUR", lines: [{ sku: "A", description: "Art A", quantity: 1 }],
    has_albaran: false, has_etiqueta: false, store_slug: "boprint",
    placed_at: "2026-09-01T10:00:00+00:00", ...over,
  };
}

function historyRow(over: Partial<SatHistoryRow> = {}): SatHistoryRow {
  return {
    order_id: "o1", order_number: "BOP-1", contact_name: "Ana Pi", company_name: null,
    kind: "email_sat", at: "2026-09-10T09:30:00+00:00", actor_user_id: "u1",
    actor_name: "Pedidos User", to: ["taller@bomedia.net"], cc: [], subject: "Pedido BOP-1",
    attachment_kinds: ["albaran"], reason: null, from_status: null,
    preparation_status: "in_queue", transport_status: "not_shipped",
    factusol_albaran_number: "5-500001", has_albaran: false, store_slug: "boprint",
    placed_at: "2026-09-01T10:00:00+00:00", cancelled: false, excluded: false, ...over,
  };
}

beforeEach(() => {
  window.localStorage.clear();
  mockUser.mockResolvedValue({
    id: "p", role: "pedidos", full_name: "Pedidos User", email: "p@x", is_active: true,
  });
  mockSettings.mockResolvedValue({
    woocommerce_stores: [
      { slug: "artisjet", label: "Artisjet" }, { slug: "boprint", label: "BoPrint" },
    ],
  });
  mockQueue.mockResolvedValue({
    preparing: [item()],
    ready_for_pickup: [item({ id: "o2", order_number: "BOP-2", preparation_status: "packed",
                             has_albaran: true, has_etiqueta: true })],
  });
  mockHistory.mockResolvedValue({ items: [], limit: 100 });
});

describe("SatQueuePage (Lote B6)", () => {
  it("carga la cola sin filtros y pasa los filtros a getSatQueue", async () => {
    const user = userEvent.setup();
    render(<SatQueuePage />);
    expect(await screen.findByText("Por embalar: 1 · Listos: 1")).toBeInTheDocument();
    expect(mockQueue).toHaveBeenLastCalledWith({});

    await user.selectOptions(screen.getByLabelText("Estado"), "blocked");
    await waitFor(() => expect(mockQueue).toHaveBeenLastCalledWith({ estado: "blocked" }));

    // Tienda (del catálogo de Ajustes ERP) y fechas.
    await user.selectOptions(await screen.findByLabelText("Tienda"), "boprint");
    await waitFor(() => expect(mockQueue).toHaveBeenLastCalledWith(
      { estado: "blocked", store_slug: "boprint" },
    ));
    await user.type(screen.getByLabelText("Fecha desde"), "2026-09-01");
    await waitFor(() => expect(mockQueue).toHaveBeenLastCalledWith(
      expect.objectContaining({ desde: "2026-09-01" }),
    ));
    // Buscador (con retardo) — llega a la cola como `q`.
    await user.type(screen.getByLabelText("Buscar pedido o cliente"), "dupli");
    await waitFor(() => expect(mockQueue).toHaveBeenLastCalledWith(
      expect.objectContaining({ q: "dupli" }),
    ));
    // Limpiar filtros vuelve a la cola completa.
    await user.click(screen.getByRole("button", { name: "Limpiar filtros" }));
    await waitFor(() => expect(mockQueue).toHaveBeenLastCalledWith({}));
  });

  it("alterna Tarjetas / Lista, recuerda la vista y la lista conserva las acciones", async () => {
    const user = userEvent.setup();
    mockPicked.mockResolvedValue({ order_id: "o2", transport_status: "in_transit", already_picked_up: false });
    render(<SatQueuePage />);
    // Por defecto tarjetas: la card de «por embalar» enlaza al modo trabajo.
    expect(await screen.findByRole("link", { name: /Abrir →/ })).toHaveAttribute("href", "/erp/sat/o1");
    expect(screen.queryByRole("table", { name: "Pedidos por embalar" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Lista" }));
    const prepTable = await screen.findByRole("table", { name: "Pedidos por embalar" });
    expect(within(prepTable).getByRole("link", { name: "BOP-1" })).toHaveAttribute("href", "/erp/sat/o1");
    expect(within(prepTable).getByText("Ana Pi · Duplicoder SL")).toBeInTheDocument();
    expect(within(prepTable).getByText("boprint")).toBeInTheDocument();
    expect(within(prepTable).getByRole("button", { name: /Descargar albarán/ })).toBeInTheDocument();
    expect(window.localStorage.getItem("bohub.sat.queue.view")).toBe("list");

    // La fila de «listos» tiene las mismas acciones que la card: imprimir,
    // marcar recogido (con confirmación) y reabrir.
    const readyTable = screen.getByRole("table", { name: "Pedidos listos para envío" });
    expect(within(readyTable).getByRole("button", { name: /Imprimir albarán/ })).toBeInTheDocument();
    expect(within(readyTable).getByRole("button", { name: /Imprimir etiqueta/ })).toBeInTheDocument();
    expect(within(readyTable).getByRole("button", { name: "Reabrir preparación" })).toBeInTheDocument();
    await user.click(within(readyTable).getByRole("button", { name: /Marcar recogido/ }));
    await user.click(within(readyTable).getByRole("button", { name: "Sí, recogido" }));
    await waitFor(() => expect(mockPicked).toHaveBeenCalledWith("o2"));
    // Tras la acción se recarga la cola.
    await waitFor(() => expect(mockQueue.mock.calls.length).toBeGreaterThanOrEqual(2));

    await user.click(screen.getByRole("button", { name: "Tarjetas" }));
    expect(await screen.findByRole("link", { name: /Abrir →/ })).toBeInTheDocument();
    expect(window.localStorage.getItem("bohub.sat.queue.view")).toBe("cards");
  });

  it("arranca en vista lista si así quedó guardado", async () => {
    window.localStorage.setItem("bohub.sat.queue.view", "list");
    render(<SatQueuePage />);
    expect(await screen.findByRole("table", { name: "Pedidos por embalar" })).toBeInTheDocument();
  });

  it("el historial se pliega/despliega, carga con los filtros y pinta las filas", async () => {
    const user = userEvent.setup();
    mockHistory.mockResolvedValue({
      items: [
        historyRow(),
        historyRow({
          order_id: "o3", order_number: "ART-9", kind: "aprobado", at: "2026-09-09T08:00:00+00:00",
          actor_name: "Admin User", to: [], subject: null, reason: "aprobado en Cola PEDIDOS",
          from_status: "pending_review", preparation_status: "packed", factusol_albaran_number: null,
          has_albaran: true, contact_name: null, company_name: "Otra SL",
        }),
      ],
      limit: 100,
    });
    render(<SatQueuePage />);
    await screen.findByText("Por embalar: 1 · Listos: 1");
    expect(mockHistory).not.toHaveBeenCalled();

    await user.selectOptions(screen.getByLabelText("Estado"), "ready");
    await user.click(screen.getByRole("button", { name: /Historial de enviados al taller/ }));
    await waitFor(() => expect(mockHistory).toHaveBeenCalledWith({ estado: "ready" }));
    const table = await screen.findByRole("table", { name: "Enviados al taller" });
    const rows = within(table).getAllByRole("row");
    expect(rows).toHaveLength(3); // cabecera + 2
    expect(within(rows[1]).getByRole("link", { name: "BOP-1" })).toHaveAttribute("href", "/erp/orders/o1");
    expect(within(rows[1]).getByText("Email SAT")).toBeInTheDocument();
    expect(within(rows[1]).getByText("taller@bomedia.net")).toBeInTheDocument();
    expect(within(rows[1]).getByText("Pedidos User")).toBeInTheDocument();
    expect(within(rows[1]).getByText("5-500001")).toBeInTheDocument();
    expect(within(rows[2]).getByText("Aprobado")).toBeInTheDocument();
    expect(within(rows[2]).getByText("aprobado en Cola PEDIDOS")).toBeInTheDocument();
    expect(within(rows[2]).getByText("Otra SL")).toBeInTheDocument();
    expect(within(rows[2]).getByText("Subido")).toBeInTheDocument();
    expect(within(rows[2]).getByText("Embalado")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Historial de enviados al taller/ }));
    expect(screen.queryByRole("table", { name: "Enviados al taller" })).not.toBeInTheDocument();
  });

  it("«Añadir pedido a la cola» resuelve el nº, encola y recarga", async () => {
    const user = userEvent.setup();
    mockFind.mockResolvedValue({
      id: "o9", order_number: "BOP-9", contact_name: null, company_name: "Nueva SL",
      preparation_status: "pending_review", transport_status: "not_shipped",
      already_queued: false, cancelled: false, excluded: false,
    });
    mockEnqueue.mockResolvedValue({
      order_id: "o9", order_number: "BOP-9", preparation_status: "in_queue",
      already_queued: false, approved: true, via: "approve",
    });
    render(<SatQueuePage />);
    await screen.findByText("Por embalar: 1 · Listos: 1");
    const calls = mockQueue.mock.calls.length;

    await user.type(await screen.findByLabelText("Número de pedido a añadir"), " bop-9 ");
    await user.click(screen.getByRole("button", { name: "Añadir" }));
    await waitFor(() => expect(mockFind).toHaveBeenCalledWith("bop-9"));
    await waitFor(() => expect(mockEnqueue).toHaveBeenCalledWith("o9"));
    expect(await screen.findByRole("status")).toHaveTextContent(
      "BOP-9 (Nueva SL) añadido a la Cola SAT (aprobado).",
    );
    await waitFor(() => expect(mockQueue.mock.calls.length).toBeGreaterThan(calls));
    expect(screen.getByLabelText("Número de pedido a añadir")).toHaveValue("");

    // Error del backend (409 anulado) → mensaje, sin recargar de más.
    mockFind.mockResolvedValue({ id: "o10", order_number: "BOP-10", already_queued: false });
    mockEnqueue.mockRejectedValue(new Error("El pedido BOP-10 está anulado: no se puede añadir a la Cola SAT."));
    await user.type(screen.getByLabelText("Número de pedido a añadir"), "BOP-10");
    await user.click(screen.getByRole("button", { name: "Añadir" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/está anulado/);
  });

  it("sin permiso de edición (rol sat) no enseña «Añadir pedido a la cola»", async () => {
    mockUser.mockResolvedValue({
      id: "s", role: "sat", full_name: "Sat User", email: "s@x", is_active: true,
    });
    render(<SatQueuePage />);
    await screen.findByText("Por embalar: 1 · Listos: 1");
    expect(screen.queryByLabelText("Número de pedido a añadir")).not.toBeInTheDocument();
  });
});
