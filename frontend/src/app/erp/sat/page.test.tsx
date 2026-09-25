import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SatQueuePage from "./page";
import type { SatQueueItem } from "../../lib/erpApi";
import {
  bulkNoShipping,
  findSatOrderByNumber,
  fireTransition,
  getErpSettings,
  getSatQueue,
  getSatShipped,
  markPickedUp,
  satEnqueueOrder,
  setPackages,
  transitionPacked,
  updateSeguimientoFields,
  uploadShippingFile,
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
  getSatShipped: jest.fn(),
  getErpSettings: jest.fn(),
  findSatOrderByNumber: jest.fn(),
  satEnqueueOrder: jest.fn(),
  bulkNoShipping: jest.fn(),
  // Dependencias de las cards / filas (no se disparan en estos tests salvo
  // «Marcar recogido»).
  downloadOrderFactusolAlbaranPdf: jest.fn(),
  fetchAlbaranFromWoo: jest.fn(),
  fireTransition: jest.fn(),
  listShippingFiles: jest.fn().mockResolvedValue([]),
  markPickedUp: jest.fn(),
  openShippingFile: jest.fn(),
  printShippingFile: jest.fn(),
  saveBlob: jest.fn(),
  // Modal de preparar / embalar (bultos, foto, reportar problema).
  setPackages: jest.fn(),
  transitionPacked: jest.fn(),
  attachDocument: jest.fn(),
  reportException: jest.fn(),
  EXCEPTION_CATALOG: jest.requireActual("../../lib/erpApi").EXCEPTION_CATALOG,
  // Lote 3: edición inline de los datos técnicos desde la cola.
  updateSeguimientoFields: jest.fn(),
  // Lote 4 · #5: subir la etiqueta desde la cola (mismo helper que la ficha).
  uploadShippingFile: jest.fn(),
  // Lote 5 · #3: guardar el nº de seguimiento desde «Listos».
  setOrderTracking: jest.fn(),
}));

const mockQueue = getSatQueue as jest.Mock;
const mockShipped = getSatShipped as jest.Mock;
const mockSettings = getErpSettings as jest.Mock;
const mockFind = findSatOrderByNumber as jest.Mock;
const mockEnqueue = satEnqueueOrder as jest.Mock;
const mockUser = getCurrentUser as jest.Mock;
const mockPicked = markPickedUp as jest.Mock;
const mockUpdateSeg = updateSeguimientoFields as jest.Mock;
const mockUpload = uploadShippingFile as jest.Mock;
const mockBulk = bulkNoShipping as jest.Mock;

/** Pedido WEB en cola (Lote 2 A3): su albarán lo genera WooCommerce, aún sin
 *  descargar → el chip ofrece «Descargar albarán». */
function item(over: Partial<SatQueueItem> = {}): SatQueueItem {
  return {
    id: "o1", order_number: "BOP-1", contact_name: "Ana Pi", company_name: "Duplicoder SL",
    preparation_status: "in_queue", transport_status: "not_shipped", payment_status: "paid",
    total_amount: 100, currency: "EUR", lines: [{ sku: "A", description: "Art A", quantity: 1 }],
    is_web_order: true, has_albaran: true, albaran_source: "woo", has_albaran_file: false,
    albaran_file_source: null, woo_albaran_available: true, woo_albaran_unavailable_reason: null,
    has_etiqueta: false, store_slug: "boprint",
    placed_at: "2026-09-01T10:00:00+00:00",
    notes: null, serial_number: null, whiterip_license: null, shipping_origin: null,
    sat_tab: "por_embalar", sin_envio: false, genei: null,
    ...over,
  };
}

/** Pedido embalado (listo para el envío) con albarán de Woo ya bajado y etiqueta. */
function packedItem(over: Partial<SatQueueItem> = {}): SatQueueItem {
  return item({
    id: "o2", order_number: "BOP-2", preparation_status: "packed", sat_tab: "embalados",
    albaran_source: "file", has_albaran_file: true, albaran_file_source: "woo_pdf_plugin",
    has_etiqueta: true, ...over,
  });
}

type Tabs = Partial<Record<"por_embalar" | "en_preparacion" | "embalados" | "pendiente_recogida",
  SatQueueItem[]>>;

/** Respuesta de `/sat/queue` por pestañas, con sus contadores (y las dos
 *  secciones de antes, como las manda el backend). */
function queue(t: Tabs, extra: { sin_envio?: number; enviados?: number } = {}) {
  const pe = t.por_embalar ?? [];
  const ep = t.en_preparacion ?? [];
  const em = t.embalados ?? [];
  const pr = t.pendiente_recogida ?? [];
  return {
    por_embalar: pe, en_preparacion: ep, embalados: em, pendiente_recogida: pr,
    preparing: [...pe, ...ep], ready_for_pickup: [...em, ...pr],
    counts: {
      por_embalar: pe.length, en_preparacion: ep.length, embalados: em.length,
      pendiente_recogida: pr.length, pendientes: pe.length + ep.length + em.length + pr.length,
      sin_envio: extra.sin_envio ?? 0, enviados: extra.enviados ?? 0,
    },
  };
}

/** Pedido MANUAL sin albarán (ni en FACTUSOL ni subido). */
function manualItem(over: Partial<SatQueueItem> = {}): SatQueueItem {
  return item({
    id: "o3", order_number: "MAN-3", contact_name: null, company_name: "Otra SL",
    is_web_order: false, has_albaran: false, albaran_source: null,
    woo_albaran_available: false, store_slug: null, ...over,
  });
}

/** La cola está cargada cuando las pestañas enseñan su contador. */
async function loaded(porEmbalar = 1, embalados = 1) {
  await screen.findByRole("tab", { name: `Por embalar ${porEmbalar}` });
  expect(screen.getByRole("tab", { name: `Embalados ${embalados}` })).toBeInTheDocument();
}

type User = ReturnType<typeof userEvent.setup>;

/** Abre una pestaña por su nombre (sin el contador). */
async function pestana(user: User, nombre: RegExp) {
  await user.click(await screen.findByRole("tab", { name: nombre }));
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
  mockQueue.mockResolvedValue(queue({ por_embalar: [item()], embalados: [packedItem()] }));
  mockShipped.mockResolvedValue({ items: [], total: 0, limit: 200 });
});

describe("SatQueuePage · pestañas", () => {
  it("orden nuevo (Todos pendientes la primera y la de entrada), contadores y SU subconjunto", async () => {
    const user = userEvent.setup();
    mockQueue.mockResolvedValue(queue({
      por_embalar: [item({ id: "a", order_number: "PE-1" })],
      en_preparacion: [item({ id: "b", order_number: "EP-1", preparation_status: "preparing",
                              sat_tab: "en_preparacion" })],
      embalados: [packedItem({ id: "c", order_number: "EM-1" })],
      pendiente_recogida: [packedItem({
        id: "d", order_number: "PR-1", sat_tab: "pendiente_recogida",
        genei: { shipment_code: "G1", state_bucket: "ready", state_label: "Tramitado",
                 label_available: true },
      })],
    }, { sin_envio: 2, enviados: 5 }));
    render(<SatQueuePage />);
    await screen.findByRole("tab", { name: "Todos pendientes 4" });
    expect(screen.getAllByRole("tab").map((t) => t.textContent?.trim())).toEqual([
      "Todos pendientes 4", "Por embalar 1", "En preparación 1", "Embalados 1",
      "Pendiente de recogida 1", "Enviados 5", "Sin envío 2", "Incidencias",
    ]);
    expect(screen.getByRole("tab", { name: /Todos pendientes/ })).toHaveAttribute("aria-selected", "true");
    const visibles = () => screen.getAllByRole("article").map((a) => a.getAttribute("aria-label"));
    expect(visibles()).toEqual(["Pedido PE-1", "Pedido EP-1", "Pedido EM-1", "Pedido PR-1"]);
    await pestana(user, /Por embalar/);
    expect(visibles()).toEqual(["Pedido PE-1"]);
    await pestana(user, /En preparación/);
    expect(visibles()).toEqual(["Pedido EP-1"]);
    await pestana(user, /^Embalados/);
    expect(visibles()).toEqual(["Pedido EM-1"]);
    await pestana(user, /Pendiente de recogida/);
    expect(visibles()).toEqual(["Pedido PR-1"]);
    expect(screen.getByText("Pendiente de recogida", { selector: ".badge" })).toBeInTheDocument();
  });

  it("cada pestaña lleva su color (fondo pastel + texto oscuro) como variables CSS", async () => {
    render(<SatQueuePage />);
    await loaded();
    const colores: Record<string, [string, string]> = {
      "Todos pendientes": ["#DBEAFE", "sat-tab--pendientes"],
      "Por embalar": ["#FED7AA", "sat-tab--por_embalar"],
      "En preparación": ["#FEF3C7", "sat-tab--en_preparacion"],
      "Embalados": ["#DCFCE7", "sat-tab--embalados"],
      "Pendiente de recogida": ["#EDE9FE", "sat-tab--pendiente_recogida"],
      "Enviados": ["#E5E7EB", "sat-tab--enviados"],
      "Sin envío": ["#EAE0D5", "sat-tab--sin_envio"],
      "Incidencias": ["#FEE2E2", "sat-tab--incidencias"],
    };
    for (const [nombre, [bg, cls]] of Object.entries(colores)) {
      const tab = screen.getByRole("tab", { name: new RegExp(`^${nombre}`) });
      expect(tab).toHaveClass("sat-tab--color", cls);
      expect(tab.style.getPropertyValue("--sat-tab-bg")).toBe(bg);
      expect(tab.style.getPropertyValue("--sat-tab-fg")).toMatch(/^#[0-9A-F]{6}$/);
    }
  });
});

describe("SatQueuePage · preparar y embalar en un MODAL sobre la cola", () => {
  it("«Empezar preparación» abre el modal con los bultos; al embalar se cierra y la cola se actualiza SIN F5", async () => {
    const user = userEvent.setup();
    const enCola = item({ id: "o1", order_number: "BOP-1" });
    mockQueue.mockResolvedValue(queue({ por_embalar: [enCola], embalados: [packedItem()] }));
    (fireTransition as jest.Mock).mockResolvedValue({});
    (setPackages as jest.Mock).mockResolvedValue([]);
    (transitionPacked as jest.Mock).mockResolvedValue({});
    render(<SatQueuePage />);
    await loaded();
    await pestana(user, /Por embalar/);
    // En la cola no hay «modo trabajo» a pantalla completa.
    expect(screen.queryByRole("link", { name: /modo trabajo/i })).not.toBeInTheDocument();

    // Tras empezar, la cola (recargada en silencio) ya lo tiene en preparación.
    mockQueue.mockResolvedValue(queue({
      en_preparacion: [{ ...enCola, preparation_status: "preparing", sat_tab: "en_preparacion" }],
      embalados: [packedItem()],
    }));
    await user.click(screen.getByRole("button", { name: "▶ Empezar preparación" }));
    const modal = await screen.findByRole("dialog", { name: "Preparar BOP-1" });
    await waitFor(() =>
      expect(fireTransition).toHaveBeenCalledWith("o1", { domain: "preparation", to_status: "preparing" }));
    // El modal es un overlay: la cola sigue debajo, en la misma pantalla.
    expect(screen.getByRole("tablist")).toBeInTheDocument();
    expect(within(modal).getByText("Art A")).toBeInTheDocument();          // líneas para cotejar
    const peso = await within(modal).findByLabelText("Peso bulto 1");
    await user.type(peso, "2");
    await user.type(within(modal).getByLabelText("Alto bulto 1"), "10");
    await user.type(within(modal).getByLabelText("Ancho bulto 1"), "20");
    await user.type(within(modal).getByLabelText("Fondo bulto 1"), "30");
    await user.click(within(modal).getByRole("button", { name: "+ Añadir bulto" }));
    expect(within(modal).getByLabelText("Peso bulto 2")).toBeInTheDocument();
    await user.click(within(modal).getAllByRole("button", { name: "Eliminar" })[0]);

    // Al embalar, la cola ya lo trae en «Embalados».
    mockQueue.mockResolvedValue(queue({ embalados: [
      packedItem(), { ...enCola, preparation_status: "packed", sat_tab: "embalados" },
    ] }));
    const llamadas = mockQueue.mock.calls.length;
    await user.click(within(modal).getByRole("button", { name: "📦 Embalar" }));
    await waitFor(() => expect(transitionPacked).toHaveBeenCalledWith("o1"));
    expect(setPackages).toHaveBeenCalledWith("o1", [
      { weight_kg: 2, height_cm: 10, width_cm: 20, depth_cm: 30 },
    ]);
    // Se cierra el modal y la cola se recarga sola: la tarjeta sale de su pestaña
    // y los contadores cuadran al instante (sin recargar la página).
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() => expect(mockQueue.mock.calls.length).toBeGreaterThan(llamadas));
    expect(await screen.findByRole("tab", { name: "Por embalar 0" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "En preparación 0" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Embalados 2" })).toBeInTheDocument();
    expect(screen.queryByRole("article", { name: "Pedido BOP-1" })).not.toBeInTheDocument();
    await pestana(user, /^Embalados/);
    expect(screen.getByRole("article", { name: "Pedido BOP-1" })).toBeInTheDocument();
  });

  it("un pedido ya en preparación: «📦 Embalar» abre el modal (sin volver a empezar) y «Cerrar» vuelve a la cola", async () => {
    const user = userEvent.setup();
    mockQueue.mockResolvedValue(queue({ en_preparacion: [
      item({ preparation_status: "preparing", sat_tab: "en_preparacion" }),
    ] }));
    (fireTransition as jest.Mock).mockClear();
    render(<SatQueuePage />);
    await pestana(user, /En preparación/);
    await user.click(await screen.findByRole("button", { name: "📦 Embalar" }));
    const modal = await screen.findByRole("dialog", { name: "Preparar BOP-1" });
    expect(within(modal).getByLabelText("Peso bulto 1")).toBeInTheDocument();
    expect(fireTransition).not.toHaveBeenCalled();
    await user.click(within(modal).getByRole("button", { name: "Cerrar" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /En preparación/ })).toHaveAttribute("aria-selected", "true");
  });

  it("con envío Genei el botón dice «Ver envío Genei»; sin él, «Crear envío con Genei»", async () => {
    mockQueue.mockResolvedValue(queue({ embalados: [
      packedItem({ id: "o2", order_number: "BOP-2" }),
      packedItem({ id: "o3", order_number: "BOP-3", genei: {
        shipment_code: "G9", state_bucket: "created", state_label: "Recogida pendiente de pago",
        label_available: false,
      } }),
    ] }));
    const user = userEvent.setup();
    render(<SatQueuePage />);
    await pestana(user, /^Embalados/);
    const sin = await screen.findByRole("article", { name: "Pedido BOP-2" });
    const con = screen.getByRole("article", { name: "Pedido BOP-3" });
    expect(within(sin).getByRole("button", { name: /Crear envío con Genei/ })).toBeInTheDocument();
    expect(within(con).getByRole("button", { name: /Ver envío Genei/ })).toBeInTheDocument();
    expect(within(con).queryByRole("button", { name: /Crear envío con Genei/ })).not.toBeInTheDocument();
  });
});

describe("SatQueuePage · «Sin envío» («No requiere envío»)", () => {
  it("se marca en lote desde los pendientes: «No requiere envío» (con confirmación)", async () => {
    mockBulk.mockResolvedValue({ ok: true, changed: 1, already: 0, value: true });
    const user = userEvent.setup();
    render(<SatQueuePage />);
    await loaded();
    await pestana(user, /Por embalar/);
    await user.click(screen.getByRole("checkbox", { name: "Seleccionar BOP-1" }));
    await user.click(screen.getByRole("button", { name: "No requiere envío" }));
    const dialog = await screen.findByRole("dialog", { name: "Confirmar No requiere envío" });
    expect(dialog).toHaveTextContent("Pasan a «Sin envío» y NO cuentan como enviados");
    await user.click(within(dialog).getByRole("button", { name: "Marcar" }));
    await waitFor(() => expect(mockBulk).toHaveBeenCalledWith(["o1"], true));
  });

  it("tiene su pestaña (no son enviados) y se pueden devolver al taller", async () => {
    mockBulk.mockResolvedValue({ ok: true, changed: 1, already: 0, value: false });
    mockShipped.mockResolvedValue({ total: 1, limit: 200, items: [
      item({ id: "o9", order_number: "BOP-9", sat_tab: "sin_envio", sin_envio: true }),
    ] });
    const user = userEvent.setup();
    render(<SatQueuePage />);
    await loaded();
    await pestana(user, /Sin envío/);
    await waitFor(() => expect(mockShipped).toHaveBeenLastCalledWith({}, true));
    const table = await screen.findByRole("table", { name: "Pedidos sin envío" });
    expect(within(table).getByText("No requiere envío")).toBeInTheDocument();
    await user.click(within(table).getByRole("checkbox", { name: "Seleccionar BOP-9" }));
    await user.click(screen.getByRole("button", { name: "Requiere envío (volver al taller)" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Devolver" }));
    await waitFor(() => expect(mockBulk).toHaveBeenCalledWith(["o9"], false));
  });

  it("«Enviados» pide solo los enviados (recogido / en tránsito / entregado), no los «Sin envío»", async () => {
    mockShipped.mockResolvedValue({ total: 250, limit: 200, items: [
      item({ id: "e1", order_number: "BOP-E1", preparation_status: "packed",
             transport_status: "in_transit", sat_tab: "enviados", tracking_number: "1Z999",
             genei: { shipment_code: "G1", courier: "GLS", state_bucket: "in_transit",
                      label_available: true } }),
    ] });
    const user = userEvent.setup();
    render(<SatQueuePage />);
    await loaded();
    await user.selectOptions(await screen.findByLabelText("Tienda"), "boprint");
    await pestana(user, /^Enviados/);
    await waitFor(() => expect(mockShipped).toHaveBeenLastCalledWith({ store_slug: "boprint" }, false));
    const table = await screen.findByRole("table", { name: "Pedidos enviados" });
    const rows = within(table).getAllByRole("row");
    expect(within(rows[1]).getByText("Recogido · en tránsito")).toBeInTheDocument();
    expect(within(rows[1]).getByText("1Z999")).toBeInTheDocument();
    expect(within(rows[1]).getByText("GLS")).toBeInTheDocument();
    expect(screen.getByText(/Mostrando los 1 más recientes de 250/)).toBeInTheDocument();
  });
});

describe("«Enviados»: estado REAL del transportista (Genei /tracking)", () => {
  it("enseña el último escaneo de la agencia, no el genérico «Recogido · en tránsito»", async () => {
    mockShipped.mockResolvedValue({ total: 1, limit: 200, items: [
      item({ id: "e2", order_number: "ALB-2-200038", preparation_status: "packed",
             transport_status: "in_transit", sat_tab: "enviados",
             tracking_number: "0033260080539700026674",
             genei: { shipment_code: "G2", courier: "Ctt Premium", state_bucket: "in_transit",
                      state_label: "Recogida efectuada / en tránsito", label_available: true,
                      carrier_status: "PENDIENTE DE ENTRADA EN RED",
                      carrier_status_at: "2026-09-24T18:00:00+00:00",
                      carrier_step: "pre_transit",
                      tracking_url: "https://www.cttexpress.com/localizador/" } }),
    ] });
    const user = userEvent.setup();
    render(<SatQueuePage />);
    await loaded();
    await pestana(user, /^Enviados/);
    const table = await screen.findByRole("table", { name: "Pedidos enviados" });
    const row = within(table).getAllByRole("row")[1];
    const estado = within(row).getByText("PENDIENTE DE ENTRADA EN RED");
    expect(estado).toHaveClass("badge", "warn");          // aún sin escanear: aviso
    expect(within(row).queryByText("Recogido · en tránsito")).not.toBeInTheDocument();
    expect(within(row).queryByText(/Recogida efectuada/)).not.toBeInTheDocument();
    expect(within(row).getByText("Ctt Premium")).toBeInTheDocument();
  });

  it("sin escaneos del transportista, el estado de Genei antes que el genérico", async () => {
    mockShipped.mockResolvedValue({ total: 1, limit: 200, items: [
      item({ id: "e3", order_number: "BOP-E3", preparation_status: "packed",
             transport_status: "in_transit", sat_tab: "enviados",
             genei: { shipment_code: "G3", state_bucket: "in_transit", state_label: "En reparto",
                      label_available: true } }),
    ] });
    const user = userEvent.setup();
    render(<SatQueuePage />);
    await loaded();
    await pestana(user, /^Enviados/);
    const table = await screen.findByRole("table", { name: "Pedidos enviados" });
    expect(within(table).getByText("En reparto")).toBeInTheDocument();
  });
});

describe("SatQueuePage (regresión)", () => {
  it("carga la cola sin filtros y pasa los filtros a getSatQueue", async () => {
    const user = userEvent.setup();
    render(<SatQueuePage />);
    await loaded();
    expect(mockQueue).toHaveBeenLastCalledWith({});
    await user.selectOptions(await screen.findByLabelText("Tienda"), "boprint");
    await waitFor(() => expect(mockQueue).toHaveBeenLastCalledWith({ store_slug: "boprint" }));
    await user.type(screen.getByLabelText("Fecha desde"), "2026-09-01");
    await waitFor(() => expect(mockQueue).toHaveBeenLastCalledWith(
      expect.objectContaining({ desde: "2026-09-01" }),
    ));
    await user.type(screen.getByLabelText("Buscar pedido o cliente"), "dupli");
    await waitFor(() => expect(mockQueue).toHaveBeenLastCalledWith(
      expect.objectContaining({ q: "dupli" }),
    ));
    await user.selectOptions(screen.getByLabelText("Orden por fecha del pedido"), "fecha_asc");
    await waitFor(() => expect(mockQueue).toHaveBeenLastCalledWith(
      expect.objectContaining({ sort: "fecha_asc" }),
    ));
    await user.click(screen.getByRole("button", { name: "Limpiar filtros" }));
    await waitFor(() => expect(mockQueue).toHaveBeenLastCalledWith({}));
  });

  it("alterna Tarjetas / Lista, recuerda la vista y la lista conserva las acciones", async () => {
    const user = userEvent.setup();
    mockPicked.mockResolvedValue({ order_id: "o2", transport_status: "in_transit", already_picked_up: false });
    render(<SatQueuePage />);
    await loaded();
    await pestana(user, /Por embalar/);
    expect(screen.getByRole("button", { name: "▶ Empezar preparación" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Lista" }));
    const prepTable = await screen.findByRole("table", { name: "Pedidos por embalar" });
    // El nº lleva a la ficha; empezar abre el modal (nada de modo trabajo).
    expect(within(prepTable).getByRole("link", { name: "BOP-1" })).toHaveAttribute("href", "/erp/orders/o1");
    expect(within(prepTable).getByRole("button", { name: "▶ Empezar" })).toBeInTheDocument();
    expect(within(prepTable).getByText("Ana Pi · Duplicoder SL")).toBeInTheDocument();
    expect(within(prepTable).getByRole("button", { name: /Descargar albarán/ })).toBeInTheDocument();
    expect(window.localStorage.getItem("bohub.sat.queue.view")).toBe("list");

    await pestana(user, /^Embalados/);
    const readyTable = await screen.findByRole("table", { name: "Pedidos embalados" });
    expect(within(readyTable).getByRole("button", { name: /Imprimir albarán/ })).toBeInTheDocument();
    expect(within(readyTable).getByRole("button", { name: /Imprimir etiqueta/ })).toBeInTheDocument();
    expect(within(readyTable).getByRole("button", { name: "Reabrir preparación" })).toBeInTheDocument();
    // Tras «Marcar recogido» la cola se recarga sola: el pedido deja «Embalados».
    mockQueue.mockResolvedValue(queue({ por_embalar: [item()] }, { enviados: 1 }));
    await user.click(within(readyTable).getByRole("button", { name: /Marcar recogido/ }));
    await user.click(within(readyTable).getByRole("button", { name: "Sí, recogido" }));
    await waitFor(() => expect(mockPicked).toHaveBeenCalledWith("o2"));
    expect(await screen.findByRole("tab", { name: "Embalados 0" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Enviados 1" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Tarjetas" }));
    expect(window.localStorage.getItem("bohub.sat.queue.view")).toBe("cards");
  });

  it("arranca en vista lista si así quedó guardado", async () => {
    window.localStorage.setItem("bohub.sat.queue.view", "list");
    render(<SatQueuePage />);
    expect(await screen.findByRole("table", { name: "Pedidos por embalar y en preparación" }))
      .toBeInTheDocument();
  });

  it("albarán por tipo de pedido (Lote 2 A3): web → Woo, manual sin albarán → «Falta albarán»", async () => {
    const user = userEvent.setup();
    window.localStorage.setItem("bohub.sat.queue.view", "list");
    const reason = "La tienda «artisjet» no tiene configurada la conexión con WooCommerce.";
    mockQueue.mockResolvedValue(queue({
      por_embalar: [
        item(),
        manualItem(),
        item({ id: "o4", order_number: "ARTISJ-9553", store_slug: "artisjet",
               has_albaran: false, albaran_source: null, woo_albaran_available: false,
               woo_albaran_unavailable_reason: reason }),
      ],
      embalados: [manualItem({ id: "o5", order_number: "MAN-5", preparation_status: "packed",
                               sat_tab: "embalados" })],
    }));
    render(<SatQueuePage />);
    await pestana(user, /Por embalar/);
    const prepTable = await screen.findByRole("table", { name: "Pedidos por embalar" });
    const rows = within(prepTable).getAllByRole("row");
    expect(within(rows[1]).getByRole("button", { name: /Descargar albarán/ })).toBeInTheDocument();
    expect(within(rows[1]).queryByText(/Falta/)).not.toBeInTheDocument();
    expect(within(rows[2]).getByRole("link", { name: /Falta albarán/ })).toHaveAttribute("href", "/erp/orders/o3");
    expect(within(rows[3]).getByRole("link", { name: /Albarán de WooCommerce no disponible/ }))
      .toHaveAttribute("href", "/erp/orders/o4");
    expect(within(rows[3]).getByText(reason)).toBeInTheDocument();
    await pestana(user, /^Embalados/);
    const readyTable = await screen.findByRole("table", { name: "Pedidos embalados" });
    expect(within(readyTable).getByRole("link", { name: /Falta albarán/ })).toHaveAttribute("href", "/erp/orders/o5");
  });

  it("la vista lista enseña los datos técnicos (con copiar) y las observaciones encima de la fila", async () => {
    const user = userEvent.setup();
    window.localStorage.setItem("bohub.sat.queue.view", "list");
    mockQueue.mockResolvedValue(queue({ por_embalar: [
      item({ notes: "Cliente pide manual en alemán.", serial_number: "FLX-7741-2026",
             shipping_origin: "SAT" }),
      manualItem(),
    ] }));
    render(<SatQueuePage />);
    await pestana(user, /Por embalar/);
    const table = await screen.findByRole("table", { name: "Pedidos por embalar" });
    const rows = within(table).getAllByRole("row");
    expect(rows).toHaveLength(4);
    expect(rows[1]).toHaveClass("sat-row-notes");
    expect(within(rows[1]).getByRole("note", { name: "Observaciones del comercial" }))
      .toHaveTextContent("Cliente pide manual en alemán.");
    expect(within(rows[2]).getByText("FLX-7741-2026")).toHaveClass("sat-tech-value");
    expect(within(rows[2]).getByRole("button", { name: "Copiar nº de serie" })).toBeInTheDocument();
    expect(rows[3].querySelector(".sat-td-tech")).toHaveTextContent("—");
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
    await loaded();
    const calls = mockQueue.mock.calls.length;
    await user.type(await screen.findByLabelText("Número de pedido a añadir"), " bop-9 ");
    await user.click(screen.getByRole("button", { name: "Añadir" }));
    await waitFor(() => expect(mockEnqueue).toHaveBeenCalledWith("o9"));
    expect(await screen.findByRole("status")).toHaveTextContent(
      "BOP-9 (Nueva SL) añadido a la Cola SAT (aprobado).",
    );
    await waitFor(() => expect(mockQueue.mock.calls.length).toBeGreaterThan(calls));
    mockFind.mockResolvedValue({ id: "o10", order_number: "BOP-10", already_queued: false });
    mockEnqueue.mockRejectedValue(new Error("El pedido BOP-10 está anulado: no se puede añadir a la Cola SAT."));
    await user.type(screen.getByLabelText("Número de pedido a añadir"), "BOP-10");
    await user.click(screen.getByRole("button", { name: "Añadir" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/está anulado/);
  });

  it("sin permiso de aprobar (rol sat) no enseña «Añadir pedido a la cola»", async () => {
    mockUser.mockResolvedValue({
      id: "s", role: "sat", full_name: "Sat User", email: "s@x", is_active: true,
    });
    render(<SatQueuePage />);
    await loaded();
    expect(screen.queryByLabelText("Número de pedido a añadir")).not.toBeInTheDocument();
  });

  it("«Todos pendientes» enseña por hacer y embalados a la vez (dos columnas, cada una con scroll)", async () => {
    render(<SatQueuePage />);
    await loaded();
    const panel = screen.getByRole("tabpanel", { name: "Todos pendientes" });
    const titles = within(panel).getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
    expect(titles.some((t) => t?.includes("Por embalar"))).toBe(true);
    expect(titles.some((t) => t?.includes("Embalados"))).toBe(true);
    expect(within(panel).getByRole("button", { name: "▶ Empezar preparación" })).toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: /Marcar recogido/ })).toBeInTheDocument();
    expect(panel.querySelectorAll(".sat-scroll")).toHaveLength(2);
  });

  it("«Lista» se aplica también a «Todos pendientes»: cada columna es una tabla", async () => {
    window.localStorage.setItem("bohub.sat.queue.view", "list");
    render(<SatQueuePage />);
    await loaded();
    const panel = screen.getByRole("tabpanel", { name: "Todos pendientes" });
    const prep = within(panel).getByRole("table", { name: "Pedidos por embalar y en preparación" });
    const ready = within(panel).getByRole("table", { name: "Pedidos embalados y pendientes de recogida" });
    expect(within(prep).getAllByRole("columnheader").map((h) => h.textContent)).toEqual(
      ["Nº", "Cliente", "Tienda", "Estado", "Datos técnicos", "Acciones"],
    );
    expect(within(ready).getByRole("link", { name: "BOP-2" })).toBeInTheDocument();
  });

  it("edición inline: guardar el nº de serie llama al PATCH y muestra el nuevo valor", async () => {
    const user = userEvent.setup();
    mockUpdateSeg.mockResolvedValue({
      serial_number: "FLX-9999", whiterip_license: null, shipping_origin: null,
    });
    mockQueue.mockResolvedValue(queue({ por_embalar: [item({ serial_number: null })] }));
    render(<SatQueuePage />);
    await loaded(1, 0);
    await user.click(screen.getByRole("button", { name: "Añadir nº de serie" }));
    await user.type(screen.getByRole("textbox", { name: "Editar nº de serie" }), "FLX-9999");
    await user.click(screen.getByRole("button", { name: "Guardar" }));
    await waitFor(() =>
      expect(mockUpdateSeg).toHaveBeenCalledWith("o1", { serial_number: "FLX-9999" }),
    );
    expect(await screen.findByText("FLX-9999")).toHaveClass("sat-tech-value");
  });

  it("rol sat (taller) SÍ edita los datos técnicos (serial/WhiteRIP)", async () => {
    mockUser.mockResolvedValue({
      id: "s", role: "sat", full_name: "Sat User", email: "s@x", is_active: true,
    });
    mockQueue.mockResolvedValue(queue({ por_embalar: [item({ serial_number: "FLX-1" })] }));
    render(<SatQueuePage />);
    await loaded(1, 0);
    expect(screen.getByText("FLX-1")).toHaveClass("sat-tech-value");
    expect(screen.getByRole("button", { name: /Editar/ })).toBeInTheDocument();
  });

  it("sube la etiqueta desde una fila de «Embalados» y la cola se actualiza sola", async () => {
    const user = userEvent.setup();
    window.localStorage.setItem("bohub.sat.queue.view", "list");
    const base = packedItem({
      id: "o7", order_number: "BOP-7", albaran_file_source: "manual_upload",
      is_web_order: false, has_etiqueta: false,
    });
    mockQueue.mockResolvedValue(queue({ embalados: [base] }));
    mockUpload.mockResolvedValue({
      file: {
        id: "f", kind: "etiqueta", source: "manual_upload", filename: "e.pdf",
        mime_type: "application/pdf", size_bytes: 1, uploaded_by_user_id: null,
        uploaded_at: null, download_url: "/x",
      },
      transition_applied: true, transport_status: "label_created", transition_reason: null,
    });
    render(<SatQueuePage />);
    await pestana(user, /^Embalados/);
    const readyTable = await screen.findByRole("table", { name: "Pedidos embalados" });
    // Con la etiqueta puesta, el backend ya lo cuenta en «Pendiente de recogida».
    mockQueue.mockResolvedValue(queue({ pendiente_recogida: [
      { ...base, has_etiqueta: true, sat_tab: "pendiente_recogida", transport_status: "label_created" },
    ] }));
    const pdf = new File(["%PDF-"], "e.pdf", { type: "application/pdf" });
    await user.upload(within(readyTable).getByLabelText(/Subir etiqueta/), pdf);
    await waitFor(() => expect(mockUpload).toHaveBeenCalledWith("o7", "etiqueta", pdf));
    expect(await screen.findByRole("tab", { name: "Pendiente de recogida 1" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Embalados 0" })).toBeInTheDocument();
    await pestana(user, /Pendiente de recogida/);
    expect(await screen.findByRole("button", { name: /Imprimir etiqueta/ })).toBeInTheDocument();
  });
});
