import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ErpOrdersPage from "./page";
import { listOrders } from "../../lib/erpApi";

/** ERP · rediseño de flujo (Fase 1) — la BANDEJA DE TRABAJO.
 *
 *  Lo que se comprueba aquí es lo que cambia de verdad: que el trabajo se
 *  organiza por colas (con su contador), que cada pedido enseña la acción que
 *  toca y su alerta, y que NO se ha perdido ninguna de las acciones ni de los
 *  filtros que ya existían. */

jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ children, href, className, ...rest }: {
    children: React.ReactNode; href: string; className?: string;
  }) => <a href={href} className={className} {...rest}>{children}</a>,
}));
jest.mock("../../components/PageHeader", () => ({
  PageHeader: ({ title, actions }: { title: string; actions?: React.ReactNode }) => (
    <><h1>{title}</h1>{actions}</>
  ),
}));
jest.mock("../../components/erp/OrderStatusBadge", () => ({
  OrderStatusBadge: ({ status }: { status: string }) => <span>{status}</span>,
}));
jest.mock("../../components/erp/ExcludeSeguimientoModal", () => ({
  ExcludeSeguimientoModal: () => <div role="dialog" aria-label="modal quitar" />,
}));
jest.mock("../../components/erp/RegistrarCobroModal", () => ({
  RegistrarCobroModal: ({ orderNumber }: { orderNumber: string }) => (
    <div role="dialog" aria-label="modal cobro">MODAL COBRO {orderNumber}</div>
  ),
}));
jest.mock("../../lib/api", () => ({
  getCurrentUser: jest.fn(() => Promise.resolve({ role: "admin" })),
}));
jest.mock("../../lib/erpApi", () => ({
  ERP_EDIT_ROLES: ["admin", "pedidos"],
  EXCLUSION_REASON_CODES: ["cancelado", "otro"],
  customerLabel: (o: { contact_name?: string | null; company_name?: string | null }) =>
    [o.contact_name, o.company_name].filter(Boolean).join(" · "),
  listOrders: jest.fn(),
  completeOrder: jest.fn(),
  uncompleteOrder: jest.fn(),
  completeOrdersBulk: jest.fn(),
  excludeSeguimiento: jest.fn(),
  includeSeguimiento: jest.fn(),
  previewExcludeSeguimiento: jest.fn(),
  refreshOrdersFactusolCobro: jest.fn(),
}));

type Wf = Record<string, unknown>;

function wf(over: Wf = {}): Wf {
  return {
    queue: "por_revisar", queue_label: "Por revisar",
    next_action: "aprobar", next_action_label: "Aprobar",
    next_action_hint: "Revisa el pedido y apruébalo para que pase al taller.",
    alerts: [], blocked: false, regime: "nacional", company: null,
    steps: [], ...over,
  };
}

function order(over = {}) {
  return {
    id: "o-1", order_number: "BOPRIN-1", contact_name: "Bruno García",
    company_name: "Grabados FG", external_source: "woocommerce", store_id: null,
    contact_id: null, company_id: "c-1", total_amount: 1720.62, currency: "EUR",
    payment_status: "paid", preparation_status: "pending_review",
    transport_status: "not_shipped", invoice_status: "not_invoiced",
    tracking_number: null, factusol_invoice_number: null, factusol_cobro_status: null,
    factusol_cobro: null, approved_at: null, placed_at: "2026-09-08T10:00:00",
    created_at: "2026-09-08T10:00:00", externally_processed_at: null, excluded: false,
    seguimiento_excluded_at: null, seguimiento_excluded_reason: null,
    seguimiento_excluded_by_user_id: null, seguimiento_excluded_by_name: null,
    completed: false, completed_at: null, completed_by_user_id: null,
    completed_by_name: null, workflow: wf(),
    ...over,
  };
}

// Los cuatro casos de la maqueta: aprobar, facturar (intracomunitario),
// incidencia bloqueante y cobro pendiente.
const A = order();
const B = order({
  id: "o-2", order_number: "ARTISJ-2", contact_name: "Alexandre",
  company_name: "La Maison de la Plaque", total_amount: 351.52,
  workflow: wf({
    queue: "por_facturar", queue_label: "Por facturar",
    next_action: "emitir_factura", next_action_label: "Emitir factura",
    next_action_hint: "Emite la factura en FACTUSOL.", regime: "intracomunitario",
    alerts: [{
      code: "cliente_intracomunitario",
      text: "Cliente intracomunitario: la factura debe salir sin IVA.",
      action: null, action_label: null, blocking: false,
    }],
  }),
});
const C = order({
  id: "o-3", order_number: "PRO-3", external_source: "manual",
  contact_name: "Eduard Riera", company_name: null, total_amount: 60.5,
  workflow: wf({
    queue: "incidencias", queue_label: "Incidencias", blocked: true,
    next_action: "vincular_empresa", next_action_label: "Vincular empresa a FACTUSOL",
    next_action_hint: "«Riera SL» no está vinculada a un cliente de FACTUSOL.",
    alerts: [{
      code: "empresa_sin_vincular",
      text: "«Riera SL» no está vinculada a un cliente de FACTUSOL.",
      action: "vincular_empresa", action_label: "Vincular empresa a FACTUSOL", blocking: true,
    }],
  }),
});
const D = order({
  id: "o-4", order_number: "BOPRIN-4", total_amount: 100,
  invoice_status: "invoiced_by_erp", factusol_invoice_number: "260731",
  factusol_cobro_status: "pendiente",
  workflow: wf({
    queue: "por_cobrar", queue_label: "Por cobrar",
    next_action: "registrar_cobro", next_action_label: "Registrar cobro",
    next_action_hint: "El pedido consta pagado: registra el cobro en FACTUSOL.",
  }),
});

const COUNTS = {
  por_revisar: 4, por_facturar: 6, por_cobrar: 3, por_enviar: 5,
  incidencias: 2, listo: 9,
};

function page(items: unknown[]) {
  return { items, queue_counts: COUNTS, queue: null };
}

function row(number: string) {
  return screen.getByText(number).closest("[data-order-row]") as HTMLElement;
}

async function abrirMenu(user: ReturnType<typeof userEvent.setup>, numero: string) {
  await user.click(screen.getByRole("button", { name: `Más acciones ${numero}` }));
}

beforeEach(() => {
  (listOrders as jest.Mock).mockReset();
  (listOrders as jest.Mock).mockResolvedValue(page([A, B, C, D]));
});

describe("ERP · Bandeja de trabajo (rediseño de flujo)", () => {
  it("arriba van las colas con su contador y al elegir una se pide esa cola", async () => {
    const user = userEvent.setup();
    render(<ErpOrdersPage />);
    await screen.findByText("BOPRIN-1");
    expect(screen.getByRole("button", { name: "Por revisar (4)" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Por facturar (6)" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Incidencias (2)" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Por facturar (6)" }));
    await waitFor(() => expect(listOrders).toHaveBeenLastCalledWith(
      expect.objectContaining({ queue: "por_facturar" }),
    ));
    // Volver a pulsarla quita el filtro: la bandeja vuelve a enseñarlo todo.
    await user.click(screen.getByRole("button", { name: "Por facturar (6)" }));
    await waitFor(() => expect(listOrders).toHaveBeenLastCalledWith(
      expect.objectContaining({ queue: undefined }),
    ));
  });

  it("cada pedido enseña su siguiente acción, su alerta y el importe con el régimen", async () => {
    render(<ErpOrdersPage />);
    await screen.findByText("BOPRIN-1");

    // Pendiente de aprobar: la acción lleva a donde se hace.
    expect(within(row("BOPRIN-1")).getByRole("link", { name: "Aprobar BOPRIN-1" }))
      .toHaveAttribute("href", "/erp/orders/o-1");

    // Intracomunitario: se avisa de que la factura va SIN IVA y el importe lo dice.
    const b = within(row("ARTISJ-2"));
    expect(b.getByRole("link", { name: "Emitir factura ARTISJ-2" })).toBeInTheDocument();
    expect(b.getByText(/la factura debe salir sin IVA/)).toBeInTheDocument();
    expect(b.getByText("exento · intracomunitario")).toBeInTheDocument();

    // Incidencia bloqueante: la tarjeta se marca y la acción es resolverla.
    const c = row("PRO-3");
    expect(c.className).toContain("is-alert");
    expect(within(c).getByText(/no está vinculada a un cliente de FACTUSOL/)).toBeInTheDocument();
    expect(within(c).getByRole("link", { name: "Vincular empresa a FACTUSOL PRO-3" })).toBeInTheDocument();
    expect(within(c).getByText("manual")).toBeInTheDocument();
    // El mapeo de líneas ya no existe en el flujo: ni aviso ni botón.
    expect(screen.queryByText(/sin mapear/)).toBeNull();
    expect(screen.queryByRole("link", { name: /Mapear líneas/ })).toBeNull();
  });

  it("la acción que la bandeja sabe hacer se dispara sin salir (registrar cobro) y no se repite en el menú", async () => {
    const user = userEvent.setup();
    render(<ErpOrdersPage />);
    await screen.findByText("BOPRIN-4");
    await user.click(screen.getByRole("button", { name: "Registrar cobro BOPRIN-4" }));
    expect(await screen.findByRole("dialog", { name: "modal cobro" }))
      .toHaveTextContent("MODAL COBRO BOPRIN-4");
    // Está una sola vez: como botón principal, no también en el «⋯».
    await abrirMenu(user, "BOPRIN-4");
    expect(screen.getAllByRole("button", { name: "Registrar cobro BOPRIN-4" })).toHaveLength(1);
  });

  it("no se pierde ninguna acción ni filtro de los de siempre", async () => {
    const user = userEvent.setup();
    render(<ErpOrdersPage />);
    await screen.findByText("BOPRIN-1");

    // Filtros de siempre (ahora como refinamiento de la cola).
    for (const label of [
      "Filtro preparación", "Filtro pago", "Filtro completado", "Filtro cobro FACTUSOL",
    ]) {
      expect(screen.getByRole("combobox", { name: label })).toBeInTheDocument();
    }
    expect(screen.getByRole("button", { name: "Actualizar cobros FACTUSOL" })).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Mostrar procesados externamente" })).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Ver pedidos ocultados de la bandeja" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Cola PEDIDOS" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "+ Nuevo pedido manual" })).toBeInTheDocument();

    // Acciones por pedido: siguen todas, en el menú «⋯».
    await abrirMenu(user, "BOPRIN-1");
    expect(screen.getByRole("link", { name: "Abrir ficha" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Marcar completado BOPRIN-1" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Registrar cobro BOPRIN-1" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Quitar BOPRIN-1 de la bandeja" })).toBeInTheDocument();

    // Y las de bloque, con la selección múltiple.
    await user.click(screen.getByRole("checkbox", { name: "Seleccionar todo" }));
    expect(screen.getByRole("button", { name: "Completar seleccionados (4)" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Quitar de la bandeja (4)" })).toBeInTheDocument();
  });
});
