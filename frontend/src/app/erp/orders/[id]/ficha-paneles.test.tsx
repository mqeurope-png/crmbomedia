import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ErpOrderDetailPage from "./page";
import { getOrder, getOrderFactusolCobro, getOrderTimeline } from "../../../lib/erpApi";

/** ERP · Lote 2 · PR-2 — la ficha de pedido como molde del sistema:
 *
 *  - Paneles plegables (Líneas · Envío y seguimiento · Historial) con un
 *    resumen en la cabecera y memoria por usuario (localStorage). Por defecto
 *    se abre el del paso actual; desde 1280 px, todos.
 *  - «Pendiente de cobro» destacado dentro del resumen económico (el saldo
 *    en vivo de FACTUSOL o, si no, el persistido).
 *  - En móvil, la acción del paso actual pegada abajo (PrimaryActionBar),
 *    una sola vez. */

jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ children, href, className }: {
    children: React.ReactNode; href: string; className?: string;
  }) => <a href={href} className={className}>{children}</a>,
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
jest.mock("../../../components/erp/FactusolDocumentDetailModal", () => ({
  PDF_LANGS: [{ value: "es", label: "Español" }],
}));
jest.mock("../../../components/erp/InvoiceEmailModal", () => ({ InvoiceEmailModal: () => null }));
jest.mock("../../../components/erp/OrderEmailModal", () => ({ OrderEmailModal: () => null }));
jest.mock("../../../components/erp/RegistrarCobroModal", () => ({ RegistrarCobroModal: () => null }));
jest.mock("../../../components/erp/EmitFactusolButton", () => ({
  EmitFactusolButton: ({ buttonHidden }: { buttonHidden?: boolean }) => (
    buttonHidden ? null : <button type="button">Emitir factura FACTUSOL</button>
  ),
}));
jest.mock("../../../components/erp/OrderStatusMachine", () => ({
  OrderStatusMachine: () => null,
}));
jest.mock("../../../components/erp/ShippingFilesSection", () => ({
  ShippingFilesSection: () => <div>documentos de envío</div>,
}));
// #6 se prueba aparte (ficha-cliente-factusol): aquí solo importa que no
// arrastre la lectura de la empresa ni el panel real de la ficha de empresa.
jest.mock("../../../components/erp/OrderFactusolClientPanel", () => ({
  OrderFactusolClientPanel: () => null,
}));
jest.mock("../../../lib/api", () => ({
  getCurrentUser: jest.fn(() => Promise.resolve({ role: "admin" })),
}));
jest.mock("../../../lib/erpApi", () => ({
  ERP_EDIT_ROLES: ["admin", "pedidos"],
  DOMAIN_LABELS: {},
  STATUS_LABELS: {},
  customerLabel: () => "Rotulación Levante S.L.",
  resolveOrderCobroStatus: (
    o: { factusol_cobro_status?: string | null },
    live: { status?: string | null } | null | undefined,
  ) => {
    const s = live?.status === "cobrada" || live?.status === "pendiente" ? live.status : null;
    return s ?? o.factusol_cobro_status ?? null;
  },
  getOrder: jest.fn(),
  getOrderTimeline: jest.fn(),
  getFactusolStatus: jest.fn(() => Promise.resolve({ status: "none" })),
  getErpSettings: jest.fn(() => Promise.resolve({ shipping_origins: [] })),
  getOrderFactusolInvoiceRef: jest.fn(),
  getOrderFactusolCobro: jest.fn(),
  downloadOrderFactusolPedidoPdf: jest.fn(),
  createOrderAlbaran: jest.fn(),
  fireTransition: jest.fn(),
  saveBlob: jest.fn(),
  updateOrderLanguage: jest.fn(),
  updateOrderSeguimiento: jest.fn(),
  completeOrder: jest.fn(),
  uncompleteOrder: jest.fn(),
}));

/** Ancho de pantalla simulado: el molde lo lee con `matchMedia`. */
let viewport: "mobile" | "medium" | "wide" = "medium";
function fakeMatchMedia(query: string): MediaQueryList {
  const matches = query.includes("min-width: 1280px")
    ? viewport === "wide"
    : query.includes("max-width: 767px") ? viewport === "mobile" : false;
  return {
    matches, media: query, onchange: null,
    addListener: () => undefined, removeListener: () => undefined,
    addEventListener: () => undefined, removeEventListener: () => undefined,
    dispatchEvent: () => false,
  } as unknown as MediaQueryList;
}

const H = 3600 * 1000;
const STEPS = [
  { key: "creado", label: "Creado", state: "done", detail: "2026-09-12" },
  { key: "pagado", label: "Pagado", state: "done", detail: "408.00 EUR" },
  { key: "aprobado", label: "Aprobado", state: "done", detail: "2026-09-12" },
  { key: "albaran", label: "Albarán", state: "done", detail: "2-100418" },
  { key: "factura", label: "Factura", state: "now", detail: null },
  { key: "cobro", label: "Cobro", state: "pending", detail: null },
  { key: "enviado", label: "Enviado", state: "pending", detail: null },
];

function line(id: string, codart: string | null) {
  return {
    id, position: 0, product_sku: `sku-${id}`, product_codart: codart,
    description: `Artículo ${id}`, quantity: 1, unit_price: 100, tax_rate: 21,
    line_total: 100, notes: null,
  };
}

function detail(over = {}) {
  return {
    id: "o-1", order_number: "BP-2479", contact_name: null,
    company_name: "Rotulación Levante S.L.", external_source: "manual",
    store_id: null, contact_id: null, company_id: "c-1",
    // 3 líneas de 100 (base 300) + IVA 21 % (63) + portes 45 = 408.
    total_amount: 408, currency: "EUR", payment_status: "paid",
    preparation_status: "in_queue", transport_status: "not_shipped",
    invoice_status: "not_invoiced", tracking_number: null, factusol_invoice_number: null,
    factusol_albaran_number: "2-100418", factusol_cobro_status: null, factusol_cobro: null,
    serial_number: null, whiterip_license: null, shipping_origin: null, language: "es",
    approved_at: "2026-09-12T10:00:00", placed_at: "2026-09-12T09:14:00",
    created_at: "2026-09-12T09:14:00", externally_processed_at: null,
    externally_processed_note: null, externally_processed_by_user_id: null,
    notes: null, packing: null,
    lines: [line("1", "A1"), line("2", "A2"), line("3", null)],
    status_history: [], exceptions: [], blockers: [],
    available_transitions: { payment: [], invoice: [], preparation: [], transport: [] },
    warnings: [], completed: false, completed_at: null, completed_by_user_id: null,
    completed_by_name: null,
    workflow: {
      queue: "por_facturar", queue_label: "Por facturar",
      next_action: "emitir_factura", next_action_label: "Emitir factura",
      next_action_hint: "Emite la factura en FACTUSOL.",
      blocked: false, regime: "nacional", steps: STEPS, alerts: [],
      company: { id: "c-1", name: "Rotulación Levante S.L.", country: "ES", factusol_id: "1043", regime: "nacional" },
    },
    ...over,
  };
}

/** Pedido ya facturado y pendiente de cobro (cola «Por cobrar»). */
function facturado(over = {}) {
  return detail({
    invoice_status: "invoiced_by_erp", factusol_invoice_number: "260063",
    factusol_cobro_status: "pendiente",
    factusol_cobro: {
      numero: "5-260063", serie: 5, codigo: 260063, total: 408, total_cobrado: 0,
      saldo_pendiente: 408, estfac: "0", cobros: 0, cobrada: false,
      checked_at: "2026-09-13T09:00:00Z", source: "live",
    },
    workflow: {
      ...detail().workflow, queue: "por_cobrar", queue_label: "Por cobrar",
      next_action: "registrar_cobro", next_action_label: "Registrar cobro",
      next_action_hint: "El pedido consta pagado: registra el cobro en FACTUSOL.",
      steps: STEPS.map((s) => (s.key === "factura"
        ? { ...s, state: "done", detail: "260063" }
        : s.key === "cobro" ? { ...s, state: "now" } : s)),
    },
    ...over,
  });
}

function withOrder(over = {}) {
  (getOrder as jest.Mock).mockResolvedValue(detail(over));
}

/** El `<details>` de un panel plegable, por su nombre accesible. */
function panel(name: string): HTMLDetailsElement {
  return screen.getByRole("group", { name }) as HTMLDetailsElement;
}

beforeEach(() => {
  viewport = "medium";
  window.matchMedia = fakeMatchMedia;
  window.localStorage.clear();
  (getOrder as jest.Mock).mockReset();
  (getOrderFactusolCobro as jest.Mock).mockReset();
  (getOrderFactusolCobro as jest.Mock).mockResolvedValue({
    order_id: "o-1", status: "pendiente",
    invoice: { serie: 5, codigo: 260063, numero: "5-260063" },
    saldo_pendiente: 300, total_cobrado: 108,
  });
  (getOrderTimeline as jest.Mock).mockReset();
  (getOrderTimeline as jest.Mock).mockResolvedValue({
    total: 2,
    items: [
      { type: "status", at: new Date(Date.now() - 26 * H).toISOString(), title: "Creado", detail: {}, actor_user_id: null },
      { type: "audit", at: new Date(Date.now() - 2 * H).toISOString(), title: "Aprobado", detail: {}, actor_user_id: null },
    ],
  });
});

describe("ERP · Ficha (Lote 2 · PR-2) — paneles plegables con resumen y memoria", () => {
  it("cada panel resume lo esencial en su cabecera («3 artículos · 1 sin mapear · portes 45.00 EUR», «Sin expedición · albarán…», «2 eventos · último hace 2 h»)", async () => {
    withOrder();
    render(<ErpOrderDetailPage />);
    await screen.findByRole("heading", { name: "Líneas" });
    expect(panel("Líneas")).toHaveTextContent("3 artículos · 1 sin mapear · portes 45.00 EUR");
    expect(panel("Envío y seguimiento")).toHaveTextContent("Sin expedición · albarán 2-100418");
    await waitFor(() => expect(panel("Historial")).toHaveTextContent("2 eventos · último hace 2 h"));
    // Y nada se pierde: dentro siguen la tabla de líneas, los documentos de
    // envío, el seguimiento y la lista de eventos.
    expect(within(panel("Líneas")).getByRole("table")).toBeInTheDocument();
    expect(within(panel("Envío y seguimiento")).getByText("documentos de envío")).toBeInTheDocument();
    expect(within(panel("Envío y seguimiento")).getByRole("heading", { name: "Seguimiento" })).toBeInTheDocument();
    expect(within(panel("Historial")).getAllByRole("listitem")).toHaveLength(2);
  });

  it("por debajo de 1280 solo se abre el panel del paso actual (facturar → Líneas); sin eventos y sin albarán el resumen lo dice", async () => {
    withOrder();
    const { unmount } = render(<ErpOrderDetailPage />);
    await screen.findByRole("heading", { name: "Líneas" });
    expect(panel("Líneas")).toHaveAttribute("open");
    expect(panel("Envío y seguimiento")).not.toHaveAttribute("open");
    expect(panel("Historial")).not.toHaveAttribute("open");

    unmount();
    (getOrderTimeline as jest.Mock).mockResolvedValue({ total: 0, items: [] });
    withOrder({
      external_source: "woocommerce", factusol_albaran_number: null, transport_status: "label_created",
      workflow: {
        ...detail().workflow, next_action: "crear_envio", next_action_label: "Marcar recogido",
        next_action_hint: "Etiqueta subida: marca el pedido como recogido cuando salga.",
      },
    });
    render(<ErpOrderDetailPage />);
    await screen.findByRole("heading", { name: "Líneas" });
    // Enviar → se abre «Envío y seguimiento»; el resto, plegado.
    expect(panel("Envío y seguimiento")).toHaveAttribute("open");
    expect(panel("Envío y seguimiento")).toHaveTextContent("Etiqueta creada");
    expect(panel("Líneas")).not.toHaveAttribute("open");
    expect(panel("Historial")).not.toHaveAttribute("open");
    expect(panel("Historial")).toHaveTextContent("Sin eventos");
  });

  it("desde 1280 px todos los paneles se abren por defecto", async () => {
    viewport = "wide";
    withOrder();
    render(<ErpOrderDetailPage />);
    await screen.findByRole("heading", { name: "Líneas" });
    for (const name of ["Líneas", "Envío y seguimiento", "Historial"]) {
      expect(panel(name)).toHaveAttribute("open");
    }
  });

  it("lo que el usuario abre o cierra se recuerda (localStorage por panel) y manda sobre el defecto al volver", async () => {
    const user = userEvent.setup();
    withOrder();
    const { unmount } = render(<ErpOrderDetailPage />);
    await screen.findByRole("heading", { name: "Líneas" });
    expect(panel("Historial")).not.toHaveAttribute("open");
    await user.click(screen.getByRole("heading", { name: "Historial" }));
    expect(panel("Historial")).toHaveAttribute("open");
    expect(window.localStorage.getItem("erp.ficha.panel.historial")).toBe("true");
    await user.click(screen.getByRole("heading", { name: "Líneas" }));
    expect(panel("Líneas")).not.toHaveAttribute("open");
    expect(window.localStorage.getItem("erp.ficha.panel.lineas")).toBe("false");
    // El de envío no se ha tocado: no se guarda nada (sigue con el defecto).
    expect(window.localStorage.getItem("erp.ficha.panel.envio")).toBeNull();

    // Al volver a la ficha (misma u otra), lo elegido sigue.
    unmount();
    render(<ErpOrderDetailPage />);
    await screen.findByRole("heading", { name: "Líneas" });
    await waitFor(() => expect(panel("Historial")).toHaveAttribute("open"));
    expect(panel("Líneas")).not.toHaveAttribute("open");
    expect(panel("Envío y seguimiento")).not.toHaveAttribute("open");
  });

  it("«Crear albarán» desde el paso actual abre «Envío y seguimiento» aunque estuviera plegado (sin tocar lo recordado)", async () => {
    const user = userEvent.setup();
    withOrder({
      factusol_albaran_number: null,
      workflow: {
        ...detail().workflow, next_action: "crear_albaran", next_action_label: "Crear albarán",
        next_action_hint: "Crea el albarán en FACTUSOL.",
      },
    });
    render(<ErpOrderDetailPage />);
    await screen.findByRole("heading", { name: "Líneas" });
    expect(panel("Envío y seguimiento")).not.toHaveAttribute("open");
    expect(panel("Envío y seguimiento")).toHaveTextContent("Sin expedición · sin albarán");
    const bar = screen.getByRole("region", { name: "Siguiente paso" });
    await user.click(within(bar).getByRole("button", { name: "Crear albarán" }));
    expect(panel("Envío y seguimiento")).toHaveAttribute("open");
    expect(window.localStorage.getItem("erp.ficha.panel.envio")).toBeNull();
    // Se puede volver a plegar a mano.
    await user.click(screen.getByRole("heading", { name: "Envío y seguimiento" }));
    expect(panel("Envío y seguimiento")).not.toHaveAttribute("open");
  });
});

describe("ERP · Ficha (Lote 2 · PR-2) — «Pendiente de cobro» destacado", () => {
  it("con factura pendiente: bloque ámbar con el saldo EN VIVO de FACTUSOL, la fila «Cobrado» y el badge del panel FACTUSOL siguen", async () => {
    (getOrder as jest.Mock).mockResolvedValue(facturado());
    render(<ErpOrderDetailPage />);
    const eco = await screen.findByRole("region", { name: "Resumen económico" });
    const bloque = within(eco).getByRole("group", { name: "Pendiente de cobro" });
    await waitFor(() => expect(bloque).toHaveTextContent("300.00 EUR"));
    expect(bloque).toHaveClass("erp-flow-cobro", "is-pending");
    expect(bloque).toHaveTextContent("de la factura en FACTUSOL");
    expect(within(eco).getByText("Cobrado").nextSibling).toHaveTextContent("108.00 EUR");
    expect(within(eco).getByText("Total").nextSibling).toHaveTextContent("408.00 EUR");
    // El panel FACTUSOL conserva su badge de cobro y su acción.
    const fac = screen.getByRole("region", { name: "FACTUSOL" });
    expect(within(fac).getByText("Pendiente de cobro FACTUSOL")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Registrar cobro" })).toBeEnabled();
  });

  it("si FACTUSOL no responde, el saldo es el persistido en el pedido", async () => {
    (getOrderFactusolCobro as jest.Mock).mockRejectedValue(new Error("sin FACTUSOL"));
    (getOrder as jest.Mock).mockResolvedValue(facturado());
    render(<ErpOrderDetailPage />);
    const eco = await screen.findByRole("region", { name: "Resumen económico" });
    const bloque = within(eco).getByRole("group", { name: "Pendiente de cobro" });
    expect(bloque).toHaveTextContent("408.00 EUR");
    expect(bloque).toHaveClass("is-pending");
    expect(within(eco).getByText("Cobrado").nextSibling).toHaveTextContent("0.00 EUR");
  });

  it("sin factura no hay nada que cobrar todavía: «—» y «sin factura emitida», en neutro", async () => {
    withOrder();
    render(<ErpOrderDetailPage />);
    const eco = await screen.findByRole("region", { name: "Resumen económico" });
    const bloque = within(eco).getByRole("group", { name: "Pendiente de cobro" });
    expect(bloque).toHaveTextContent("—");
    expect(bloque).toHaveTextContent("sin factura emitida");
    expect(bloque).toHaveClass("is-na");
    expect(within(eco).queryByText("Cobrado")).toBeNull();
    expect(getOrderFactusolCobro).not.toHaveBeenCalled();
  });

  it("factura cobrada: el bloque pasa a verde con 0.00", async () => {
    (getOrderFactusolCobro as jest.Mock).mockResolvedValue({
      order_id: "o-1", status: "cobrada",
      invoice: { serie: 5, codigo: 260063, numero: "5-260063" },
      saldo_pendiente: 0, total_cobrado: 408,
    });
    (getOrder as jest.Mock).mockResolvedValue(facturado({ factusol_cobro_status: "cobrada" }));
    render(<ErpOrderDetailPage />);
    const eco = await screen.findByRole("region", { name: "Resumen económico" });
    const bloque = within(eco).getByRole("group", { name: "Pendiente de cobro" });
    await waitFor(() => expect(bloque).toHaveClass("is-done"));
    expect(bloque).toHaveTextContent("0.00 EUR");
    expect(bloque).toHaveTextContent("factura cobrada en FACTUSOL");
    expect(within(eco).getByText("Cobrado").nextSibling).toHaveTextContent("408.00 EUR");
  });
});

describe("ERP · Ficha (Lote 2 · PR-2) — móvil", () => {
  it("por debajo de 768 la acción del paso actual va en la barra pegada abajo, una sola vez (la tarjeta del paso solo lleva el texto)", async () => {
    viewport = "mobile";
    withOrder();
    render(<ErpOrderDetailPage />);
    const sticky = await screen.findByRole("region", { name: "Acción principal" });
    expect(sticky).toHaveClass("erp-primary-sticky");
    expect(within(sticky).getByRole("button", { name: "Emitir factura" })).toBeInTheDocument();
    expect(sticky).toHaveTextContent("Emite la factura en FACTUSOL.");
    // Va al FINAL del contenido (así `sticky` se mantiene visible).
    expect(sticky.nextElementSibling).toBeNull();
    const bar = screen.getByRole("region", { name: "Siguiente paso" });
    expect(bar).toHaveTextContent("Emite la factura en FACTUSOL.");
    expect(within(bar).queryByRole("button")).toBeNull();
    expect(screen.getAllByRole("button", { name: "Emitir factura" })).toHaveLength(1);
    expect(document.querySelector("main")).toHaveClass("has-sticky-action");
  });

  it("en escritorio no hay barra pegada: la acción vive en el paso actual", async () => {
    viewport = "wide";
    withOrder();
    render(<ErpOrderDetailPage />);
    const bar = await screen.findByRole("region", { name: "Siguiente paso" });
    expect(within(bar).getByRole("button", { name: "Emitir factura" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Acción principal" })).toBeNull();
    expect(document.querySelector("main")).not.toHaveClass("has-sticky-action");
  });
});
