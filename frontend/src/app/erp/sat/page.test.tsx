import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SatQueuePage from "./page";
import type { SatQueueItem, SatTab } from "../../lib/erpApi";
import {
  bulkNoShipping,
  findSatOrderByNumber,
  fireTransition,
  getErpSettings,
  getSatOrderItem,
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
  getSatOrderItem: jest.fn(),
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
  // Embalar en la propia card (bultos en línea).
  setPackages: jest.fn(),
  transitionPacked: jest.fn(),
  // Lote 3: edición inline de los datos técnicos desde la cola.
  updateSeguimientoFields: jest.fn(),
  // Lote 4 · #5: subir la etiqueta desde la cola (mismo helper que la ficha).
  uploadShippingFile: jest.fn(),
  // Lote 5 · #3: guardar el nº de seguimiento desde «Listos».
  setOrderTracking: jest.fn(),
}));

const mockQueue = getSatQueue as jest.Mock;
const mockShipped = getSatShipped as jest.Mock;
const mockItem = getSatOrderItem as jest.Mock;
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
    sat_tab: "por_embalar", sin_seguimiento: false, genei: null,
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
function queue(t: Tabs, extra: { sin_seguimiento?: number; enviados?: number } = {}) {
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
      sin_seguimiento: extra.sin_seguimiento ?? 0, enviados: extra.enviados ?? 0,
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

describe("SatQueuePage · pestañas por paso del taller", () => {
  it("las pestañas, en su orden y con los contadores del backend; cada una pinta SU subconjunto", async () => {
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
    }, { sin_seguimiento: 2, enviados: 5 }));
    render(<SatQueuePage />);
    await screen.findByRole("tab", { name: "Por embalar 1" });
    expect(screen.getAllByRole("tab").map((t) => t.textContent?.trim())).toEqual([
      "Por embalar 1", "En preparación 1", "Embalados 1", "Todos pendientes 4",
      "Sin seguimiento 2", "Pendiente de recogida 1", "Enviados 5", "Incidencias",
    ]);
    const visibles = () => screen.getAllByRole("article").map((a) => a.getAttribute("aria-label"));
    expect(visibles()).toEqual(["Pedido PE-1"]);
    await user.click(screen.getByRole("tab", { name: /En preparación/ }));
    expect(visibles()).toEqual(["Pedido EP-1"]);
    await user.click(screen.getByRole("tab", { name: /^Embalados/ }));
    expect(visibles()).toEqual(["Pedido EM-1"]);
    await user.click(screen.getByRole("tab", { name: /Pendiente de recogida/ }));
    expect(visibles()).toEqual(["Pedido PR-1"]);
    expect(screen.getByText("Pendiente de recogida", { selector: ".badge" })).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: /Todos pendientes/ }));
    expect(visibles()).toEqual(["Pedido PE-1", "Pedido EP-1", "Pedido EM-1", "Pedido PR-1"]);
  });

  it("«Empezar preparación» y «Embalar» se hacen EN LA CARD: no se cierra ni salta de pestaña", async () => {
    const user = userEvent.setup();
    const enCola = item({ id: "o1", order_number: "BOP-1" });
    mockQueue.mockResolvedValue(queue({ por_embalar: [enCola], embalados: [packedItem()] }));
    (fireTransition as jest.Mock).mockResolvedValue({});
    (setPackages as jest.Mock).mockResolvedValue([]);
    (transitionPacked as jest.Mock).mockResolvedValue({});
    mockItem
      .mockResolvedValueOnce({ ...enCola, preparation_status: "preparing", sat_tab: "en_preparacion" })
      .mockResolvedValueOnce({ ...enCola, preparation_status: "packed", sat_tab: "embalados" });
    render(<SatQueuePage />);
    await loaded();
    const calls = mockQueue.mock.calls.length;

    await user.click(screen.getByRole("button", { name: "▶ Empezar preparación" }));
    expect(fireTransition).toHaveBeenCalledWith("o1", { domain: "preparation", to_status: "preparing" });
    // Sigue en «Por embalar», abierto, con los bultos y «Embalar» en línea…
    const embalar = await screen.findByRole("region", { name: "Embalar BOP-1" });
    expect(screen.getByRole("tab", { name: /Por embalar/ })).toHaveAttribute("aria-selected", "true");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    // …y los contadores ya lo cuentan en «En preparación».
    expect(screen.getByRole("tab", { name: "Por embalar 0" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "En preparación 1" })).toBeInTheDocument();

    await user.type(within(embalar).getByLabelText("Peso bulto 1"), "2");
    await user.type(within(embalar).getByLabelText("Alto bulto 1"), "10");
    await user.type(within(embalar).getByLabelText("Ancho bulto 1"), "20");
    await user.type(within(embalar).getByLabelText("Fondo bulto 1"), "30");
    await user.click(within(embalar).getByRole("button", { name: "📦 Embalar" }));
    await waitFor(() => expect(transitionPacked).toHaveBeenCalledWith("o1"));
    // La card pasa al paso siguiente EN SU SITIO (ya embalada: recogido, Genei…).
    const card = await screen.findByRole("article", { name: "Pedido BOP-1" });
    expect(await within(card).findByRole("button", { name: /Marcar recogido/ })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Embalados 2" })).toBeInTheDocument();
    // Sin recargar la cola entera.
    expect(mockQueue.mock.calls.length).toBe(calls);
    // Al cambiar de pestaña, cada uno en la suya.
    await user.click(screen.getByRole("tab", { name: /^Embalados/ }));
    expect(screen.getByRole("article", { name: "Pedido BOP-1" })).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: /Por embalar/ }));
    expect(screen.queryByRole("article", { name: "Pedido BOP-1" })).not.toBeInTheDocument();
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
    await user.click(await screen.findByRole("tab", { name: /^Embalados/ }));
    const sin = await screen.findByRole("article", { name: "Pedido BOP-2" });
    const con = screen.getByRole("article", { name: "Pedido BOP-3" });
    expect(within(sin).getByRole("button", { name: /Crear envío con Genei/ })).toBeInTheDocument();
    expect(within(con).getByRole("button", { name: /Ver envío Genei/ })).toBeInTheDocument();
    expect(within(con).queryByRole("button", { name: /Crear envío con Genei/ })).not.toBeInTheDocument();
  });
});

describe("SatQueuePage · «Sin seguimiento» (enviado sin tracking)", () => {
  it("se marca en lote desde los pendientes (con confirmación)", async () => {
    mockBulk.mockResolvedValue({ ok: true, changed: 1, already: 0, value: true });
    const user = userEvent.setup();
    render(<SatQueuePage />);
    await loaded();
    await user.click(screen.getByRole("checkbox", { name: "Seleccionar BOP-1" }));
    await user.click(screen.getByRole("button", { name: "Marcar enviado sin seguimiento" }));
    const dialog = await screen.findByRole("dialog", { name: "Confirmar Sin seguimiento" });
    expect(dialog).toHaveTextContent("ENVIADOS sin nº de tracking");
    await user.click(within(dialog).getByRole("button", { name: "Marcar" }));
    await waitFor(() => expect(mockBulk).toHaveBeenCalledWith(["o1"], true));
  });

  it("su pestaña lista los enviados sin tracking y permite quitarlos", async () => {
    mockBulk.mockResolvedValue({ ok: true, changed: 1, already: 0, value: false });
    mockShipped.mockResolvedValue({ total: 1, limit: 200, items: [
      item({ id: "o9", order_number: "BOP-9", sat_tab: "sin_seguimiento", sin_seguimiento: true }),
    ] });
    const user = userEvent.setup();
    render(<SatQueuePage />);
    await loaded();
    await user.click(screen.getByRole("tab", { name: /Sin seguimiento/ }));
    await waitFor(() => expect(mockShipped).toHaveBeenLastCalledWith({}, true));
    const table = await screen.findByRole("table", { name: "Pedidos enviados sin seguimiento" });
    expect(within(table).getByText("Enviado sin seguimiento")).toBeInTheDocument();
    await user.click(within(table).getByRole("checkbox", { name: "Seleccionar BOP-9" }));
    await user.click(screen.getByRole("button", { name: "Quitar «Sin seguimiento»" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Devolver" }));
    await waitFor(() => expect(mockBulk).toHaveBeenCalledWith(["o9"], false));
  });
});

describe("SatQueuePage · «Enviados»", () => {
  it("carga los enviados (recogido / en tránsito / entregado + sin seguimiento) con los filtros", async () => {
    mockShipped.mockResolvedValue({ total: 250, limit: 200, items: [
      item({ id: "e1", order_number: "BOP-E1", preparation_status: "packed",
             transport_status: "in_transit", sat_tab: "enviados", tracking_number: "1Z999",
             genei: { shipment_code: "G1", courier: "GLS", state_bucket: "in_transit",
                      label_available: true } }),
      item({ id: "e2", order_number: "BOP-E2", sat_tab: "sin_seguimiento", sin_seguimiento: true }),
    ] });
    const user = userEvent.setup();
    render(<SatQueuePage />);
    await loaded();
    await user.selectOptions(await screen.findByLabelText("Tienda"), "boprint");
    await user.click(screen.getByRole("tab", { name: /^Enviados/ }));
    await waitFor(() => expect(mockShipped).toHaveBeenLastCalledWith({ store_slug: "boprint" }, false));
    const table = await screen.findByRole("table", { name: "Pedidos enviados" });
    expect(table).toHaveClass("data-table", "data-table--responsive");
    const rows = within(table).getAllByRole("row");
    expect(within(rows[1]).getByText("Recogido · en tránsito")).toBeInTheDocument();
    expect(within(rows[1]).getByText("1Z999")).toBeInTheDocument();
    expect(within(rows[1]).getByText("GLS")).toBeInTheDocument();
    expect(within(rows[2]).getByText("Enviado sin seguimiento")).toBeInTheDocument();
    expect(screen.getByText(/Mostrando los 2 más recientes de 250/)).toBeInTheDocument();
    expect(screen.getByRole("tabpanel", { name: "Enviados" }).querySelector(".sat-scroll")).not.toBeNull();
  });
});

describe("SatQueuePage (Lote B6 · regresión)", () => {
  it("carga la cola sin filtros y pasa los filtros a getSatQueue", async () => {
    const user = userEvent.setup();
    render(<SatQueuePage />);
    await loaded();
    expect(mockQueue).toHaveBeenLastCalledWith({});
    // Tienda (del catálogo de Ajustes ERP) y fechas.
    await user.selectOptions(await screen.findByLabelText("Tienda"), "boprint");
    await waitFor(() => expect(mockQueue).toHaveBeenLastCalledWith({ store_slug: "boprint" }));
    await user.type(screen.getByLabelText("Fecha desde"), "2026-09-01");
    await waitFor(() => expect(mockQueue).toHaveBeenLastCalledWith(
      expect.objectContaining({ desde: "2026-09-01" }),
    ));
    // Buscador (con retardo) — llega a la cola como `q`.
    await user.type(screen.getByLabelText("Buscar pedido o cliente"), "dupli");
    await waitFor(() => expect(mockQueue).toHaveBeenLastCalledWith(
      expect.objectContaining({ q: "dupli" }),
    ));
    // Orden por fecha.
    await user.selectOptions(screen.getByLabelText("Orden por fecha del pedido"), "fecha_asc");
    await waitFor(() => expect(mockQueue).toHaveBeenLastCalledWith(
      expect.objectContaining({ sort: "fecha_asc" }),
    ));
    await user.click(screen.getByRole("button", { name: "Limpiar filtros" }));
    await waitFor(() => expect(mockQueue).toHaveBeenLastCalledWith({}));
  });

  it("alterna Tarjetas / Lista, recuerda la vista y la lista conserva las acciones (recogido en su sitio)", async () => {
    const user = userEvent.setup();
    mockPicked.mockResolvedValue({ order_id: "o2", transport_status: "in_transit", already_picked_up: false });
    mockItem.mockResolvedValue(packedItem({ transport_status: "in_transit", sat_tab: "enviados" }));
    render(<SatQueuePage />);
    // Por defecto tarjetas: la card en cola ofrece empezar (canEdit) y el modo trabajo.
    expect(await screen.findByRole("button", { name: "▶ Empezar preparación" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Modo trabajo" })).toHaveAttribute("href", "/erp/sat/o1");
    expect(screen.queryByRole("table", { name: "Pedidos por embalar" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Lista" }));
    const prepTable = await screen.findByRole("table", { name: "Pedidos por embalar" });
    expect(within(prepTable).getByRole("link", { name: "BOP-1" })).toHaveAttribute("href", "/erp/sat/o1");
    expect(within(prepTable).getByText("Ana Pi · Duplicoder SL")).toBeInTheDocument();
    expect(within(prepTable).getByText("boprint")).toBeInTheDocument();
    expect(within(prepTable).getByRole("button", { name: /Descargar albarán/ })).toBeInTheDocument();
    expect(window.localStorage.getItem("bohub.sat.queue.view")).toBe("list");

    await user.click(screen.getByRole("tab", { name: /^Embalados/ }));
    const readyTable = await screen.findByRole("table", { name: "Pedidos embalados" });
    expect(within(readyTable).getByRole("button", { name: /Imprimir albarán/ })).toBeInTheDocument();
    expect(within(readyTable).getByRole("button", { name: /Imprimir etiqueta/ })).toBeInTheDocument();
    expect(within(readyTable).getByRole("button", { name: "Reabrir preparación" })).toBeInTheDocument();
    const calls = mockQueue.mock.calls.length;
    await user.click(within(readyTable).getByRole("button", { name: /Marcar recogido/ }));
    await user.click(within(readyTable).getByRole("button", { name: "Sí, recogido" }));
    await waitFor(() => expect(mockPicked).toHaveBeenCalledWith("o2"));
    // Recogido EN SU SITIO: la fila queda como enviada, sin recargar la cola.
    expect(await within(readyTable).findByText("Recogido · en tránsito")).toBeInTheDocument();
    expect(mockQueue.mock.calls.length).toBe(calls);
    expect(screen.getByRole("tab", { name: "Embalados 0" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Tarjetas" }));
    expect(window.localStorage.getItem("bohub.sat.queue.view")).toBe("cards");
  });

  it("arranca en vista lista si así quedó guardado", async () => {
    window.localStorage.setItem("bohub.sat.queue.view", "list");
    render(<SatQueuePage />);
    expect(await screen.findByRole("table", { name: "Pedidos por embalar" })).toBeInTheDocument();
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
    const prepTable = await screen.findByRole("table", { name: "Pedidos por embalar" });
    const rows = within(prepTable).getAllByRole("row");
    expect(within(rows[1]).getByRole("button", { name: /Descargar albarán/ })).toBeInTheDocument();
    expect(within(rows[1]).queryByText(/Falta/)).not.toBeInTheDocument();
    expect(within(rows[2]).getByRole("link", { name: /Falta albarán/ })).toHaveAttribute("href", "/erp/orders/o3");
    expect(within(rows[2]).queryByRole("button", { name: /Descargar albarán/ })).not.toBeInTheDocument();
    expect(within(rows[3]).getByRole("link", { name: /Albarán de WooCommerce no disponible/ }))
      .toHaveAttribute("href", "/erp/orders/o4");
    expect(within(rows[3]).getByText(reason)).toBeInTheDocument();
    expect(within(rows[3]).queryByText(/Falta albarán/)).not.toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: /^Embalados/ }));
    const readyTable = await screen.findByRole("table", { name: "Pedidos embalados" });
    expect(within(readyTable).getByRole("link", { name: /Falta albarán/ })).toHaveAttribute("href", "/erp/orders/o5");
  });

  it("la vista lista enseña los datos técnicos (con copiar) y las observaciones encima de la fila", async () => {
    window.localStorage.setItem("bohub.sat.queue.view", "list");
    mockQueue.mockResolvedValue(queue({ por_embalar: [
      item({ notes: "Cliente pide manual en alemán.", serial_number: "FLX-7741-2026",
             shipping_origin: "SAT" }),
      manualItem(),
    ] }));
    render(<SatQueuePage />);
    const table = await screen.findByRole("table", { name: "Pedidos por embalar" });
    expect(within(table).getByRole("columnheader", { name: "Datos técnicos" })).toBeInTheDocument();
    const rows = within(table).getAllByRole("row");
    expect(rows).toHaveLength(4);
    expect(rows[1]).toHaveClass("sat-row-notes");
    expect(within(rows[1]).getByRole("note", { name: "Observaciones del comercial" }))
      .toHaveTextContent("Cliente pide manual en alemán.");
    expect(within(rows[2]).getByText("FLX-7741-2026")).toHaveClass("sat-tech-value");
    expect(within(rows[2]).getByRole("button", { name: "Copiar nº de serie" })).toBeInTheDocument();
    expect(within(rows[2]).queryByText("SAT")).not.toBeInTheDocument();
    expect(rows[3]).not.toHaveClass("sat-row-notes");
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
    await waitFor(() => expect(mockFind).toHaveBeenCalledWith("bop-9"));
    await waitFor(() => expect(mockEnqueue).toHaveBeenCalledWith("o9"));
    expect(await screen.findByRole("status")).toHaveTextContent(
      "BOP-9 (Nueva SL) añadido a la Cola SAT (aprobado).",
    );
    await waitFor(() => expect(mockQueue.mock.calls.length).toBeGreaterThan(calls));
    expect(screen.getByLabelText("Número de pedido a añadir")).toHaveValue("");
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

  it("«Todos pendientes» enseña los de por hacer y los embalados a la vez (dos columnas)", async () => {
    const user = userEvent.setup();
    render(<SatQueuePage />);
    await loaded();
    await user.click(screen.getByRole("tab", { name: /Todos pendientes/ }));
    const panel = screen.getByRole("tabpanel", { name: "Todos pendientes" });
    const titles = within(panel).getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
    expect(titles.some((t) => t?.includes("Por embalar"))).toBe(true);
    expect(titles.some((t) => t?.includes("Embalados"))).toBe(true);
    expect(within(panel).getByRole("button", { name: "▶ Empezar preparación" })).toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: /Marcar recogido/ })).toBeInTheDocument();
    expect(panel.querySelectorAll(".sat-scroll")).toHaveLength(2);
  });

  it("«Lista» se aplica también a «Todos pendientes»: cada columna es una tabla", async () => {
    const user = userEvent.setup();
    window.localStorage.setItem("bohub.sat.queue.view", "list");
    render(<SatQueuePage />);
    await loaded();
    await user.click(screen.getByRole("tab", { name: /Todos pendientes/ }));
    const panel = screen.getByRole("tabpanel", { name: "Todos pendientes" });
    const prep = within(panel).getByRole("table", { name: "Pedidos por embalar y en preparación" });
    const ready = within(panel).getByRole("table", { name: "Pedidos embalados y pendientes de recogida" });
    expect(within(prep).getAllByRole("columnheader").map((h) => h.textContent)).toEqual(
      ["Nº", "Cliente", "Tienda", "Estado", "Datos técnicos", "Acciones"],
    );
    expect(within(prep).getByRole("link", { name: "BOP-1" })).toHaveAttribute("href", "/erp/sat/o1");
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

  it("cada pestaña tiene su contenedor de scroll y pinta todos los pedidos dentro", async () => {
    window.localStorage.setItem("bohub.sat.queue.view", "list");
    const many = Array.from({ length: 5 }, (_, i) => item({ id: `p${i}`, order_number: `BOP-${i}` }));
    mockQueue.mockResolvedValue(queue({ por_embalar: many, embalados: [packedItem()] }));
    render(<SatQueuePage />);
    const prepTable = await screen.findByRole("table", { name: "Pedidos por embalar" });
    const prepScroll = screen.getByRole("tabpanel", { name: "Por embalar" }).querySelector(".sat-scroll");
    expect(prepScroll).toContainElement(prepTable);
    many.forEach((o) =>
      expect(within(prepScroll as HTMLElement).getByRole("link", { name: o.order_number })).toBeInTheDocument(),
    );
  });

  it("sube la etiqueta desde una fila de «Embalados» y la fila pasa a imprimirla (en su sitio)", async () => {
    const user = userEvent.setup();
    window.localStorage.setItem("bohub.sat.queue.view", "list");
    const base = packedItem({
      id: "o7", order_number: "BOP-7", albaran_file_source: "manual_upload",
      is_web_order: false, has_etiqueta: false,
    });
    mockQueue.mockResolvedValue(queue({ embalados: [base] }));
    mockItem.mockResolvedValue({ ...base, has_etiqueta: true, sat_tab: "pendiente_recogida",
                                 transport_status: "label_created" });
    mockUpload.mockResolvedValue({
      file: {
        id: "f", kind: "etiqueta", source: "manual_upload", filename: "e.pdf",
        mime_type: "application/pdf", size_bytes: 1, uploaded_by_user_id: null,
        uploaded_at: null, download_url: "/x",
      },
      transition_applied: true, transport_status: "label_created", transition_reason: null,
    });
    render(<SatQueuePage />);
    await user.click(await screen.findByRole("tab", { name: /^Embalados/ }));
    const readyTable = await screen.findByRole("table", { name: "Pedidos embalados" });
    const pdf = new File(["%PDF-"], "e.pdf", { type: "application/pdf" });
    await user.upload(within(readyTable).getByLabelText(/Subir etiqueta/), pdf);
    await waitFor(() => expect(mockUpload).toHaveBeenCalledWith("o7", "etiqueta", pdf));
    expect(await screen.findByRole("button", { name: /Imprimir etiqueta/ })).toBeInTheDocument();
    expect(screen.queryByLabelText(/Subir etiqueta/)).not.toBeInTheDocument();
    // Con la etiqueta puesta ya cuenta como «Pendiente de recogida».
    expect(screen.getByRole("tab", { name: "Pendiente de recogida 1" })).toBeInTheDocument();
  });
});
