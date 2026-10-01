import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SeguimientoPageView from "./page";
import { exportSeguimientoXlsx, listSeguimiento } from "../../lib/erpApi";

/** ERP · Seguimiento — la tabla cabe o se desplaza SOLA (no la página).
 *
 *  Lo que jsdom puede comprobar: la tabla vive en su propia región con scroll,
 *  con la cabecera, las columnas fijas (casilla, Situación, Nº pedido,
 *  Cliente) y «Quitar» marcadas; Productos va en una línea truncada con el
 *  texto completo accesible; fechas cortas; y el selector «Columnas» oculta /
 *  enseña, se recuerda por usuario al recargar y NO toca el Excel. Lo visual
 *  (sin scroll de página a 1920 y 1366 px, barra visible, sombreado) se
 *  verifica en un navegador real (ver la descripción del PR). */

const mockUser: { current: Record<string, unknown> } = {
  current: { id: "u-1", email: "ana@example.com", role: "admin" },
};

jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ children }: { children: React.ReactNode }) => <span>{children}</span>,
}));
jest.mock("../../components/PageHeader", () => ({
  PageHeader: ({ title }: { title: string }) => <h1>{title}</h1>,
}));
jest.mock("../../lib/api", () => ({
  getCurrentUser: jest.fn(() => Promise.resolve(mockUser.current)),
}));
jest.mock("../../lib/erpApi", () => ({
  ERP_EDIT_ROLES: ["admin", "pedidos"],
  EXCLUSION_REASON_CODES: ["cancelado", "duplicado", "prueba", "reembolsado", "otro"],
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

const PRODUCTOS = "1× Impresora UV DTF A3 Procolored con kit de tintas, 2× Botella de tinta blanca 500 ml";

function row(over = {}) {
  return {
    id: "ord-1", order_number: "FLUXLA-5749", serie: 5,
    empresa: "Streamtec", empresa_corta: "ST", fecha: "2026-09-30",
    cliente: "La Rueca", vendedor: "WEB", origen: "OFI",
    transportista: null, preparado: null, recogido: null,
    fecha_envio_factura: null, productos: PRODUCTOS,
    proforma: null, albaran_pedido: "FLUXLA-5749", factura: null,
    tracking: null, num_serie: null, whiterip: null, orden: null,
    estado: "facturado", en_curso: true, excluido: false,
    excluido_en: null, excluido_por: null, excluido_motivo: null,
    excluido_por_nombre: null,
    escrito_drive: true, pendiente_escribir: false, woo_status: "processing",
    oculto_por_estado: false, estado_woo_motivo: null, reembolsado: false,
    situacion: "por_cobrar", situacion_label: "Por cobrar", situacion_tone: "b",
    importe: 100, moneda: "EUR", empresa_serie: "5 · Streamtec",
    fecha_factura: "2026-09-30", factura_enviada: null,
    cobro: "pendiente", cobro_label: "Pendiente",
    preparacion: "Listo", envio: "En tránsito", courier: "Ctt Premium", origen_label: "WEB",
    serie_whiterip: "", nota_incidencia: "", incidencia: null,
    ...over,
  };
}

function pageOf(items: ReturnType<typeof row>[]) {
  return {
    items, total: items.length, columns: [],
    drive: { configured: false, service_account_email: null, spreadsheet_id: null },
  };
}

function headerNames(table: HTMLElement): string[] {
  return within(table).getAllByRole("columnheader").map((th) => th.textContent?.trim() ?? "");
}

beforeEach(() => {
  window.localStorage.clear();
  mockUser.current = { id: "u-1", email: "ana@example.com", role: "admin" };
  (listSeguimiento as jest.Mock).mockReset();
  (listSeguimiento as jest.Mock).mockResolvedValue(pageOf([row()]));
  (exportSeguimientoXlsx as jest.Mock).mockReset();
  (exportSeguimientoXlsx as jest.Mock).mockResolvedValue(new Blob(["x"]));
});

describe("ERP · Seguimiento — tabla con scroll propio", () => {
  it("la tabla scrollea en su propia región, con la cabecera, las columnas fijas y «Quitar» marcadas", async () => {
    render(<SeguimientoPageView />);
    await screen.findByText("La Rueca");
    const region = screen.getByRole("region", { name: "Tabla de seguimiento" });
    expect(region).toHaveClass("scroll-table");
    expect(region).toHaveAttribute("tabindex", "0");   // con el teclado también se desplaza
    const table = within(region).getByRole("table");
    expect(table).toHaveClass("erp-seguimiento-table", "is-compact");

    // Fijas a la izquierda, en orden: casilla, Situación, Nº pedido, Cliente.
    const th = (name: string | RegExp) => within(table).getByRole("columnheader", { name });
    expect(th("Seleccionar todo")).toHaveClass("sticky-l", "sticky-l-0");
    expect(th("Situación")).toHaveClass("sticky-l", "sticky-l-1");
    expect(th(/Nº pedido/)).toHaveClass("sticky-l", "sticky-l-2");
    expect(th("Cliente")).toHaveClass("sticky-l", "sticky-l-3", "sticky-l-last");
    expect(th("Productos")).not.toHaveClass("sticky-l");
    // «Quitar», fija a la derecha (cabecera y fila).
    expect(th("Acciones")).toHaveClass("sticky-r");
    const quitar = screen.getByRole("button", { name: "Quitar FLUXLA-5749 del seguimiento" });
    expect(quitar.closest("td")).toHaveClass("sticky-r");
    // Las celdas de la fila llevan las mismas marcas que su cabecera.
    expect(screen.getByText("La Rueca").closest("td")).toHaveClass("sticky-l-3", "sticky-l-last");
  });

  it("Productos va en una línea truncada con el texto completo accesible (title + clic para desplegar)", async () => {
    const user = userEvent.setup();
    render(<SeguimientoPageView />);
    await screen.findByText("La Rueca");
    // Una sola copia del texto: el botón truncado.
    const trunc = screen.getByText(PRODUCTOS);
    expect(trunc.tagName).toBe("BUTTON");
    expect(trunc).toHaveClass("seg-trunc");
    expect(trunc).toHaveAttribute("title", PRODUCTOS);
    expect(trunc).toHaveAttribute("aria-expanded", "false");
    expect(screen.getAllByText(PRODUCTOS)).toHaveLength(1);
    await user.click(trunc);
    expect(trunc).toHaveAttribute("aria-expanded", "true");
    expect(trunc).toHaveClass("is-open");
    await user.click(trunc);
    expect(trunc).toHaveAttribute("aria-expanded", "false");
  });

  it("«No aplica» en Preparación / Envío sigue en gris; los estados reales no", async () => {
    (listSeguimiento as jest.Mock).mockResolvedValue(pageOf([
      row({ preparacion: "No aplica", envio: "No aplica" }),
      row({ id: "ord-2", order_number: "FLUXLA-5750", cliente: "Otra" }),
    ]));
    render(<SeguimientoPageView />);
    await screen.findByText("La Rueca");
    for (const cell of screen.getAllByText("No aplica")) {
      expect(cell.closest("td")).toHaveClass("muted", "small");
    }
    expect(screen.getByText("Listo").closest("td")).not.toHaveClass("muted");
    expect(screen.getByText("En tránsito").closest("td")).not.toHaveClass("muted");
  });

  it("las fechas van cortas (dd/mm/aa) con la completa en el title", async () => {
    render(<SeguimientoPageView />);
    await screen.findByText("La Rueca");
    const cells = screen.getAllByText("30/09/26");   // Fecha y Fecha factura
    expect(cells.length).toBeGreaterThanOrEqual(2);
    expect(cells[0].closest("td")).toHaveAttribute("title", "30/9/2026");
    expect(screen.queryByText("30/9/2026")).toBeNull();
  });

  it("«Columnas» oculta y enseña columnas, se recuerda al recargar (por usuario) y no toca el Excel", async () => {
    const user = userEvent.setup();
    const { unmount: unmountFirst } = render(<SeguimientoPageView />);
    await screen.findByText("La Rueca");
    const table = screen.getByRole("table");
    const all = headerNames(table);
    expect(all).toContain("Productos");

    // El Excel antes de tocar nada…
    await user.click(screen.getByRole("button", { name: "Descargar Excel" }));
    await waitFor(() => expect(exportSeguimientoXlsx).toHaveBeenCalledTimes(1));
    const filtrosAntes = (exportSeguimientoXlsx as jest.Mock).mock.calls[0][0];

    // Ocultar Productos y Tracking.
    await user.click(screen.getByRole("button", { name: "Columnas" }));
    const pop = screen.getByRole("group", { name: "Columnas visibles" });
    // Todas visibles por defecto, en el orden de la tabla; el Nº no se puede quitar.
    expect(within(pop).getAllByRole("checkbox").every((c) => (c as HTMLInputElement).checked)).toBe(true);
    expect(within(pop).getByRole("checkbox", { name: "Mostrar columna Nº pedido" })).toBeDisabled();
    await user.click(within(pop).getByRole("checkbox", { name: "Mostrar columna Productos" }));
    await user.click(within(pop).getByRole("checkbox", { name: "Mostrar columna Tracking" }));
    expect(headerNames(screen.getByRole("table"))).not.toContain("Productos");
    expect(headerNames(screen.getByRole("table"))).not.toContain("Tracking");
    expect(screen.queryByText(PRODUCTOS)).toBeNull();
    expect(screen.getByRole("button", { name: /Columnas \(17\/19\)/ })).toBeInTheDocument();
    expect(JSON.parse(window.localStorage.getItem("bohub.seguimiento.columnas.u-1") ?? "[]"))
      .toEqual(expect.arrayContaining(["productos", "tracking"]));

    // …y después: mismos filtros, nada de columnas (el Excel sigue con todas).
    await user.click(screen.getByRole("button", { name: "Descargar Excel" }));
    await waitFor(() => expect(exportSeguimientoXlsx).toHaveBeenCalledTimes(2));
    expect((exportSeguimientoXlsx as jest.Mock).mock.calls[1][0]).toEqual(filtrosAntes);

    // Recargar: la elección sigue.
    unmountFirst();
    const { unmount: unmountSecond } = render(<SeguimientoPageView />);
    await screen.findByText("La Rueca");
    await waitFor(() => expect(headerNames(screen.getByRole("table"))).not.toContain("Productos"));
    expect(headerNames(screen.getByRole("table"))).not.toContain("Tracking");

    // Otro usuario en el mismo navegador: lo suyo (todas visibles).
    unmountSecond();
    mockUser.current = { id: "u-2", email: "bea@example.com", role: "admin" };
    const { unmount: unmountThird } = render(<SeguimientoPageView />);
    await screen.findByText("La Rueca");
    expect(headerNames(screen.getByRole("table"))).toEqual(all);
    unmountThird();

    // «Mostrar todas» lo devuelve todo (y lo guarda).
    mockUser.current = { id: "u-1", email: "ana@example.com", role: "admin" };
    render(<SeguimientoPageView />);
    await screen.findByText("La Rueca");
    await waitFor(() => expect(headerNames(screen.getByRole("table"))).not.toContain("Productos"));
    await user.click(screen.getByRole("button", { name: /Columnas/ }));
    await user.click(screen.getByRole("button", { name: "Mostrar todas" }));
    expect(headerNames(screen.getByRole("table"))).toEqual(all);
    expect(window.localStorage.getItem("bohub.seguimiento.columnas.u-1")).toBe("[]");
  });

  it("con una columna fija oculta, las demás fijas se recolocan (offsets en orden)", async () => {
    window.localStorage.setItem("bohub.seguimiento.columnas.u-1", JSON.stringify(["situacion"]));
    render(<SeguimientoPageView />);
    await screen.findByText("La Rueca");
    const table = screen.getByRole("table");
    expect(headerNames(table)).not.toContain("Situación");
    expect(within(table).getByRole("columnheader", { name: /Nº pedido/ })).toHaveClass("sticky-l-1");
    expect(within(table).getByRole("columnheader", { name: "Cliente" })).toHaveClass("sticky-l-2", "sticky-l-last");
  });

  it("un valor guardado corrupto o con claves desconocidas no rompe: todas visibles", async () => {
    window.localStorage.setItem("bohub.seguimiento.columnas.u-1", "{no es json");
    const { unmount } = render(<SeguimientoPageView />);
    await screen.findByText("La Rueca");
    expect(headerNames(screen.getByRole("table"))).toContain("Productos");
    unmount();
    window.localStorage.setItem("bohub.seguimiento.columnas.u-1", JSON.stringify(["no-existe", 7]));
    render(<SeguimientoPageView />);
    await screen.findByText("La Rueca");
    expect(screen.getByRole("button", { name: "Columnas" })).toBeInTheDocument();   // sin «(n/19)»
  });
});
