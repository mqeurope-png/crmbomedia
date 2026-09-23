import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ErpOrdersPage from "./page";
import { approveOrder, approveOrdersBulk, listOrders } from "../../lib/erpApi";

/** ERP · rediseño de flujo (Fase 1) — la BANDEJA DE TRABAJO.
 *
 *  Lo que se comprueba aquí es lo que cambia de verdad: que el trabajo se
 *  organiza por colas (con su contador), que cada pedido enseña la acción que
 *  toca y su alerta, y que NO se ha perdido ninguna de las acciones ni de los
 *  filtros que ya existían. */

// `?queue=` de la URL (Lote 2 D): cada test lo fija antes de montar.
let search = "";
jest.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(search),
}));
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
  WORKFLOW_QUEUES: [
    "por_revisar", "por_facturar", "por_cobrar", "por_enviar", "incidencias", "listo",
  ],
  customerLabel: (o: { contact_name?: string | null; company_name?: string | null }) =>
    [o.contact_name, o.company_name].filter(Boolean).join(" · "),
  listOrders: jest.fn(),
  approveOrder: jest.fn(),
  approveOrdersBulk: jest.fn(),
  completeOrder: jest.fn(),
  uncompleteOrder: jest.fn(),
  completeOrdersBulk: jest.fn(),
  excludeSeguimiento: jest.fn(),
  includeSeguimiento: jest.fn(),
  previewExcludeSeguimiento: jest.fn(),
  refreshOrdersFactusolCobro: jest.fn(),
  getErpSettings: jest.fn(() => Promise.resolve({
    woocommerce_stores: [
      { slug: "artisjet", label: "artisJet" },
      { slug: "boprint", label: "BoPrint" },
      { slug: "fluxlasers", label: "Flux Lasers" },
    ],
  })),
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
  preparation_status: "in_queue", approved_at: "2026-09-09T10:00:00",
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

/** El Nº de cada pedido VISIBLE, en el orden en que salen en el DOM — vale
 *  igual para Tarjetas (`<article>`) que para Lista (`<tr>`). */
function ordenVisible(): string[] {
  return Array.from(document.querySelectorAll("[data-order-row]"))
    .map((el) => el.getAttribute("data-order-row") as string);
}

async function abrirMenu(user: ReturnType<typeof userEvent.setup>, numero: string) {
  await user.click(screen.getByRole("button", { name: `Más acciones ${numero}` }));
}

beforeEach(() => {
  // La bandeja recuerda los últimos filtros y la vista: cada test parte de cero.
  window.localStorage.clear();
  window.history.replaceState({}, "", "/erp/orders");
  search = "";
  (listOrders as jest.Mock).mockReset();
  (listOrders as jest.Mock).mockResolvedValue(page([A, B, C, D]));
  (approveOrder as jest.Mock).mockReset();
  (approveOrdersBulk as jest.Mock).mockReset();
  jest.restoreAllMocks();
});

function ultimaLlamada() {
  const calls = (listOrders as jest.Mock).mock.calls;
  return calls[calls.length - 1][0];
}

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

    // Pendiente de aprobar: se aprueba aquí mismo (Lote 2 D), sin ir a otra pantalla.
    expect(within(row("BOPRIN-1")).getByRole("button", { name: "Aprobar BOPRIN-1" }))
      .toHaveTextContent("Aprobar");

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
    // Lote 2 A2: «Ver ocultados» / «Ver anulados» viven en un control excluyente.
    const ver = within(screen.getByRole("group", { name: "Ver" }));
    expect(ver.getByRole("button", { name: "Ver activos" })).toHaveAttribute("aria-pressed", "true");
    expect(ver.getByRole("button", { name: "Ver ocultados" })).toHaveAttribute("aria-pressed", "false");
    expect(ver.getByRole("button", { name: "Ver anulados" })).toHaveAttribute("aria-pressed", "false");
    // Lote 2 D: la Cola PEDIDOS ya no es una pantalla aparte (ni enlace a ella).
    expect(screen.queryByRole("link", { name: "Cola PEDIDOS" })).toBeNull();
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

// --- Lote B7: defaults, filtros nuevos, pastillas, vista lista, anulados ------

describe("ERP · Bandeja (Lote B7) — defaults reversibles y chips", () => {
  it("al entrar se piden los pagados sin completar y se ven como chips; «Limpiar» lo deja todo en «todos»", async () => {
    const user = userEvent.setup();
    render(<ErpOrdersPage />);
    await screen.findByText("BOPRIN-1");
    expect(listOrders).toHaveBeenCalledTimes(1);
    expect(listOrders).toHaveBeenLastCalledWith(expect.objectContaining({
      completed: false, payment: "paid", sort: "placed_desc", show_cancelled: false,
    }));
    expect(screen.getByRole("combobox", { name: "Filtro pago" })).toHaveValue("paid");
    expect(screen.getByRole("combobox", { name: "Filtro completado" })).toHaveValue("no");
    expect(screen.getByRole("button", { name: "Eliminar filtro Pago: Pagado" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Eliminar filtro Sin completar" })).toBeInTheDocument();
    // Con los defaults puestos no hace falta «Por defecto».
    expect(screen.queryByRole("button", { name: "Por defecto" })).toBeNull();

    await user.click(screen.getByRole("button", { name: "Limpiar filtros" }));
    await waitFor(() => expect(listOrders).toHaveBeenLastCalledWith(expect.objectContaining({
      completed: undefined, payment: undefined, invoiced: undefined, store_slug: undefined,
      placed_from: undefined, placed_to: undefined, queue: undefined, sort: "placed_desc",
    })));
    expect(screen.queryByRole("button", { name: /Eliminar filtro/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "Limpiar filtros" })).toBeNull();
    expect(screen.getByText("Sin filtros de refinamiento.")).toBeInTheDocument();

    // Y vuelta a los defaults con un clic.
    await user.click(screen.getByRole("button", { name: "Por defecto" }));
    await waitFor(() => expect(listOrders).toHaveBeenLastCalledWith(expect.objectContaining({
      completed: false, payment: "paid",
    })));
  });

  it("cada chip se quita con su × y los últimos filtros se recuerdan al volver", async () => {
    const user = userEvent.setup();
    const { unmount } = render(<ErpOrdersPage />);
    await screen.findByText("BOPRIN-1");
    await user.click(screen.getByRole("button", { name: "Eliminar filtro Pago: Pagado" }));
    await waitFor(() => expect(listOrders).toHaveBeenLastCalledWith(expect.objectContaining({
      payment: undefined, completed: false,
    })));
    expect(screen.getByRole("combobox", { name: "Filtro pago" })).toHaveValue("");
    unmount();

    // Segunda visita: sin «Pago: Pagado» pero con «Sin completar» (lo último que dejó).
    (listOrders as jest.Mock).mockClear();
    render(<ErpOrdersPage />);
    await screen.findByText("BOPRIN-1");
    expect(listOrders).toHaveBeenCalledTimes(1);
    expect(ultimaLlamada()).toEqual(expect.objectContaining({ payment: undefined, completed: false }));
    expect(screen.queryByRole("button", { name: "Eliminar filtro Pago: Pagado" })).toBeNull();
    expect(screen.getByRole("button", { name: "Eliminar filtro Sin completar" })).toBeInTheDocument();
  });
});

describe("ERP · Bandeja (Lote B7) — filtros nuevos y orden", () => {
  it("facturado, tienda y fechas se mandan al backend y salen como chips", async () => {
    const user = userEvent.setup();
    render(<ErpOrdersPage />);
    await screen.findByText("BOPRIN-1");

    await user.selectOptions(screen.getByRole("combobox", { name: "Filtro facturado" }), "yes");
    await waitFor(() => expect(ultimaLlamada()).toEqual(expect.objectContaining({ invoiced: true })));
    await user.selectOptions(screen.getByRole("combobox", { name: "Filtro facturado" }), "no");
    await waitFor(() => expect(ultimaLlamada()).toEqual(expect.objectContaining({ invoiced: false })));
    expect(screen.getByRole("button", { name: "Eliminar filtro Sin facturar" })).toBeInTheDocument();

    // Las tiendas vienen de los ajustes (slug → etiqueta).
    const tienda = screen.getByRole("combobox", { name: "Filtro tienda" });
    await waitFor(() => expect(tienda).toHaveTextContent("BoPrint"));
    await user.selectOptions(tienda, "boprint");
    await waitFor(() => expect(ultimaLlamada()).toEqual(expect.objectContaining({ store_slug: "boprint" })));
    expect(screen.getByRole("button", { name: "Eliminar filtro Tienda: BoPrint" })).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Fecha desde"), { target: { value: "2026-09-01" } });
    await waitFor(() => expect(ultimaLlamada()).toEqual(expect.objectContaining({ placed_from: "2026-09-01" })));
    fireEvent.change(screen.getByLabelText("Fecha hasta"), { target: { value: "2026-09-15" } });
    await waitFor(() => expect(ultimaLlamada()).toEqual(expect.objectContaining({
      placed_from: "2026-09-01", placed_to: "2026-09-15",
    })));
    expect(screen.getByRole("button", { name: "Eliminar filtro Desde 1/9/2026" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Eliminar filtro Hasta 15/9/2026" })).toBeInTheDocument();

    // Quitar el chip de la tienda deja el resto.
    await user.click(screen.getByRole("button", { name: "Eliminar filtro Tienda: BoPrint" }));
    await waitFor(() => expect(ultimaLlamada()).toEqual(expect.objectContaining({
      store_slug: undefined, invoiced: false, placed_from: "2026-09-01",
    })));
  });

  it("A2: «Ordenar por» + el botón de sentido invierten la fecha (desc ↔ asc) y la fecha va destacada en cada fila", async () => {
    const user = userEvent.setup();
    render(<ErpOrdersPage />);
    await screen.findByText("BOPRIN-1");
    expect(within(row("BOPRIN-1")).getByText("8/9/2026")).toHaveClass("erp-flow-date");

    // Por defecto: Fecha, descendente.
    expect(screen.getByRole("combobox", { name: "Ordenar por" })).toHaveValue("fecha");
    const orden = screen.getByRole("button", { name: "Orden descendente" });
    expect(orden).toHaveTextContent("↓ desc");
    await user.click(orden);
    await waitFor(() => expect(ultimaLlamada()).toEqual(expect.objectContaining({ sort: "placed_asc" })));
    expect(screen.getByRole("button", { name: "Orden ascendente" })).toHaveTextContent("↑ asc");
    await user.click(screen.getByRole("button", { name: "Orden ascendente" }));
    await waitFor(() => expect(ultimaLlamada()).toEqual(expect.objectContaining({ sort: "placed_desc" })));
  });

  it("«Ver anulados» pide solo los anulados y la fila enseña el badge con motivo y quién", async () => {
    (listOrders as jest.Mock).mockImplementation((f?: { show_cancelled?: boolean }) =>
      Promise.resolve(page(f?.show_cancelled ? [order({
        id: "o-9", order_number: "MANUAL-000009", external_source: "manual",
        cancelled: true, cancelled_at: "2026-09-11T09:00:00", cancelled_reason: "duplicado",
        cancelled_by_name: "Bart", workflow: wf({ queue: "listo", queue_label: "Listo", next_action: "ninguna" }),
      })] : [A, B, C, D])),
    );
    const user = userEvent.setup();
    render(<ErpOrdersPage />);
    await screen.findByText("BOPRIN-1");
    await user.click(screen.getByRole("button", { name: "Ver anulados" }));
    await waitFor(() => expect(ultimaLlamada()).toEqual(expect.objectContaining({
      show_cancelled: true, show_excluded: false,
    })));
    const r = row("MANUAL-000009");
    expect(within(r).getByText("Anulado")).toHaveAttribute(
      "title", expect.stringMatching(/Anulado el 11\/9\/2026 por Bart: duplicado/),
    );
    expect(within(r).getByText("duplicado")).toBeInTheDocument();
    // Excluyente con «Ver ocultados» (como el backend) y sin acciones de bloque.
    expect(screen.getByRole("button", { name: "Ver anulados" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Ver ocultados" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByRole("checkbox", { name: "Mostrar procesados externamente" })).toBeDisabled();
    expect(screen.queryByRole("checkbox", { name: "Seleccionar todo" })).toBeNull();
    // Vuelta a la bandeja normal.
    await user.click(screen.getByRole("button", { name: "Ver activos" }));
    await waitFor(() => expect(ultimaLlamada()).toEqual(expect.objectContaining({
      show_cancelled: false, show_excluded: false,
    })));
    expect(screen.getByRole("checkbox", { name: "Mostrar procesados externamente" })).toBeEnabled();
  });

  it("«Ver ocultados» y «Ver anulados» son excluyentes: elegir uno quita el otro y nunca van los dos flags", async () => {
    const user = userEvent.setup();
    render(<ErpOrdersPage />);
    await screen.findByText("BOPRIN-1");
    await user.click(screen.getByRole("button", { name: "Ver ocultados" }));
    await waitFor(() => expect(ultimaLlamada()).toEqual(expect.objectContaining({
      show_excluded: true, show_cancelled: false,
    })));
    expect(screen.getByRole("button", { name: "Ver ocultados" })).toHaveAttribute("aria-pressed", "true");
    // Pasar a anulados deja de pedir ocultados (un solo flag).
    await user.click(screen.getByRole("button", { name: "Ver anulados" }));
    await waitFor(() => expect(ultimaLlamada()).toEqual(expect.objectContaining({
      show_excluded: false, show_cancelled: true,
    })));
    expect(screen.getByRole("button", { name: "Ver ocultados" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByRole("button", { name: "Ver anulados" })).toHaveAttribute("aria-pressed", "true");
    // Nunca se ha mandado show_excluded y show_cancelled a la vez.
    for (const [args] of (listOrders as jest.Mock).mock.calls) {
      expect(args.show_excluded && args.show_cancelled).toBeFalsy();
    }
    // «Limpiar filtros» vuelve a activos.
    await user.click(screen.getByRole("button", { name: "Limpiar filtros" }));
    await waitFor(() => expect(ultimaLlamada()).toEqual(expect.objectContaining({
      show_excluded: false, show_cancelled: false,
    })));
    expect(screen.getByRole("button", { name: "Ver activos" })).toHaveAttribute("aria-pressed", "true");
  });
});

// --- Lote 2 D: la Cola PEDIDOS es la cola «Por revisar» de la bandeja --------

describe("ERP · Bandeja (Lote 2 D) — aprobar aquí mismo y cola desde la URL", () => {
  it("«?queue=por_revisar» preselecciona la cola al entrar (la URL manda) y otras colas también", async () => {
    search = "queue=por_revisar";
    render(<ErpOrdersPage />);
    await screen.findByText("BOPRIN-1");
    expect(listOrders).toHaveBeenCalledTimes(1);
    expect(ultimaLlamada()).toEqual(expect.objectContaining({ queue: "por_revisar" }));
    expect(screen.getByRole("button", { name: "Por revisar (4)" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Ver todas las colas" })).toBeInTheDocument();
    // Los filtros recordados siguen (solo la cola viene de la URL).
    expect(ultimaLlamada()).toEqual(expect.objectContaining({ payment: "paid", completed: false }));
  });

  it("un valor de cola desconocido en la URL se ignora", async () => {
    search = "queue=lo-que-sea";
    render(<ErpOrdersPage />);
    await screen.findByText("BOPRIN-1");
    expect(ultimaLlamada()).toEqual(expect.objectContaining({ queue: undefined }));
    expect(screen.queryByRole("button", { name: "Ver todas las colas" })).toBeNull();
  });

  it("«Aprobar» en la fila llama a approveOrder, avisa y recarga; con bloqueos enseña el motivo", async () => {
    (approveOrder as jest.Mock).mockResolvedValueOnce({ id: "o-1", preparation_status: "in_queue" });
    const user = userEvent.setup();
    render(<ErpOrdersPage />);
    await screen.findByText("BOPRIN-1");
    const calls = (listOrders as jest.Mock).mock.calls.length;
    await user.click(within(row("BOPRIN-1")).getByRole("button", { name: "Aprobar BOPRIN-1" }));
    await waitFor(() => expect(approveOrder).toHaveBeenCalledWith("o-1"));
    expect(await screen.findByRole("status")).toHaveTextContent("BOPRIN-1 aprobado: pasa a la cola del taller (SAT).");
    await waitFor(() => expect((listOrders as jest.Mock).mock.calls.length).toBe(calls + 1));

    // 409 `blocked`: el mensaje llega ya formateado con los bloqueos.
    (approveOrder as jest.Mock).mockRejectedValueOnce(new Error("Bloqueado: 1 excepción(es) sin resolver"));
    await user.click(within(row("BOPRIN-1")).getByRole("button", { name: "Aprobar BOPRIN-1" }));
    await waitFor(() => expect(document.querySelector(".form-error")).toHaveTextContent(
      "No se pudo aprobar BOPRIN-1: Bloqueado: 1 excepción(es) sin resolver",
    ));
  });

  it("«Aprobar seleccionados (n)» cuenta solo los pendientes de revisión, confirma, llama a approveOrdersBulk e informa los fallos", async () => {
    const confirm = jest.spyOn(window, "confirm").mockReturnValue(true);
    (approveOrdersBulk as jest.Mock).mockResolvedValue({
      ok: false, approved: 1, already_approved: 0,
      failed: [{ order_id: "o-3", code: "blocked", error: "Bloqueado: 1 excepción(es) sin resolver" }],
      items: [{ ...A, preparation_status: "in_queue" }],
    });
    const user = userEvent.setup();
    render(<ErpOrdersPage />);
    await screen.findByText("BOPRIN-1");
    expect(screen.queryByRole("button", { name: /Aprobar seleccionados/ })).toBeNull();
    // A, C pendientes de revisión; D ya está aprobado (en cola, facturado): no cuenta.
    await user.click(screen.getByRole("checkbox", { name: "Seleccionar BOPRIN-1" }));
    await user.click(screen.getByRole("checkbox", { name: "Seleccionar PRO-3" }));
    await user.click(screen.getByRole("checkbox", { name: "Seleccionar BOPRIN-4" }));
    expect(screen.getByRole("button", { name: "Completar seleccionados (3)" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Aprobar seleccionados (2)" }));
    expect(confirm).toHaveBeenCalledWith(expect.stringMatching(/¿Aprobar 2 pedido\(s\)\?/));
    expect(confirm).toHaveBeenCalledWith(expect.stringMatching(/1 de los seleccionados no está\(n\) pendiente\(s\) de revisión/));
    await waitFor(() => expect(approveOrdersBulk).toHaveBeenCalledWith(["o-1", "o-3"]));
    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent("1 pedido(s) aprobado(s): pasan a la cola del taller (SAT)");
    expect(status).toHaveTextContent("No se pudo aprobar 1: PRO-3: Bloqueado: 1 excepción(es) sin resolver");
    expect(document.querySelector(".form-error")).toBeNull();
    // Recarga (los aprobados cambian de cola) y limpia la selección.
    await waitFor(() => expect((listOrders as jest.Mock).mock.calls.length).toBeGreaterThanOrEqual(2));
    expect(screen.queryByRole("button", { name: /Aprobar seleccionados/ })).toBeNull();
  });

  it("si se cancela la confirmación no se aprueba nada; sin pendientes de revisión no hay botón", async () => {
    jest.spyOn(window, "confirm").mockReturnValue(false);
    const user = userEvent.setup();
    render(<ErpOrdersPage />);
    await screen.findByText("BOPRIN-1");
    await user.click(screen.getByRole("checkbox", { name: "Seleccionar BOPRIN-1" }));
    await user.click(screen.getByRole("button", { name: "Aprobar seleccionados (1)" }));
    expect(approveOrdersBulk).not.toHaveBeenCalled();
    // Solo D (in_queue) seleccionado: «Completar» sí, «Aprobar» no.
    await user.click(screen.getByRole("checkbox", { name: "Seleccionar BOPRIN-1" }));
    await user.click(screen.getByRole("checkbox", { name: "Seleccionar BOPRIN-4" }));
    expect(screen.getByRole("button", { name: "Completar seleccionados (1)" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Aprobar seleccionados/ })).toBeNull();
  });
});

describe("ERP · Bandeja (Lote B7) — pastillas de estado y origen", () => {
  it("cada pedido lleva las cuatro pastillas: verde lo que se cumple, gris lo que no", async () => {
    render(<ErpOrdersPage />);
    await screen.findByText("BOPRIN-1");
    // A: pagado, sin factura, sin cobro, sin completar.
    const a = within(row("BOPRIN-1"));
    expect(a.getByLabelText("Pagado: sí")).toHaveClass("is-on");
    expect(a.getByLabelText("Facturado: no")).toHaveClass("is-off");
    expect(a.getByLabelText("Cobro registrado: no")).toHaveClass("is-off");
    expect(a.getByLabelText("Completado: no")).toHaveClass("is-off");
    // D: facturada, cobro pendiente en FACTUSOL (la pastilla gris + el badge de detalle).
    const dd = within(row("BOPRIN-4"));
    expect(dd.getByLabelText("Facturado: sí")).toHaveClass("is-on");
    expect(dd.getByLabelText("Cobro registrado: no")).toHaveClass("is-off");
    expect(dd.getByText("Pendiente de cobro FACTUSOL")).toBeInTheDocument();
    // C: bloqueada → badge «Bloqueado» además de la alerta.
    expect(within(row("PRO-3")).getByText("Bloqueado")).toHaveClass("badge", "bad");
  });

  it("la pastilla de origen dice la tienda web (por el prefijo del nº) o «manual»", async () => {
    render(<ErpOrdersPage />);
    await screen.findByText("BOPRIN-1");
    await waitFor(() => expect(within(row("BOPRIN-1")).getByText("BoPrint")).toHaveClass("erp-flow-src", "is-woo"));
    expect(within(row("ARTISJ-2")).getByText("artisJet")).toBeInTheDocument();
    expect(within(row("PRO-3")).getByText("manual")).toHaveClass("is-man");
  });
});

describe("ERP · Bandeja (Lote B7) — vista lista", () => {
  it("«Lista» pinta la tabla con las mismas acciones y selección, y se recuerda", async () => {
    const user = userEvent.setup();
    render(<ErpOrdersPage />);
    await screen.findByText("BOPRIN-1");
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.getByRole("button", { name: "Tarjetas" })).toHaveAttribute("aria-pressed", "true");

    await user.click(screen.getByRole("button", { name: "Lista" }));
    const table = screen.getByRole("table");
    for (const h of ["Nº", "Cliente", "Tienda", "Importe", "Estado", "Cola · siguiente paso", "Acciones"]) {
      expect(within(table).getByRole("columnheader", { name: h })).toBeInTheDocument();
    }
    expect(within(table).getByRole("columnheader", { name: /Fecha/ })).toHaveAttribute("aria-sort", "descending");
    expect(within(table).getAllByRole("row")).toHaveLength(5);   // cabecera + 4
    expect(window.localStorage.getItem("erp.bandeja.vista")).toBe("list");

    // Misma información y mismas acciones que la tarjeta.
    const dd = row("BOPRIN-4");
    expect(dd.tagName).toBe("TR");
    expect(within(dd).getByRole("link", { name: "BOPRIN-4" })).toHaveAttribute("href", "/erp/orders/o-4");
    expect(within(dd).getByText("Bruno García · Grabados FG")).toBeInTheDocument();
    expect(within(dd).getByText("8/9/2026")).toBeInTheDocument();
    expect(within(dd).getByText("100.00 EUR")).toBeInTheDocument();
    expect(within(dd).getByLabelText("Facturado: sí")).toBeInTheDocument();
    expect(within(dd).getByText("Por cobrar")).toBeInTheDocument();
    expect(within(dd).getByRole("button", { name: "Registrar cobro BOPRIN-4" })).toBeInTheDocument();
    await user.click(within(dd).getByRole("button", { name: "Más acciones BOPRIN-4" }));
    expect(screen.getByRole("button", { name: "Quitar BOPRIN-4 de la bandeja" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Marcar completado BOPRIN-4" })).toBeInTheDocument();
    // Lote 2 A1: el botón principal de la columna ACCIONES lleva su etiqueta
    // visible dentro de la fila (enlace-botón y botón), con la misma pinta
    // `.button` que en la tarjeta (el color lo arregla el CSS de la tabla).
    const accionC = within(row("PRO-3")).getByRole("link", { name: "Vincular empresa a FACTUSOL PRO-3" });
    expect(accionC).toHaveTextContent("Vincular empresa a FACTUSOL");
    expect(accionC).toHaveClass("button", "small");
    expect(accionC.closest("td")).not.toBeNull();
    const accionA = within(row("BOPRIN-1")).getByRole("button", { name: "Aprobar BOPRIN-1" });
    expect(accionA).toHaveTextContent("Aprobar");
    expect(accionA).toHaveClass("button", "small");
    expect(within(row("ARTISJ-2")).getByRole("link", { name: "Emitir factura ARTISJ-2" }))
      .toHaveTextContent("Emitir factura");
    expect(row("PRO-3").className).toContain("is-alert");

    // Selección múltiple y acciones de bloque, igual que en tarjetas.
    await user.click(within(dd).getByRole("checkbox", { name: "Seleccionar BOPRIN-4" }));
    await user.click(within(row("BOPRIN-1")).getByRole("checkbox", { name: "Seleccionar BOPRIN-1" }));
    expect(screen.getByRole("button", { name: "Completar seleccionados (2)" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Quitar de la bandeja (2)" })).toBeInTheDocument();

    // La cabecera «Fecha» también invierte el orden.
    await user.click(within(table).getByRole("button", { name: "Ordenar por fecha (descendente)" }));
    await waitFor(() => expect(ultimaLlamada()).toEqual(expect.objectContaining({ sort: "placed_asc" })));
    expect(screen.getByRole("columnheader", { name: /Fecha/ })).toHaveAttribute("aria-sort", "ascending");
  });

  it("la vista guardada se respeta al volver", async () => {
    window.localStorage.setItem("erp.bandeja.vista", "list");
    render(<ErpOrdersPage />);
    await screen.findByText("BOPRIN-1");
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Lista" })).toHaveAttribute("aria-pressed", "true");
  });
});

// --- Buscador + orden + tipo de pedido (A1/A2/A3) ----------------------------

const W1 = order({
  id: "w-1", order_number: "BOPRIN-10", contact_name: "Ana", company_name: null,
  total_amount: 200, placed_at: "2026-09-20T10:00:00",
  workflow: wf({ queue: "listo", queue_label: "Listo", next_action: "ninguna" }),
});
const M1 = order({
  id: "m-1", order_number: "MANUAL-000005", external_source: "manual",
  contact_name: "Bruno", company_name: null, total_amount: 50,
  placed_at: "2026-09-10T10:00:00",
  workflow: wf({ queue: "por_revisar", queue_label: "Por revisar", next_action: "ninguna" }),
});
const S1 = order({
  id: "s-1", order_number: "MUESTRA-0007", external_source: "manual", order_kind: "sample",
  contact_name: "Carla", company_name: null, total_amount: 0,
  placed_at: "2026-09-23T10:00:00",
  workflow: wf({ queue: "por_enviar", queue_label: "Por enviar", next_action: "ninguna" }),
});
const P1 = order({
  id: "p-1", order_number: "PRO-99", external_source: "factusol_proforma",
  contact_name: "Diego", company_name: null, total_amount: 999,
  placed_at: "2026-09-01T10:00:00",
  workflow: wf({ queue: "por_facturar", queue_label: "Por facturar", next_action: "ninguna" }),
});

describe("ERP · Bandeja — buscador, orden y tipo (A1/A2/A3)", () => {
  beforeEach(() => {
    (listOrders as jest.Mock).mockResolvedValue(page([W1, M1, S1, P1]));
  });

  it("A1: el buscador filtra por cliente y por Nº de pedido, en vivo", async () => {
    const user = userEvent.setup();
    render(<ErpOrdersPage />);
    await screen.findByText("BOPRIN-10");

    await user.type(screen.getByRole("searchbox", { name: "Buscar pedidos" }), "ana");
    expect(ordenVisible()).toEqual(["BOPRIN-10"]);

    await user.clear(screen.getByRole("searchbox", { name: "Buscar pedidos" }));
    await user.type(screen.getByRole("searchbox", { name: "Buscar pedidos" }), "manual-000005");
    expect(ordenVisible()).toEqual(["MANUAL-000005"]);

    // Sin resultados: aviso propio (no confundir con «bandeja vacía»).
    await user.clear(screen.getByRole("searchbox", { name: "Buscar pedidos" }));
    await user.type(screen.getByRole("searchbox", { name: "Buscar pedidos" }), "nadie tiene este nombre");
    expect(screen.getByText("Ningún pedido coincide con la búsqueda o el filtro de tipo.")).toBeInTheDocument();

    // No llama otra vez al backend: es un filtro EN VIVO sobre lo cargado.
    expect(listOrders).toHaveBeenCalledTimes(1);
  });

  it("A2: ordena por Fecha, Importe, Cliente y Situación, igual en Tarjetas y en Lista; por defecto Fecha desc", async () => {
    const user = userEvent.setup();
    render(<ErpOrdersPage />);
    await screen.findByText("BOPRIN-10");

    // Por defecto: Fecha, la más reciente primero.
    expect(screen.getByRole("combobox", { name: "Ordenar por" })).toHaveValue("fecha");
    expect(ordenVisible()).toEqual(["MUESTRA-0007", "BOPRIN-10", "MANUAL-000005", "PRO-99"]);

    await user.selectOptions(screen.getByRole("combobox", { name: "Ordenar por" }), "importe");
    expect(ordenVisible()).toEqual(["PRO-99", "BOPRIN-10", "MANUAL-000005", "MUESTRA-0007"]);

    await user.selectOptions(screen.getByRole("combobox", { name: "Ordenar por" }), "cliente");
    expect(ordenVisible()).toEqual(["PRO-99", "MUESTRA-0007", "MANUAL-000005", "BOPRIN-10"]);

    await user.selectOptions(screen.getByRole("combobox", { name: "Ordenar por" }), "situacion");
    expect(ordenVisible()).toEqual(["BOPRIN-10", "MUESTRA-0007", "PRO-99", "MANUAL-000005"]);

    // El mismo orden en la vista Lista.
    await user.click(screen.getByRole("button", { name: "Lista" }));
    expect(ordenVisible()).toEqual(["BOPRIN-10", "MUESTRA-0007", "PRO-99", "MANUAL-000005"]);
  });

  it("A3: el filtro de tipo usa el mismo origen que las tarjetas (WEB/Manual/Muestra/Proforma)", async () => {
    const user = userEvent.setup();
    render(<ErpOrdersPage />);
    await screen.findByText("BOPRIN-10");

    await user.click(screen.getByRole("button", { name: "Filtro tipo Muestra" }));
    expect(ordenVisible()).toEqual(["MUESTRA-0007"]);
    expect(screen.getByRole("button", { name: "Filtro tipo Muestra" })).toHaveAttribute("aria-pressed", "true");
    await user.click(screen.getByRole("button", { name: "Filtro tipo Muestra" }));

    await user.click(screen.getByRole("button", { name: "Filtro tipo WEB" }));
    expect(ordenVisible()).toEqual(["BOPRIN-10"]);
    await user.click(screen.getByRole("button", { name: "Filtro tipo WEB" }));

    await user.click(screen.getByRole("button", { name: "Filtro tipo Proforma" }));
    expect(ordenVisible()).toEqual(["PRO-99"]);
    await user.click(screen.getByRole("button", { name: "Filtro tipo Proforma" }));

    await user.click(screen.getByRole("button", { name: "Filtro tipo Manual" }));
    expect(ordenVisible()).toEqual(["MANUAL-000005"]);

    // Multiselección: Manual + Muestra a la vez.
    await user.click(screen.getByRole("button", { name: "Filtro tipo Muestra" }));
    expect([...ordenVisible()].sort()).toEqual(["MANUAL-000005", "MUESTRA-0007"].sort());

    // No llama otra vez al backend.
    expect(listOrders).toHaveBeenCalledTimes(1);
  });
});
