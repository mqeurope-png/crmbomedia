import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SeguimientoPageView from "./page";
import {
  downloadFacturasPdfZip,
  downloadFactusolDocumentPdf,
  getOrderFactusolInvoiceRef,
  listSeguimiento,
  saveBlob,
} from "../../lib/erpApi";

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

function row(over = {}) {
  return {
    id: "ord-1", order_number: "ART-000123", serie: 5,
    empresa: "Streamtec", empresa_corta: "ST", fecha: "2026-08-26",
    cliente: "DUPLICODER SL", vendedor: "—", origen: "OFI",
    transportista: null, preparado: null, recogido: null,
    fecha_envio_factura: null, productos: "1× Artículo",
    proforma: null, albaran_pedido: "ART-000123", factura: "260063",
    tracking: null, num_serie: null, whiterip: null, orden: null,
    estado: "facturado", en_curso: false, excluido: false,
    excluido_en: null, excluido_por: null, excluido_motivo: null,
    escrito_drive: false, pendiente_escribir: true, woo_status: null,
    oculto_por_estado: false, estado_woo_motivo: null, reembolsado: false,
    ...over,
  };
}

beforeEach(() => {
  (listSeguimiento as jest.Mock).mockReset();
  (listSeguimiento as jest.Mock).mockResolvedValue({
    items: [row()], total: 1, columns: [],
    drive: { configured: false, service_account_email: null, spreadsheet_id: null },
  });
  (getOrderFactusolInvoiceRef as jest.Mock).mockReset();
  (getOrderFactusolInvoiceRef as jest.Mock).mockResolvedValue({
    serie: 5, codigo: 260063, numero: "5-260063",
  });
  (downloadFactusolDocumentPdf as jest.Mock).mockResolvedValue(new Blob());
  (downloadFacturasPdfZip as jest.Mock).mockResolvedValue(new Blob());
  (saveBlob as jest.Mock).mockReset();
});

describe("ERP · Seguimiento — descarga de PDF de factura", () => {
  it("el botón PDF de una fila con factura descarga su PDF", async () => {
    const user = userEvent.setup();
    render(<SeguimientoPageView />);
    await screen.findByText("260063");
    await user.click(screen.getByRole("button", { name: "PDF" }));
    await waitFor(() =>
      expect(getOrderFactusolInvoiceRef).toHaveBeenCalledWith("ord-1"),
    );
    expect(downloadFactusolDocumentPdf).toHaveBeenCalledWith("facturas", 5, 260063);
    expect(saveBlob).toHaveBeenCalled();
  });

  it("la selección múltiple baja las facturas de los seleccionados en ZIP", async () => {
    const user = userEvent.setup();
    render(<SeguimientoPageView />);
    await screen.findByText("260063");
    await user.click(screen.getByRole("checkbox", { name: "Seleccionar ART-000123" }));
    await user.click(
      await screen.findByRole("button", { name: /Descargar facturas \(PDF\)/ }),
    );
    await waitFor(() =>
      expect(downloadFacturasPdfZip).toHaveBeenCalledWith([{ serie: 5, codigo: 260063 }]),
    );
    expect(saveBlob).toHaveBeenCalledWith(expect.anything(), "facturas_pdf.zip");
  });
});
