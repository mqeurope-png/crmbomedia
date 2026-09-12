import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ErpOrdersPage from "./page";
import { listOrders, refreshOrdersFactusolCobro } from "../../lib/erpApi";

jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ children, href }: { children: React.ReactNode; href: string }) => (
    <a href={href}>{children}</a>
  ),
}));
jest.mock("../../components/PageHeader", () => ({
  PageHeader: ({ title }: { title: string }) => <h1>{title}</h1>,
}));
jest.mock("../../components/erp/ExcludeSeguimientoModal", () => ({
  ExcludeSeguimientoModal: () => null,
}));
// Badge de estado del CRM: el texto crudo del estado («pending» / «paid»).
jest.mock("../../components/erp/OrderStatusBadge", () => ({
  OrderStatusBadge: ({ status }: { status: string }) => <span>{status}</span>,
}));
// Modal compartido (con sus propios tests): aquí importa que la fila lo abra
// con el pedido correcto y que la fila se repinte con el resultado.
jest.mock("../../components/erp/RegistrarCobroModal", () => ({
  RegistrarCobroModal: ({ orderId, orderNumber, onDone }: {
    orderId: string; orderNumber: string; onDone?: (info: unknown) => void;
  }) => (
    <div role="dialog" aria-label="modal cobro">
      MODAL COBRO {orderId} {orderNumber}
      <button type="button" onClick={() => onDone?.({
        order_id: orderId, order_number: orderNumber, status: "cobrada",
        invoice: { serie: 1, codigo: 260731, numero: "1-260731" },
        total: 100, total_cobrado: 100, saldo_pendiente: 0, estfac: "2", cobros: 1,
        checked_at: "2026-09-12T10:00:00Z",
      })}>
        done
      </button>
    </div>
  ),
}));
jest.mock("../../lib/api", () => ({
  getCurrentUser: jest.fn(() => Promise.resolve({ role: "admin" })),
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
  refreshOrdersFactusolCobro: jest.fn(),
}));

function order(over = {}) {
  return {
    id: "o-1", order_number: "BOPRIN-99930", contact_name: null, company_name: "Duplicoder",
    external_source: "woocommerce", store_id: null, contact_id: null, company_id: "c-1",
    total_amount: 121, currency: "EUR", payment_status: "pending",
    preparation_status: "packed", transport_status: "not_shipped",
    invoice_status: "invoiced_by_erp", tracking_number: null, factusol_invoice_number: "260730",
    factusol_albaran_number: null, factusol_cobro_status: "cobrada", factusol_invoice_serie: 1,
    factusol_cobro_checked_at: "2026-09-12T09:00:00Z",
    factusol_cobro: { numero: "1-260730", serie: 1, codigo: 260730, total: 121, total_cobrado: 121,
      saldo_pendiente: 0, estfac: "2", cobros: 1, cobrada: true,
      checked_at: "2026-09-12T09:00:00Z", source: "live" },
    serial_number: null, whiterip_license: null, shipping_origin: null, language: "es",
    approved_at: null, placed_at: "2026-09-01T10:00:00", created_at: "2026-09-01T10:00:00",
    externally_processed_at: null, excluded: false, seguimiento_excluded_at: null,
    seguimiento_excluded_reason: null, seguimiento_excluded_by_user_id: null,
    seguimiento_excluded_by_name: null, completed: false, completed_at: null,
    completed_by_user_id: null, completed_by_name: null,
    ...over,
  };
}

const ROWS = [
  // A: cobrada en FACTUSOL aunque el CRM diga «Pendiente» de pago.
  order(),
  // B: el CRM dice «Pagado», pero el cobro NO está registrado en FACTUSOL.
  order({ id: "o-2", order_number: "BOPRIN-99931", payment_status: "paid",
    factusol_invoice_number: "260731", factusol_cobro_status: "pendiente",
    factusol_cobro: { numero: "1-260731", serie: 1, codigo: 260731, total: 100, total_cobrado: 0,
      saldo_pendiente: 100, estfac: "0", cobros: 0, cobrada: false,
      checked_at: "2026-09-12T09:00:00Z", source: "live" },
    total_amount: 100 }),
  // C: sin factura.
  order({ id: "o-3", order_number: "BOPRIN-99932", invoice_status: "not_invoiced",
    factusol_invoice_number: null, factusol_cobro_status: null, factusol_invoice_serie: null,
    factusol_cobro: null, payment_status: "paid", total_amount: 24.2 }),
];

function row(number: string) {
  return screen.getByText(number).closest("tr") as HTMLTableRowElement;
}

beforeEach(() => {
  (listOrders as jest.Mock).mockReset();
  (listOrders as jest.Mock).mockResolvedValue(ROWS);
  (refreshOrdersFactusolCobro as jest.Mock).mockReset();
});

describe("ERP · Bandeja — cobro FACTUSOL por fila, filtro, botón y TOTAL con IVA", () => {
  it("test_bandeja_indicador_cobro_factusol: la fila enseña el estado FACTUSOL (cobrado / pendiente / nada) separado del «Pagado» del CRM", async () => {
    render(<ErpOrdersPage />);
    await screen.findByText("BOPRIN-99930");
    const a = row("BOPRIN-99930");
    expect(within(a).getByText("Cobrado FACTUSOL")).toBeInTheDocument();
    expect(within(a).getByText("pending")).toBeInTheDocument();          // PAGO del CRM
    const b = row("BOPRIN-99931");
    expect(within(b).getByText("Pendiente de cobro FACTUSOL")).toBeInTheDocument();
    expect(within(b).getByText("paid")).toBeInTheDocument();             // PAGO del CRM
    const c = row("BOPRIN-99932");
    expect(within(c).queryByText(/FACTUSOL/)).toBeNull();
  });

  it("test_bandeja_total_con_iva: la columna TOTAL pinta el importe final que manda el backend (con IVA)", async () => {
    render(<ErpOrdersPage />);
    await screen.findByText("BOPRIN-99930");
    expect(within(row("BOPRIN-99930")).getByText("121.00 EUR")).toBeInTheDocument();
    expect(within(row("BOPRIN-99932")).getByText("24.20 EUR")).toBeInTheDocument();
  });

  it("filtro «Cobro FACTUSOL» en la barra existente → parámetro `cobro`", async () => {
    const user = userEvent.setup();
    render(<ErpOrdersPage />);
    await screen.findByText("BOPRIN-99930");
    await user.selectOptions(screen.getByLabelText("Filtro cobro FACTUSOL"), "pendiente");
    await waitFor(() => expect(listOrders).toHaveBeenLastCalledWith(
      expect.objectContaining({ cobro: "pendiente" }),
    ));
    await user.selectOptions(screen.getByLabelText("Filtro cobro FACTUSOL"), "");
    await waitFor(() => expect(listOrders).toHaveBeenLastCalledWith(
      expect.objectContaining({ cobro: undefined }),
    ));
  });

  it("test_registrar_cobro_manual_desde_bandeja: «Registrar cobro» por fila abre el MISMO modal con la factura precargada y repinta solo esa fila", async () => {
    const user = userEvent.setup();
    render(<ErpOrdersPage />);
    await screen.findByText("BOPRIN-99930");
    // A ya cobrada → «Cobrado» deshabilitado; C sin factura → deshabilitado con tooltip.
    const btnA = screen.getByRole("button", { name: "Registrar cobro BOPRIN-99930" });
    expect(btnA).toBeDisabled();
    expect(btnA).toHaveTextContent("Cobrado");
    const btnC = screen.getByRole("button", { name: "Registrar cobro BOPRIN-99932" });
    expect(btnC).toBeDisabled();
    expect(btnC).toHaveAttribute("title", expect.stringMatching(/Emite la factura primero/));
    // B pendiente → abre el modal con ese pedido, sin salir de la bandeja.
    const btnB = screen.getByRole("button", { name: "Registrar cobro BOPRIN-99931" });
    expect(btnB).toBeEnabled();
    await user.click(btnB);
    expect(await screen.findByRole("dialog", { name: "modal cobro" }))
      .toHaveTextContent("MODAL COBRO o-2 BOPRIN-99931");
    const calls = (listOrders as jest.Mock).mock.calls.length;
    await user.click(screen.getByRole("button", { name: "done" }));
    await waitFor(() => expect(within(row("BOPRIN-99931")).getByText("Cobrado FACTUSOL")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Registrar cobro BOPRIN-99931" })).toBeDisabled();
    expect((listOrders as jest.Mock).mock.calls.length).toBe(calls);   // sin recargar la bandeja
    expect(screen.getByRole("status")).toHaveTextContent(/BOPRIN-99931: cobro registrado en FACTUSOL/);
    expect(document.querySelector(".form-error")).toBeNull();
  });

  it("«Actualizar cobros FACTUSOL» comprueba en bloque y repinta las filas en su sitio", async () => {
    (refreshOrdersFactusolCobro as jest.Mock).mockResolvedValue({
      checked: 2,
      items: [
        { ...ROWS[1], factusol_cobro_status: "cobrada",
          factusol_cobro: { ...ROWS[1].factusol_cobro, cobrada: true, saldo_pendiente: 0 } },
        ROWS[0],
      ],
    });
    const user = userEvent.setup();
    render(<ErpOrdersPage />);
    await screen.findByText("BOPRIN-99930");
    const calls = (listOrders as jest.Mock).mock.calls.length;
    await user.click(screen.getByRole("button", { name: "Actualizar cobros FACTUSOL" }));
    await waitFor(() => expect(refreshOrdersFactusolCobro).toHaveBeenCalledWith());
    await waitFor(() => expect(within(row("BOPRIN-99931")).getByText("Cobrado FACTUSOL")).toBeInTheDocument());
    expect(screen.getByRole("status")).toHaveTextContent("comprobado en 2 pedido(s)");
    expect((listOrders as jest.Mock).mock.calls.length).toBe(calls);
  });
});
