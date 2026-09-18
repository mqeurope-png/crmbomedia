import { render, screen, waitFor, within } from "@testing-library/react";
import SeguimientoPageView from "./page";
import { listSeguimiento } from "../../lib/erpApi";

jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ children }: { children: React.ReactNode }) => <span>{children}</span>,
}));
jest.mock("../../components/PageHeader", () => ({
  PageHeader: ({ title }: { title: string }) => <h1>{title}</h1>,
}));
jest.mock("../../lib/api", () => ({
  getCurrentUser: jest.fn(() => Promise.resolve({ role: "admin" })),
}));
jest.mock("../../lib/erpApi", () => ({
  ERP_EDIT_ROLES: ["admin", "pedidos"],
  listSeguimiento: jest.fn(),
  getErpSettings: jest.fn(() => Promise.resolve({ shipping_origins: [] })),
  exportSeguimientoXlsx: jest.fn(),
  excludeSeguimiento: jest.fn(),
  includeSeguimiento: jest.fn(),
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

function row(over: Record<string, unknown> = {}) {
  return {
    id: "ord-1", order_number: "ART-000123", serie: 5,
    empresa: "Streamtec", empresa_corta: "ST", fecha: "2026-09-04",
    cliente: "DUPLICODER SL", vendedor: "WEB", origen: "OFI",
    transportista: "UPS", preparado: null, recogido: null,
    fecha_envio_factura: null, productos: "1× Artículo",
    proforma: null, albaran_pedido: "ART-000123", factura: "5-260050",
    tracking: null, num_serie: null, whiterip: null, orden: null,
    estado: "facturado", en_curso: true, excluido: false,
    excluido_en: null, excluido_por: null, excluido_motivo: null,
    escrito_drive: false, pendiente_escribir: true, woo_status: null,
    oculto_por_estado: false, estado_woo_motivo: null, reembolsado: false,
    situacion: "por_cobrar", situacion_label: "Por cobrar",
    situacion_tone: "b", importe: 113.36, moneda: "EUR",
    empresa_serie: "5 · Streamtec", fecha_factura: "2026-09-04",
    factura_enviada: null, cobro: "pendiente", cobro_label: "Pendiente",
    preparacion: "En cola", envio: "Sin enviar", origen_label: "WEB",
    serie_whiterip: "", nota_incidencia: "", incidencia: null,
    ...over,
  };
}

function mockRows(items: ReturnType<typeof row>[]) {
  (listSeguimiento as jest.Mock).mockResolvedValue({
    items, total: items.length,
    columns: [], incidencias_columns: [],
    drive: { configured: false, service_account_email: null, spreadsheet_id: null },
  });
}

describe("ERP · Seguimiento — hoja simplificada (rediseño 2026)", () => {
  beforeEach(() => (listSeguimiento as jest.Mock).mockReset());

  it("muestra las columnas nuevas y el estado en la columna Situación", async () => {
    mockRows([row()]);
    render(<SeguimientoPageView />);
    // Cabeceras (sin la flecha de orden ni la casilla de selección).
    await screen.findByText("Por cobrar");
    const heads = screen.getAllByRole("columnheader")
      .map((h) => (h.textContent ?? "").replace(/[↑↓]/g, "").trim());
    for (const label of [
      "Situación", "Nº pedido", "Origen", "Importe", "Empresa (serie)",
      "Fecha factura", "Factura enviada", "Cobro", "Preparación", "Envío",
      "Nº serie · WhiteRIP", "Nota / Incidencia",
    ]) {
      expect(heads).toContain(label);
    }
    // Situación coloreada (celda con tono azul «por cobrar»).
    const pill = await screen.findByText("Por cobrar");
    expect(pill).toHaveClass("seg-situacion", "is-b");
    // Empresa (serie) e Importe con formato €.
    expect(screen.getByText("5 · Streamtec")).toBeInTheDocument();
    expect(screen.getByText(/113,36\s*€/)).toBeInTheDocument();
  });

  it("un pedido «No requiere envío» muestra Preparación/Envío = No aplica", async () => {
    mockRows([row({
      order_number: "ART-000999", situacion: "listo", situacion_label: "Listo",
      situacion_tone: "g", preparacion: "No aplica", envio: "No aplica",
      cobro: "cobrado", cobro_label: "Cobrado ✓",
    })]);
    render(<SeguimientoPageView />);
    await screen.findByText("Listo");
    expect(screen.getAllByText("No aplica")).toHaveLength(2);   // Preparación + Envío
    const cobro = screen.getByText("Cobrado ✓");
    expect(cobro).toHaveClass("seg-cobro", "is-cobrado");
  });

  it("una incidencia se pinta en rojo y su nota va en Nota / Incidencia", async () => {
    mockRows([row({
      situacion: "incidencias", situacion_label: "Incidencia", situacion_tone: "r",
      nota_incidencia: "«DUPLICODER SL» no está vinculada a un cliente de FACTUSOL.",
      incidencia: {
        tipo: "Bloqueo", motivo: "empresa sin vincular",
        asignado: "", fecha: "2026-09-04", estado: "Abierta",
      },
    })]);
    render(<SeguimientoPageView />);
    const pill = await screen.findByText("Incidencia");
    expect(pill).toHaveClass("seg-situacion", "is-r");
    const table = screen.getByRole("table");
    expect(within(table).getByText(/no está vinculada a un cliente de FACTUSOL/))
      .toBeInTheDocument();
  });

  it("por defecto pide el orden por Situación", async () => {
    mockRows([row()]);
    render(<SeguimientoPageView />);
    await waitFor(() => expect(listSeguimiento).toHaveBeenCalled());
    expect((listSeguimiento as jest.Mock).mock.calls[0][0]).toMatchObject({
      sort: "situacion", dir: "desc",
    });
  });
});
