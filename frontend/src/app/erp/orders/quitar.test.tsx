import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ErpOrdersPage from "./page";
import {
  excludeSeguimiento,
  includeSeguimiento,
  listOrders,
  previewExcludeSeguimiento,
} from "../../lib/erpApi";

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
  excludeSeguimiento: jest.fn(),
  includeSeguimiento: jest.fn(),
  previewExcludeSeguimiento: jest.fn(),
}));

function order(over = {}) {
  return {
    id: "o-1", order_number: "BOPRIN-99922", contact_name: null, company_name: "Mookase",
    external_source: "woocommerce", store_id: null, contact_id: null, company_id: "c-1",
    total_amount: 12.5, currency: "EUR", payment_status: "pending",
    preparation_status: "pending_review", transport_status: "not_shipped",
    invoice_status: "not_invoiced", tracking_number: null, factusol_invoice_number: null,
    approved_at: null, placed_at: "2026-09-01T10:00:00", created_at: "2026-09-01T10:00:00",
    externally_processed_at: null, excluded: false, seguimiento_excluded_at: null,
    seguimiento_excluded_reason: null, seguimiento_excluded_by_user_id: null,
    seguimiento_excluded_by_name: null,
    ...over,
  };
}

const HIDDEN = order({
  excluded: true, seguimiento_excluded_at: "2026-09-10T18:00:00",
  seguimiento_excluded_reason: "prueba: probando agile", seguimiento_excluded_by_name: "Bart",
});

beforeEach(() => {
  (listOrders as jest.Mock).mockReset();
  (listOrders as jest.Mock).mockImplementation((f?: { show_excluded?: boolean }) =>
    Promise.resolve(f?.show_excluded ? [HIDDEN] : [order()]),
  );
  (previewExcludeSeguimiento as jest.Mock).mockReset();
  (previewExcludeSeguimiento as jest.Mock).mockResolvedValue({
    ok: true,
    items: [{
      order_id: "o-1", order_number: "BOPRIN-99922", cliente: "Mookase", woo_status: "on-hold",
      excluido: false, excluido_motivo: null, avisos: ["escrito en Drive"],
    }],
    con_avisos: 1, ya_excluidos: 0,
  });
  (excludeSeguimiento as jest.Mock).mockReset();
  (excludeSeguimiento as jest.Mock).mockResolvedValue({
    ok: true, excluded: 1, already_excluded: 0, reason: "prueba",
    avisos: { "BOPRIN-99922": ["escrito en Drive"] }, con_avisos: 1,
  });
  (includeSeguimiento as jest.Mock).mockReset();
  (includeSeguimiento as jest.Mock).mockResolvedValue({
    ok: true, included: 1, already_included: 0,
  });
});

describe("ERP · Pedidos (bandeja) — quitar / reincluir a mano", () => {
  it("«Quitar» en la fila abre el diálogo de la bandeja, avisa y quita igualmente", async () => {
    const user = userEvent.setup();
    render(<ErpOrdersPage />);
    await screen.findByText("Mookase");
    await user.click(screen.getByRole("button", { name: "Quitar BOPRIN-99922 de la bandeja" }));
    const dialog = within(await screen.findByRole("dialog"));
    expect(dialog.getByRole("heading", { name: "Quitar BOPRIN-99922 de la bandeja" }))
      .toBeInTheDocument();
    expect(await dialog.findByRole("alert")).toHaveTextContent("escrito en Drive");
    const confirm = dialog.getByRole("button", { name: "Quitar igualmente (1)" });
    await waitFor(() => expect(confirm).toBeEnabled());
    await user.click(dialog.getByRole("button", { name: "Prueba" }));
    await user.click(confirm);
    await waitFor(() =>
      expect(excludeSeguimiento).toHaveBeenCalledWith(["o-1"], undefined, "prueba"),
    );
    expect(await screen.findByRole("status")).toHaveTextContent(/1 pedido\(s\) quitado\(s\) de la bandeja/);
    // Se recarga la bandeja tras quitar.
    expect((listOrders as jest.Mock).mock.calls.length).toBeGreaterThanOrEqual(2);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("selección múltiple: «Quitar de la bandeja (n)» con un motivo común", async () => {
    (listOrders as jest.Mock).mockImplementation(() => Promise.resolve([
      order(),
      order({ id: "o-2", order_number: "BOPRIN-99927", company_name: "NEONLED" }),
    ]));
    (previewExcludeSeguimiento as jest.Mock).mockResolvedValue({
      ok: true, items: [], con_avisos: 0, ya_excluidos: 0,
    });
    (excludeSeguimiento as jest.Mock).mockResolvedValue({
      ok: true, excluded: 2, already_excluded: 0, reason: "cancelado", avisos: {}, con_avisos: 0,
    });
    const user = userEvent.setup();
    render(<ErpOrdersPage />);
    await screen.findByText("NEONLED");
    await user.click(screen.getByRole("checkbox", { name: "Seleccionar BOPRIN-99922" }));
    await user.click(screen.getByRole("checkbox", { name: "Seleccionar BOPRIN-99927" }));
    await user.click(screen.getByRole("button", { name: "Quitar de la bandeja (2)" }));
    const dialog = within(await screen.findByRole("dialog"));
    expect(dialog.getByRole("heading", { name: "Quitar 2 pedidos de la bandeja" }))
      .toBeInTheDocument();
    const confirm = await dialog.findByRole("button", { name: "Quitar de la bandeja (2)" });
    await waitFor(() => expect(confirm).toBeEnabled());
    await user.click(dialog.getByRole("button", { name: "Cancelado" }));
    await user.click(confirm);
    await waitFor(() =>
      expect(excludeSeguimiento).toHaveBeenCalledWith(["o-1", "o-2"], undefined, "cancelado"),
    );
  });

  it("«Ver ocultados» lista los quitados con motivo y «Reincluir» los devuelve", async () => {
    const user = userEvent.setup();
    render(<ErpOrdersPage />);
    await screen.findByText("Mookase");
    await user.click(screen.getByRole("checkbox", { name: "Ver pedidos ocultados de la bandeja" }));
    await waitFor(() =>
      expect(listOrders).toHaveBeenLastCalledWith(expect.objectContaining({ show_excluded: true })),
    );
    expect(await screen.findByText("prueba: probando agile")).toBeInTheDocument();
    expect(screen.getByText(/10\/9\/2026 · Bart/)).toBeInTheDocument();
    expect(screen.getByTitle(/Quitado a mano de las listas de trabajo/)).toHaveTextContent("Oculto");
    await user.click(screen.getByRole("button", { name: "Reincluir BOPRIN-99922 en la bandeja" }));
    await waitFor(() => expect(includeSeguimiento).toHaveBeenCalledWith(["o-1"]));
    expect(await screen.findByRole("status")).toHaveTextContent(/1 pedido\(s\) reincluido\(s\)/);
  });

  it("sin permiso de edición no hay casillas ni botón «Quitar»", async () => {
    const { getCurrentUser } = jest.requireMock("../../lib/api");
    (getCurrentUser as jest.Mock).mockResolvedValueOnce({ role: "user" });
    render(<ErpOrdersPage />);
    await screen.findByText("Mookase");
    expect(screen.queryByRole("button", { name: /Quitar/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: "Seleccionar BOPRIN-99922" })).not.toBeInTheDocument();
  });
});
