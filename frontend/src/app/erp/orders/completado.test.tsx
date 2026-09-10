import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ErpOrdersPage from "./page";
import { completeOrder, listOrders, uncompleteOrder } from "../../lib/erpApi";

jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ children }: { children: React.ReactNode }) => <span>{children}</span>,
}));
jest.mock("../../components/PageHeader", () => ({
  PageHeader: ({ title }: { title: string }) => <h1>{title}</h1>,
}));
jest.mock("../../components/erp/OrderStatusBadge", () => ({
  OrderStatusBadge: ({ status }: { status: string }) => <span>{status}</span>,
}));
jest.mock("../../lib/api", () => ({
  getCurrentUser: jest.fn(() => Promise.resolve({ role: "pedidos" })),
}));
jest.mock("../../lib/erpApi", () => ({
  ERP_EDIT_ROLES: ["admin", "pedidos"],
  EXCLUSION_REASON_CODES: ["cancelado", "duplicado", "prueba", "reembolsado", "otro"],
  customerLabel: (o: { contact_name?: string | null; company_name?: string | null }) =>
    [o.contact_name, o.company_name].filter(Boolean).join(" · "),
  listOrders: jest.fn(),
  completeOrder: jest.fn(),
  uncompleteOrder: jest.fn(),
  excludeSeguimiento: jest.fn(),
  includeSeguimiento: jest.fn(),
  previewExcludeSeguimiento: jest.fn(),
}));

function order(over = {}) {
  return {
    id: "o-1", order_number: "BOPRIN-99930", contact_name: null, company_name: "Duplicoder",
    external_source: "woocommerce", store_id: null, contact_id: null, company_id: "c-1",
    total_amount: 4500, currency: "EUR", payment_status: "paid",
    preparation_status: "packed", transport_status: "not_shipped",
    invoice_status: "not_invoiced", tracking_number: null, factusol_invoice_number: null,
    approved_at: null, placed_at: "2026-09-01T10:00:00", created_at: "2026-09-01T10:00:00",
    externally_processed_at: null, excluded: false, seguimiento_excluded_at: null,
    seguimiento_excluded_reason: null, seguimiento_excluded_by_user_id: null,
    seguimiento_excluded_by_name: null, completed: false, completed_at: null,
    completed_by_user_id: null, completed_by_name: null,
    ...over,
  };
}

const DONE = order({
  completed: true, completed_at: "2026-09-10T18:00:00", completed_by_name: "Bart",
  invoice_status: "generated", factusol_invoice_number: "5-260100",
});

beforeEach(() => {
  (listOrders as jest.Mock).mockReset();
  (listOrders as jest.Mock).mockResolvedValue([order()]);
  (completeOrder as jest.Mock).mockReset();
  (completeOrder as jest.Mock).mockResolvedValue({
    ...DONE, already_completed: false,
    completion_avisos: ["aún sin facturar", "el envío no consta como enviado en BoHub"],
  });
  (uncompleteOrder as jest.Mock).mockReset();
  (uncompleteOrder as jest.Mock).mockResolvedValue({ ...order(), already_uncompleted: false });
  jest.restoreAllMocks();
});

describe("ERP · Pedidos (bandeja) — marcar completado", () => {
  it("«Completar» en la fila: sin factura pide confirmación, avisa y marca igualmente", async () => {
    const confirm = jest.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();
    render(<ErpOrdersPage />);
    await screen.findByText("Duplicoder");
    await user.click(screen.getByRole("button", { name: "Marcar completado BOPRIN-99930" }));
    expect(confirm).toHaveBeenCalledWith(expect.stringMatching(/aún no está facturado/));
    await waitFor(() => expect(completeOrder).toHaveBeenCalledWith("o-1"));
    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent(/marcado como completado/);
    expect(status).toHaveTextContent(/solo en BoHub; WooCommerce no cambia/);
    expect(status).toHaveTextContent(/aún sin facturar/);
    // Se recarga la bandeja.
    expect((listOrders as jest.Mock).mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it("si Bart cancela la confirmación, no se marca", async () => {
    jest.spyOn(window, "confirm").mockReturnValue(false);
    const user = userEvent.setup();
    render(<ErpOrdersPage />);
    await screen.findByText("Duplicoder");
    await user.click(screen.getByRole("button", { name: "Marcar completado BOPRIN-99930" }));
    expect(completeOrder).not.toHaveBeenCalled();
  });

  it("facturado: marca sin preguntar; el completado muestra el badge y «Desmarcar» lo revierte", async () => {
    const confirm = jest.spyOn(window, "confirm");
    (listOrders as jest.Mock).mockResolvedValueOnce([order({
      invoice_status: "generated", factusol_invoice_number: "5-260100",
    })]).mockResolvedValue([DONE]);
    (completeOrder as jest.Mock).mockResolvedValue({
      ...DONE, already_completed: false, completion_avisos: [],
    });
    const user = userEvent.setup();
    render(<ErpOrdersPage />);
    await screen.findByText("Duplicoder");
    await user.click(screen.getByRole("button", { name: "Marcar completado BOPRIN-99930" }));
    expect(confirm).not.toHaveBeenCalled();
    await waitFor(() => expect(completeOrder).toHaveBeenCalledWith("o-1"));
    // Tras recargar: badge «Completado» (con quién) y botón «Desmarcar».
    expect(await screen.findByText("Completado")).toHaveAttribute(
      "title", expect.stringMatching(/por Bart/),
    );
    await user.click(screen.getByRole("button", { name: "Desmarcar completado BOPRIN-99930" }));
    await waitFor(() => expect(uncompleteOrder).toHaveBeenCalledWith("o-1"));
    expect(await screen.findByText(/ya no está marcado como completado/)).toBeInTheDocument();
  });

  it("el filtro «Completado» pide a la API solo completados / sin completar", async () => {
    const user = userEvent.setup();
    render(<ErpOrdersPage />);
    await screen.findByText("Duplicoder");
    await user.selectOptions(screen.getByRole("combobox", { name: "Filtro completado" }), "yes");
    await waitFor(() =>
      expect(listOrders).toHaveBeenLastCalledWith(expect.objectContaining({ completed: true })),
    );
    await user.selectOptions(screen.getByRole("combobox", { name: "Filtro completado" }), "no");
    await waitFor(() =>
      expect(listOrders).toHaveBeenLastCalledWith(expect.objectContaining({ completed: false })),
    );
    await user.selectOptions(screen.getByRole("combobox", { name: "Filtro completado" }), "");
    await waitFor(() =>
      expect(listOrders).toHaveBeenLastCalledWith(expect.objectContaining({ completed: undefined })),
    );
  });
});
