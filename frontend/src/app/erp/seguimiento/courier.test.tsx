import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SeguimientoPageView from "./page";
import { listSeguimiento, syncSeguimientoDrive } from "../../lib/erpApi";

/** ERP · Seguimiento — columna «Courier» aparte y «Envío» solo con el estado.
 *
 *  La pantalla enseña Courier entre Envío y Fecha recogido (la agencia de
 *  Genei, el courier de la Cola SAT, «otro courier» o «—»), el selector
 *  «Columnas» la incluye (visible por defecto), el filtro «Transportista» se
 *  pide al backend (que filtra por Courier) y la vista previa de «Actualizar
 *  hoja de Drive…» avisa de que se añadirá la columna a la hoja. */

jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ children }: { children: React.ReactNode }) => <span>{children}</span>,
}));
jest.mock("../../components/PageHeader", () => ({
  PageHeader: ({ title }: { title: string }) => <h1>{title}</h1>,
}));
jest.mock("../../lib/api", () => ({
  getCurrentUser: jest.fn(() => Promise.resolve({ id: "u-1", role: "admin" })),
}));
jest.mock("../../lib/erpApi", () => ({
  ERP_EDIT_ROLES: ["admin", "pedidos"],
  EXCLUSION_REASON_CODES: ["cancelado", "otro"],
  isManagedSummary: (s: { mode?: string }) => s.mode === "managed_tab",
  listSeguimiento: jest.fn(),
  getErpSettings: jest.fn(() => Promise.resolve({ shipping_origins: [] })),
  exportSeguimientoXlsx: jest.fn(),
  excludeSeguimiento: jest.fn(),
  includeSeguimiento: jest.fn(),
  previewExcludeSeguimiento: jest.fn(),
  reconcileFactusolInvoices: jest.fn(),
  reconcileWooStatuses: jest.fn(),
  waitForFactusolReconcile: jest.fn(),
  waitForReconcileWoo: jest.fn(),
  syncSeguimientoDrive: jest.fn(),
  saveBlob: jest.fn(),
  downloadFacturasPdfZip: jest.fn(),
  downloadFactusolDocumentPdf: jest.fn(),
  getOrderFactusolInvoiceRef: jest.fn(),
}));

/** Resumen de la pestaña gestionada (vista previa o escritura). */
function managed(dryRun: boolean) {
  return {
    mode: "managed_tab", tab: "Seguimiento (app)", incidencias_tab: "Incidencias (app)",
    historic_tab: "", rows: 4, incidencias: 0, por_situacion: { Listo: 4 },
    columns: new Array(20).fill("x"), dry_run: dryRun, written: !dryRun,
    manuales: 0,
  };
}

function row(over: Record<string, unknown> = {}) {
  return {
    id: "ord-1", order_number: "BOP-1", serie: 5,
    empresa: "Streamtec", empresa_corta: "ST", fecha: "2026-09-04",
    cliente: "Acme SL", vendedor: "WEB", origen: "OFI",
    transportista: "Genei", preparado: null, recogido: "2026-09-05",
    fecha_envio_factura: null, productos: "1× Artículo",
    proforma: null, albaran_pedido: "BOP-1", factura: null,
    tracking: null, num_serie: null, whiterip: null, orden: null,
    estado: "enviado", en_curso: true, excluido: false,
    excluido_en: null, excluido_por: null, excluido_motivo: null,
    escrito_drive: true, pendiente_escribir: false, woo_status: null,
    oculto_por_estado: false, estado_woo_motivo: null, reembolsado: false,
    situacion: "listo", situacion_label: "Listo", situacion_tone: "g",
    importe: 100, moneda: "EUR", empresa_serie: "5 · Streamtec",
    fecha_factura: null, factura_enviada: null, cobro: "na", cobro_label: "—",
    preparacion: "Listo", envio: "Entregado", courier: "Ctt Premium",
    origen_label: "WEB", serie_whiterip: "", nota_incidencia: "", incidencia: null,
    ...over,
  };
}

const FILAS = [
  row(),
  row({ id: "ord-2", order_number: "BOP-2", albaran_pedido: "BOP-2", cliente: "Beta SL",
        envio: "Enviado", courier: "UPS", transportista: null }),
  row({ id: "ord-3", order_number: "BOP-3", albaran_pedido: "BOP-3", cliente: "Gamma SL",
        envio: "Enviado", courier: "otro courier", transportista: null }),
  row({ id: "ord-4", order_number: "BOP-4", albaran_pedido: "BOP-4", cliente: "Delta SL",
        preparacion: "No aplica", envio: "No aplica", courier: "—", transportista: null,
        recogido: null }),
];

function mockRows(conDrive = false) {
  (listSeguimiento as jest.Mock).mockResolvedValue({
    items: FILAS, total: FILAS.length, columns: [], incidencias_columns: [],
    drive: conDrive
      ? { configured: true, spreadsheet_id: "s-1", service_account_email: "sa@x.iam" }
      : { configured: false, service_account_email: null, spreadsheet_id: null },
  });
}

function cabeceras(): string[] {
  return screen.getAllByRole("columnheader")
    .map((h) => (h.textContent ?? "").replace(/[↑↓]/g, "").trim());
}

function filaDe(cliente: string): HTMLElement {
  return screen.getByText(cliente).closest("tr") as HTMLElement;
}

beforeEach(() => {
  window.localStorage.clear();
  (listSeguimiento as jest.Mock).mockReset();
  (syncSeguimientoDrive as jest.Mock).mockReset();
});

describe("ERP · Seguimiento — columna Courier", () => {
  it("Courier va entre Envío y Fecha recogido, y Envío lleva solo el estado", async () => {
    mockRows();
    render(<SeguimientoPageView />);
    await screen.findByText("Acme SL");
    const heads = cabeceras();
    const i = heads.indexOf("Envío");
    expect(heads.slice(i, i + 3)).toEqual(["Envío", "Courier", "Fecha recogido"]);

    const genei = filaDe("Acme SL");
    expect(within(genei).getByText("Entregado")).toHaveClass("seg-col-envio");
    expect(within(genei).getByText("Ctt Premium").closest("td")).toHaveClass("seg-col-courier");
    const ups = filaDe("Beta SL");
    expect(within(ups).getByText("Enviado")).toBeInTheDocument();
    expect(within(ups).getByText("UPS").closest("td")).toHaveClass("seg-col-courier");
    expect(within(filaDe("Gamma SL")).getByText("otro courier")).toBeInTheDocument();
    // Sin envío: «No aplica» (en gris) y Courier «—».
    const sin = filaDe("Delta SL");
    const courier = sin.querySelector("td.seg-col-courier");
    expect(courier).toHaveTextContent("—");
    expect(sin.querySelector("td.seg-col-envio")).toHaveClass("muted");
    // Ninguna celda de Envío lleva el courier dentro («Enviado · UPS» ya no existe).
    expect(screen.queryByText(/Enviado · /)).toBeNull();
  });

  it("el selector «Columnas» incluye Courier, visible por defecto, y la oculta", async () => {
    mockRows();
    const user = userEvent.setup();
    render(<SeguimientoPageView />);
    await screen.findByText("Acme SL");
    await user.click(screen.getByRole("button", { name: "Columnas" }));
    const pop = screen.getByRole("group", { name: "Columnas visibles" });
    const casilla = within(pop).getByRole("checkbox", { name: "Mostrar columna Courier" });
    expect(casilla).toBeChecked();
    // En su sitio dentro del orden fijo: entre Envío y Fecha recogido.
    const etiquetas = within(pop).getAllByRole("checkbox").map((c) => c.getAttribute("aria-label"));
    const j = etiquetas.indexOf("Mostrar columna Courier");
    expect(etiquetas[j - 1]).toBe("Mostrar columna Envío");
    expect(etiquetas[j + 1]).toBe("Mostrar columna Fecha recogido");
    await user.click(casilla);
    expect(cabeceras()).not.toContain("Courier");
    expect(screen.queryByText("Ctt Premium")).toBeNull();
    expect(JSON.parse(window.localStorage.getItem("bohub.seguimiento.columnas.u-1") ?? "[]"))
      .toEqual(["courier"]);
  });

  it("el filtro «Transportista» se pide al backend (que filtra por Courier)", async () => {
    mockRows();
    const user = userEvent.setup();
    render(<SeguimientoPageView />);
    await screen.findByText("Acme SL");
    const input = screen.getByRole("textbox", { name: "Filtrar por transportista" });
    expect(input).toHaveAttribute("title", expect.stringMatching(/Courier/));
    await user.type(input, "ups");
    await waitFor(() => expect(listSeguimiento).toHaveBeenLastCalledWith(
      expect.objectContaining({ transportista: "ups" }),
    ));
  });

  it("la vista previa de Drive avisa de que se añadirá la columna Courier", async () => {
    mockRows(true);
    (syncSeguimientoDrive as jest.Mock).mockResolvedValue({
      mode: "managed_tab", tab: "Seguimiento (app)", incidencias_tab: "Incidencias (app)",
      historic_tab: "", rows: 4, incidencias: 0, por_situacion: { Listo: 4 },
      columns: new Array(20).fill("x"), dry_run: true, written: false,
      migracion_courier: { estado: "pendiente", formato: "sin_courier", filas: 9000,
                           celdas_antes: 81234 },
    });
    const user = userEvent.setup();
    render(<SeguimientoPageView />);
    await user.click(await screen.findByRole("button", { name: /Actualizar hoja de Drive/ }));
    const aviso = await screen.findByText(/aún no tiene la columna/);
    expect(aviso).toHaveTextContent("«Courier»");
    expect(aviso).toHaveTextContent("entre «Envío» y «Fecha recogido»");
    expect(aviso).toHaveTextContent("81234 celdas");
    expect(screen.getByText(/Se reescribirá la pestaña/)).toHaveTextContent("20 columnas");
  });

  it("la vista previa avisa si la columna Z tiene algo (no cabría al insertar)", async () => {
    mockRows(true);
    (syncSeguimientoDrive as jest.Mock).mockResolvedValue({
      ...managed(true),
      migracion_courier: { estado: "pendiente", formato: "sin_courier", filas: 9000,
                           celdas_antes: 81234, celdas_que_no_caben: 3 },
    });
    const user = userEvent.setup();
    render(<SeguimientoPageView />);
    await user.click(await screen.findByRole("button", { name: /Actualizar hoja de Drive/ }));
    const alerta = await screen.findByRole("alert");
    expect(alerta).toHaveTextContent("La columna Z tiene 3 celda(s) con dato");
    expect(alerta).toHaveTextContent("sin escribir nada");
  });

  it("en la pestaña de 17 columnas el aviso es por las columnas Y y Z", async () => {
    mockRows(true);
    (syncSeguimientoDrive as jest.Mock).mockResolvedValue({
      ...managed(true),
      migracion_courier: { estado: "pendiente", formato: "sin_recogido", filas: 40,
                           celdas_antes: 300, celdas_que_no_caben: 2 },
    });
    const user = userEvent.setup();
    render(<SeguimientoPageView />);
    await user.click(await screen.findByRole("button", { name: /Actualizar hoja de Drive/ }));
    const alerta = await screen.findByRole("alert");
    expect(alerta).toHaveTextContent("Las columnas Y y Z tienen 2 celda(s) con dato");
  });

  it("una pestaña de 17 columnas se reescribe (sin prometer inserción ni recuento)", async () => {
    mockRows(true);
    (syncSeguimientoDrive as jest.Mock)
      .mockResolvedValueOnce({
        ...managed(true),
        migracion_courier: { estado: "pendiente", formato: "sin_recogido", filas: 40,
                             celdas_antes: 300, celdas_que_no_caben: 0 },
      })
      .mockResolvedValueOnce({
        ...managed(false),
        migracion_courier: { estado: "reescrita", formato: "sin_recogido", filas: 40 },
      });
    const user = userEvent.setup();
    render(<SeguimientoPageView />);
    await user.click(await screen.findByRole("button", { name: /Actualizar hoja de Drive/ }));
    const aviso = await screen.findByText(/formato antiguo \(17 columnas/);
    expect(aviso).toHaveTextContent("se reescribe entera con las 20 columnas");
    expect(screen.queryByText(/aún no tiene la columna/)).toBeNull();
    await user.click(screen.getByRole("button", { name: /Confirmar y escribir/ }));
    expect(await screen.findByText(/se ha reescrito con las 20/)).toBeInTheDocument();
  });

  it("tras migrar, «ninguna perdida» solo si el recuento cuadra", async () => {
    mockRows(true);
    (syncSeguimientoDrive as jest.Mock)
      .mockResolvedValueOnce({
        ...managed(true),
        migracion_courier: { estado: "pendiente", formato: "sin_courier", filas: 9000,
                             celdas_antes: 81234, celdas_que_no_caben: 0 },
      })
      .mockResolvedValueOnce({
        ...managed(false),
        migracion_courier: { estado: "hecha", formato: "sin_courier", filas: 9000,
                             celdas_antes: 81234, celdas_despues: 81234 },
      });
    const user = userEvent.setup();
    render(<SeguimientoPageView />);
    await user.click(await screen.findByRole("button", { name: /Actualizar hoja de Drive/ }));
    await user.click(await screen.findByRole("button", { name: /Confirmar y escribir/ }));
    const hecho = await screen.findByText(/Columna «Courier» añadida/);
    expect(hecho).toHaveTextContent("81234 celdas con dato antes y 81234 después");
    expect(hecho).toHaveTextContent("ninguna perdida");
  });

  it("la vista previa avisa de las filas de BoHub que se conservan", async () => {
    mockRows(true);
    (syncSeguimientoDrive as jest.Mock).mockResolvedValue({
      ...managed(true),
      migracion_courier: null,
      espejo: { filas_rescatadas: ["FAC-2-526109", "ARTISJ-9492"] },
    });
    const user = userEvent.setup();
    render(<SeguimientoPageView />);
    await user.click(await screen.findByRole("button", { name: /Actualizar hoja de Drive/ }));
    const aviso = await screen.findByText(/iban a desaparecer de la hoja/);
    expect(aviso).toHaveTextContent("2 pedido(s)");
    expect(aviso).toHaveTextContent("FAC-2-526109, ARTISJ-9492");
  });

  it("la vista previa dice qué filas repetidas se fusionan antes de escribir", async () => {
    mockRows(true);
    (syncSeguimientoDrive as jest.Mock).mockResolvedValue({
      ...managed(true),
      migracion_courier: null,
      espejo: { filas_fusionadas: ["BOP-200", "BOP-202"], valores_rellenados: 3,
                valores_en_conflicto: 1 },
    });
    const user = userEvent.setup();
    render(<SeguimientoPageView />);
    await user.click(await screen.findByRole("button", { name: /Actualizar hoja de Drive/ }));
    const aviso = await screen.findByText(/repetida\(s\) se fusionan/);
    expect(aviso).toHaveTextContent("2 fila(s) repetida(s)");
    expect(aviso).toHaveTextContent("3 valor(es) que faltaban se conservan");
    expect(aviso).toHaveTextContent("1 distinto(s) se descartan");
    expect(aviso).toHaveTextContent("BOP-200, BOP-202");
  });

  it("sin migración pendiente, la vista previa no avisa de nada", async () => {
    mockRows(true);
    (syncSeguimientoDrive as jest.Mock).mockResolvedValue({
      mode: "managed_tab", tab: "Seguimiento (app)", incidencias_tab: "Incidencias (app)",
      historic_tab: "", rows: 4, incidencias: 0, por_situacion: { Listo: 4 },
      columns: new Array(20).fill("x"), dry_run: true, written: false,
      migracion_courier: null,
    });
    const user = userEvent.setup();
    render(<SeguimientoPageView />);
    await user.click(await screen.findByRole("button", { name: /Actualizar hoja de Drive/ }));
    await screen.findByText(/Se reescribirá la pestaña/);
    expect(screen.queryByText(/aún no tiene la columna/)).toBeNull();
  });
});
