import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SeguimientoPageView from "./page";
import {
  excludeSeguimiento,
  includeSeguimiento,
  listSeguimiento,
  previewExcludeSeguimiento,
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

function row(over = {}) {
  return {
    id: "ord-1", order_number: "FLUXLA-5749", serie: 5,
    empresa: "Streamtec", empresa_corta: "ST", fecha: "2026-09-01",
    cliente: "La Rueca", vendedor: "WEB", origen: "OFI",
    transportista: null, preparado: null, recogido: null,
    fecha_envio_factura: null, productos: "1× Artículo",
    proforma: null, albaran_pedido: "FLUXLA-5749", factura: "5-260001",
    tracking: null, num_serie: null, whiterip: null, orden: null,
    estado: "facturado", en_curso: true, excluido: false,
    excluido_en: null, excluido_por: null, excluido_motivo: null,
    excluido_por_nombre: null,
    escrito_drive: true, pendiente_escribir: false, woo_status: "refunded",
    oculto_por_estado: false, estado_woo_motivo: null, reembolsado: true,
    ...over,
  };
}

function pageOf(items: ReturnType<typeof row>[]) {
  return {
    items, total: items.length, columns: [],
    drive: { configured: false, service_account_email: null, spreadsheet_id: null },
  };
}

beforeEach(() => {
  (listSeguimiento as jest.Mock).mockReset();
  (listSeguimiento as jest.Mock).mockResolvedValue(pageOf([row()]));
  (previewExcludeSeguimiento as jest.Mock).mockReset();
  (previewExcludeSeguimiento as jest.Mock).mockResolvedValue({
    ok: true,
    items: [{
      order_id: "ord-1", order_number: "FLUXLA-5749", cliente: "La Rueca",
      woo_status: "refunded", excluido: false, excluido_motivo: null,
      avisos: ["facturado", "escrito en Drive"],
    }],
    con_avisos: 1, ya_excluidos: 0,
  });
  (excludeSeguimiento as jest.Mock).mockReset();
  (excludeSeguimiento as jest.Mock).mockResolvedValue({
    ok: true, excluded: 1, already_excluded: 0, reason: "reembolsado",
    avisos: { "FLUXLA-5749": ["facturado", "escrito en Drive"] }, con_avisos: 1,
  });
  (includeSeguimiento as jest.Mock).mockReset();
  (includeSeguimiento as jest.Mock).mockResolvedValue({
    ok: true, included: 1, already_included: 0,
  });
});

describe("ERP · Seguimiento — quitar / reincluir a mano", () => {
  it("el botón «Quitar» de la fila abre el diálogo, avisa (factura/Drive) y quita igualmente", async () => {
    const user = userEvent.setup();
    render(<SeguimientoPageView />);
    await screen.findByText("La Rueca");
    await user.click(screen.getByRole("button", { name: "Quitar FLUXLA-5749 del seguimiento" }));
    expect(
      await screen.findByRole("heading", { name: "Quitar FLUXLA-5749 del seguimiento" }),
    ).toBeInTheDocument();
    // Aviso: tiene factura y está «escrito en Drive», pero se puede continuar.
    expect(await screen.findByRole("alert")).toHaveTextContent("facturado, escrito en Drive");
    const confirm = screen.getByRole("button", { name: "Quitar igualmente (1)" });
    await waitFor(() => expect(confirm).toBeEnabled());
    await user.click(screen.getByRole("button", { name: "Reembolsado" }));
    await user.click(confirm);
    await waitFor(() =>
      expect(excludeSeguimiento).toHaveBeenCalledWith(["ord-1"], undefined, "reembolsado"),
    );
    expect(await screen.findByText(/1 pedido\(s\) quitado\(s\)/)).toBeInTheDocument();
    // El diálogo se cierra.
    expect(
      screen.queryByRole("heading", { name: "Quitar FLUXLA-5749 del seguimiento" }),
    ).not.toBeInTheDocument();
  });

  it("selección múltiple: las casillas de fix7 quitan varios con un motivo común", async () => {
    (listSeguimiento as jest.Mock).mockResolvedValue(pageOf([
      row(),
      row({ id: "ord-2", order_number: "BOPRIN-99922", albaran_pedido: "BOPRIN-99922",
            cliente: "Mookase", factura: null }),
    ]));
    (previewExcludeSeguimiento as jest.Mock).mockResolvedValue({
      ok: true, items: [], con_avisos: 0, ya_excluidos: 0,
    });
    (excludeSeguimiento as jest.Mock).mockResolvedValue({
      ok: true, excluded: 2, already_excluded: 0, reason: "prueba", avisos: {}, con_avisos: 0,
    });
    const user = userEvent.setup();
    render(<SeguimientoPageView />);
    await screen.findByText("Mookase");
    await user.click(screen.getByRole("checkbox", { name: "Seleccionar FLUXLA-5749" }));
    await user.click(screen.getByRole("checkbox", { name: "Seleccionar BOPRIN-99922" }));
    await user.click(screen.getByRole("button", { name: "Quitar del seguimiento (2)" }));
    // El diálogo tiene su propio «Quitar del seguimiento (2)»: se busca dentro.
    const dialog = within(await screen.findByRole("dialog"));
    expect(
      dialog.getByRole("heading", { name: "Quitar 2 pedidos del seguimiento" }),
    ).toBeInTheDocument();
    const confirm = await dialog.findByRole("button", { name: "Quitar del seguimiento (2)" });
    await waitFor(() => expect(confirm).toBeEnabled());
    await user.click(dialog.getByRole("button", { name: "Prueba" }));
    await user.click(confirm);
    await waitFor(() =>
      expect(excludeSeguimiento).toHaveBeenCalledWith(["ord-1", "ord-2"], undefined, "prueba"),
    );
  });

  it("en «Ver excluidos» se ve el motivo y «Reincluir» por fila lo deshace", async () => {
    (listSeguimiento as jest.Mock).mockResolvedValue(pageOf([row({
      excluido: true, excluido_en: "2026-09-10", excluido_por: "u-1",
      excluido_por_nombre: "Bart", excluido_motivo: "reembolsado: devuelto 10/9",
    })]));
    const user = userEvent.setup();
    render(<SeguimientoPageView />);
    await screen.findByText("La Rueca");
    await user.click(screen.getByRole("checkbox", { name: "Ver pedidos excluidos del seguimiento" }));
    await waitFor(() =>
      expect(listSeguimiento).toHaveBeenLastCalledWith(
        expect.objectContaining({ ver_excluidos: true }),
      ),
    );
    expect(await screen.findByText("reembolsado: devuelto 10/9")).toBeInTheDocument();
    expect(screen.getByText(/10\/9\/2026 · Bart/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Reincluir FLUXLA-5749 en el seguimiento" }));
    await waitFor(() => expect(includeSeguimiento).toHaveBeenCalledWith(["ord-1"]));
    expect(await screen.findByText(/1 pedido\(s\) reincluido\(s\)/)).toBeInTheDocument();
  });
});
