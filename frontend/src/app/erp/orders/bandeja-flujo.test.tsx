import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
  // La bandeja recuerda los últimos filtros y la vista: cada test parte de cero.
  window.localStorage.clear();
  (listOrders as jest.Mock).mockReset();
  (listOrders as jest.Mock).mockResolvedValue(page([A, B, C, D]));
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

  it("el botón de orden invierte la fecha (desc ↔ asc) y la fecha va destacada en cada fila", async () => {
    const user = userEvent.setup();
    render(<ErpOrdersPage />);
    await screen.findByText("BOPRIN-1");
    expect(within(row("BOPRIN-1")).getByText("8/9/2026")).toHaveClass("erp-flow-date");

    const orden = screen.getByRole("button", { name: "Orden descendente" });
    expect(orden).toHaveTextContent("Fecha ↓");
    await user.click(orden);
    await waitFor(() => expect(ultimaLlamada()).toEqual(expect.objectContaining({ sort: "placed_asc" })));
    expect(screen.getByRole("button", { name: "Orden ascendente" })).toHaveTextContent("Fecha ↑");
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
    await user.click(screen.getByRole("checkbox", { name: "Ver pedidos anulados" }));
    await waitFor(() => expect(ultimaLlamada()).toEqual(expect.objectContaining({ show_cancelled: true })));
    const r = row("MANUAL-000009");
    expect(within(r).getByText("Anulado")).toHaveAttribute(
      "title", expect.stringMatching(/Anulado el 11\/9\/2026 por Bart: duplicado/),
    );
    expect(within(r).getByText("duplicado")).toBeInTheDocument();
    // Excluyente con «Ver ocultados» (como el backend) y sin acciones de bloque.
    expect(screen.getByRole("checkbox", { name: "Ver pedidos ocultados de la bandeja" })).toBeDisabled();
    expect(screen.queryByRole("checkbox", { name: "Seleccionar todo" })).toBeNull();
    // Vuelta a la bandeja normal.
    await user.click(screen.getByRole("checkbox", { name: "Ver pedidos anulados" }));
    await waitFor(() => expect(ultimaLlamada()).toEqual(expect.objectContaining({ show_cancelled: false })));
    expect(screen.getByRole("checkbox", { name: "Ver pedidos ocultados de la bandeja" })).toBeEnabled();
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
    expect(within(row("PRO-3")).getByRole("link", { name: "Vincular empresa a FACTUSOL PRO-3" })).toBeInTheDocument();
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
