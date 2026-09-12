import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ErpOrdersPage from "./page";
import { completeOrdersBulk, listOrders } from "../../lib/erpApi";

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
jest.mock("../../components/erp/ExcludeSeguimientoModal", () => ({
  ExcludeSeguimientoModal: () => null,
}));
jest.mock("../../components/erp/RegistrarCobroModal", () => ({
  RegistrarCobroModal: () => null,
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
  completeOrdersBulk: jest.fn(),
  uncompleteOrder: jest.fn(),
  excludeSeguimiento: jest.fn(),
  includeSeguimiento: jest.fn(),
  previewExcludeSeguimiento: jest.fn(),
  refreshOrdersFactusolCobro: jest.fn(),
}));

function order(over = {}) {
  return {
    id: "o-1", order_number: "BOPRIN-99930", contact_name: null, company_name: "Duplicoder",
    external_source: "woocommerce", store_id: null, contact_id: null, company_id: "c-1",
    total_amount: 121, currency: "EUR", payment_status: "paid",
    preparation_status: "packed", transport_status: "not_shipped",
    invoice_status: "not_invoiced", tracking_number: null, factusol_invoice_number: null,
    factusol_albaran_number: null, factusol_cobro_status: null, factusol_invoice_serie: null,
    factusol_cobro: null, serial_number: null, whiterip_license: null, shipping_origin: null,
    language: "es", approved_at: null, placed_at: "2026-09-01T10:00:00",
    created_at: "2026-09-01T10:00:00", externally_processed_at: null, excluded: false,
    seguimiento_excluded_at: null, seguimiento_excluded_reason: null,
    seguimiento_excluded_by_user_id: null, seguimiento_excluded_by_name: null,
    completed: false, completed_at: null, completed_by_user_id: null, completed_by_name: null,
    ...over,
  };
}

// A: sin factura; B: facturada; C: ya completada.
const A = order();
const B = order({ id: "o-2", order_number: "BOPRIN-99931", invoice_status: "invoiced_by_erp",
  factusol_invoice_number: "260731" });
const C = order({ id: "o-3", order_number: "BOPRIN-99932", invoice_status: "invoiced_by_erp",
  factusol_invoice_number: "260732", completed: true, completed_at: "2026-09-10T10:00:00Z",
  completed_by_user_id: "u-1", completed_by_name: "Bart" });

function done(o: ReturnType<typeof order>, avisos: string[] = []) {
  return {
    ...o, completed: true, completed_at: "2026-09-12T12:00:00Z", completed_by_user_id: "u-1",
    completed_by_name: "Bart", already_completed: false, completion_avisos: avisos,
  };
}

function row(number: string) {
  return screen.getByText(number).closest("tr") as HTMLTableRowElement;
}

beforeEach(() => {
  (listOrders as jest.Mock).mockReset();
  (listOrders as jest.Mock).mockResolvedValue([A, B, C]);
  (completeOrdersBulk as jest.Mock).mockReset();
  jest.restoreAllMocks();
});

describe("ERP · Bandeja — «Completar seleccionados (N)» junto a «Quitar de la bandeja»", () => {
  it("aparece con la selección, confirma con el recuento y los sin factura, marca en lote y repinta las filas sin recargar", async () => {
    const confirm = jest.spyOn(window, "confirm").mockReturnValue(true);
    (completeOrdersBulk as jest.Mock).mockResolvedValue({
      ok: true, completed: 2, already_completed: 0, failed: [], sin_facturar: 1,
      items: [
        done(A, ["aún sin facturar", "el envío no consta como enviado en BoHub"]),
        done(B, ["el envío no consta como enviado en BoHub"]),
      ],
    });
    const user = userEvent.setup();
    render(<ErpOrdersPage />);
    await screen.findByText("BOPRIN-99930");
    expect(screen.queryByRole("button", { name: /Completar seleccionados/ })).toBeNull();
    await user.click(screen.getByLabelText("Seleccionar BOPRIN-99930"));
    await user.click(screen.getByLabelText("Seleccionar BOPRIN-99931"));
    const btn = screen.getByRole("button", { name: "Completar seleccionados (2)" });
    expect(screen.getByRole("button", { name: "Quitar de la bandeja (2)" })).toBeInTheDocument();
    const calls = (listOrders as jest.Mock).mock.calls.length;
    await user.click(btn);
    expect(confirm).toHaveBeenCalledWith(expect.stringMatching(/2 pedido\(s\) como completados/));
    expect(confirm).toHaveBeenCalledWith(expect.stringMatching(/1 aún sin facturar/));
    expect(confirm).toHaveBeenCalledWith(expect.stringMatching(/WooCommerce no cambia/));
    await waitFor(() => expect(completeOrdersBulk).toHaveBeenCalledWith(["o-1", "o-2"]));
    // Las filas afectadas se repintan con el badge, sin recargar la bandeja.
    await waitFor(() => expect(within(row("BOPRIN-99930")).getByText("Completado")).toBeInTheDocument());
    expect(within(row("BOPRIN-99931")).getByText("Completado")).toBeInTheDocument();
    expect((listOrders as jest.Mock).mock.calls.length).toBe(calls);
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent("2 pedido(s) marcado(s) como completado(s)");
    expect(status).toHaveTextContent("WooCommerce no cambia");
    expect(status).toHaveTextContent("Aviso: 1 sin facturar");
    // La selección se limpia: el botón masivo desaparece.
    expect(screen.queryByRole("button", { name: /Completar seleccionados/ })).toBeNull();
    expect(document.querySelector(".form-error")).toBeNull();
  });

  it("si Bart cancela la confirmación no se marca nada", async () => {
    jest.spyOn(window, "confirm").mockReturnValue(false);
    const user = userEvent.setup();
    render(<ErpOrdersPage />);
    await screen.findByText("BOPRIN-99930");
    await user.click(screen.getByLabelText("Seleccionar todo"));
    await user.click(screen.getByRole("button", { name: "Completar seleccionados (3)" }));
    expect(completeOrdersBulk).not.toHaveBeenCalled();
    expect(within(row("BOPRIN-99930")).queryByText("Completado")).toBeNull();
  });

  it("«seleccionar todo» con uno ya completado: idempotente, la confirmación lo dice y el resumen lo cuenta aparte", async () => {
    const confirm = jest.spyOn(window, "confirm").mockReturnValue(true);
    (completeOrdersBulk as jest.Mock).mockResolvedValue({
      ok: true, completed: 2, already_completed: 1, failed: [], sin_facturar: 1,
      items: [done(A, ["aún sin facturar"]), done(B), { ...C, already_completed: true, completion_avisos: [] }],
    });
    const user = userEvent.setup();
    render(<ErpOrdersPage />);
    await screen.findByText("BOPRIN-99930");
    await user.click(screen.getByLabelText("Seleccionar todo"));
    await user.click(screen.getByRole("button", { name: "Completar seleccionados (3)" }));
    expect(confirm).toHaveBeenCalledWith(expect.stringMatching(/1 ya estaba\(n\) completado\(s\)/));
    await waitFor(() => expect(completeOrdersBulk).toHaveBeenCalledWith(["o-1", "o-2", "o-3"]));
    expect(await screen.findByRole("status")).toHaveTextContent("1 ya lo estaba(n)");
  });

  it("fallo parcial: informa cuáles fallaron y el resto queda completado", async () => {
    jest.spyOn(window, "confirm").mockReturnValue(true);
    (completeOrdersBulk as jest.Mock).mockResolvedValue({
      ok: false, completed: 1, already_completed: 0,
      failed: [{ order_id: "o-2", error: "fallo simulado al guardar" }], sin_facturar: 1,
      items: [done(A, ["aún sin facturar"])],
    });
    const user = userEvent.setup();
    render(<ErpOrdersPage />);
    await screen.findByText("BOPRIN-99930");
    await user.click(screen.getByLabelText("Seleccionar BOPRIN-99930"));
    await user.click(screen.getByLabelText("Seleccionar BOPRIN-99931"));
    await user.click(screen.getByRole("button", { name: "Completar seleccionados (2)" }));
    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent("1 pedido(s) marcado(s) como completado(s)");
    expect(status).toHaveTextContent("No se pudo completar 1: BOPRIN-99931: fallo simulado al guardar");
    expect(within(row("BOPRIN-99930")).getByText("Completado")).toBeInTheDocument();
    expect(within(row("BOPRIN-99931")).queryByText("Completado")).toBeNull();
    expect(document.querySelector(".form-error")).toBeNull();   // no es un error rojo
  });
});
