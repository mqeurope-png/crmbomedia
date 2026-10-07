import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SeguimientoPageView from "./page";
import {
  listSeguimiento,
  reconcileWooStatuses,
  waitForReconcileWoo,
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

/** El 99976 de boprint: pagado en la tienda, sin webhook, no está en BoHub. */
const FALTA = {
  tienda: "boprint", tienda_nombre: "Boprint", woo_id: 99976, numero: "99976",
  estado: "processing", cliente: "Gráficas Norte SL (Laura Pérez)", importe: "129.00",
  moneda: "EUR", pagado_el: "2026-10-07T07:02:11+00:00",
  enlace: "https://boprint.example/wp-admin/post.php?post=99976&action=edit",
};

const BASE = {
  ok: true, scanned: 12, unchanged: 11, capped: false, limit: 20, woo_calls: 8,
  to_cancel: 1, to_fail: 0, to_trash: 0, to_unpaid: 0, to_refunded: 0,
  removed_total: 1, unknown_total: 0, to_filled: 0, to_not_found: 0,
  refreshed_total: 1, to_payment_method: 0, payment_method_by_store: {},
  payment_method_pending: 0, errors: [], samples: {},
};

beforeEach(() => {
  (listSeguimiento as jest.Mock).mockResolvedValue({
    items: [], total: 0, columns: [],
    drive: { configured: false, service_account_email: null, spreadsheet_id: null },
  });
  (reconcileWooStatuses as jest.Mock).mockReset();
  (reconcileWooStatuses as jest.Mock).mockResolvedValue({ job_id: "j", status: "queued" });
  (waitForReconcileWoo as jest.Mock).mockReset();
});

describe("ERP · Seguimiento — «Poner al día estados Woo…» importa los pagados que faltan", () => {
  it("la vista previa los lista con enlace a la tienda y al aplicar dice cuáles entraron", async () => {
    (waitForReconcileWoo as jest.Mock)
      .mockResolvedValueOnce({ status: "finished", result: {
        ...BASE, preview: true,
        missing: { dias: 90, faltan: 1, importados: 0, items: [{ ...FALTA, resultado: "a_importar" }],
                   errores: [], con_tope: false },
      } })
      .mockResolvedValueOnce({ status: "finished", result: {
        ...BASE, preview: false,
        missing: { dias: 90, faltan: 1, importados: 1, errores: [], con_tope: false,
                   items: [{ ...FALTA, resultado: "importado", order_id: "o1",
                             order_number: "BOPRIN-99976" }] },
      } });
    const user = userEvent.setup();
    render(<SeguimientoPageView />);
    await user.click(await screen.findByRole("button", { name: "Poner al día estados Woo…" }));

    const bloque = await screen.findByLabelText("Pedidos pagados que faltan");
    expect(bloque).toHaveTextContent(
      "1 pedidos pagados en la tienda no están en BoHub (últimos 90 días)",
    );
    const enlace = within(bloque).getByRole("link", { name: "Boprint #99976" });
    expect(enlace).toHaveAttribute("href", FALTA.enlace);
    expect(enlace).toHaveAttribute("target", "_blank");
    expect(bloque).toHaveTextContent("Gráficas Norte SL (Laura Pérez)");
    // 1 salida del seguimiento + 1 pedido a importar.
    await user.click(screen.getByRole("button", { name: "Aplicar (2 cambios)" }));
    await waitFor(() => expect(reconcileWooStatuses).toHaveBeenLastCalledWith({ preview: false }));
    const aviso = await screen.findByText(/Puesta al día aplicada/);
    expect(aviso).toHaveTextContent("1 pedidos conocidos puestos al día.");
    expect(aviso).toHaveTextContent(
      "Importados 1 pedidos pagados que faltaban en BoHub (BOPRIN-99976).",
    );
  });

  it("si no falta nada lo dice y si una importación falla avisa de que el Cuadre lo sigue", async () => {
    (waitForReconcileWoo as jest.Mock)
      .mockResolvedValueOnce({ status: "finished", result: {
        ...BASE, preview: true,
        missing: { dias: 90, faltan: 0, importados: 0, items: [], errores: [], con_tope: false },
      } })
      .mockResolvedValueOnce({ status: "finished", result: {
        ...BASE, preview: false,
        missing: { dias: 90, faltan: 1, importados: 0, con_tope: false,
                   errores: [{ store: "boprint", error: "boom" }],
                   items: [{ ...FALTA, resultado: "error", error: "boom" }] },
      } });
    const user = userEvent.setup();
    render(<SeguimientoPageView />);
    await user.click(await screen.findByRole("button", { name: "Poner al día estados Woo…" }));
    expect(await screen.findByLabelText("Pedidos pagados que faltan")).toHaveTextContent(
      "No falta ningún pedido pagado de los últimos 90 días.",
    );
    await user.click(screen.getByRole("button", { name: "Aplicar (1 cambios)" }));
    expect(await screen.findByText(/Puesta al día aplicada/)).toHaveTextContent(
      "1 no se pudieron importar (boprint #99976: boom): el Cuadre los sigue avisando.",
    );
  });
});
