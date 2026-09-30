import { render, screen, waitFor } from "@testing-library/react";
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

/** Nada cambia de estado en Woo, pero 3 pedidos web no tienen método de pago
 *  (importados antes de guardarlo): la puesta al día los rellena igual. */
const SIN_CAMBIOS = {
  ok: true, scanned: 12, unchanged: 12, capped: false, limit: 20, woo_calls: 20,
  to_cancel: 0, to_fail: 0, to_trash: 0, to_unpaid: 0, to_refunded: 0,
  removed_total: 0, unknown_total: 0, to_filled: 0, to_not_found: 0,
  to_payment_method: 3,
  payment_method_by_store: { "artisjet-europe": 2, boprint: 1 },
  payment_method_pending: 3,
  payment_method_samples: ["ARTISJ-9648 → Carte", "BOPRIN-99961 → PayPal"],
  errors: [], samples: {},
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

describe("ERP · Seguimiento — la puesta al día rellena el método de pago", () => {
  it("sin cambios de estado, enseña los métodos a rellenar y se pueden aplicar", async () => {
    (waitForReconcileWoo as jest.Mock)
      .mockResolvedValueOnce({ status: "finished", result: { ...SIN_CAMBIOS, preview: true } })
      .mockResolvedValueOnce({ status: "finished", result: { ...SIN_CAMBIOS, preview: false } });
    const user = userEvent.setup();
    render(<SeguimientoPageView />);
    await user.click(await screen.findByRole("button", { name: "Poner al día estados Woo…" }));
    expect(await screen.findByLabelText("Métodos de pago")).toHaveTextContent(
      "3 pedidos web sin método de pago lo rellenarían (cambien o no de estado) "
      + "(artisjet-europe 2 · boprint 1).",
    );
    // «0 cambios» de estado ya no esconde el relleno: cuenta como cambio.
    await user.click(screen.getByRole("button", { name: "Aplicar (3 cambios)" }));
    await waitFor(() => expect(reconcileWooStatuses).toHaveBeenLastCalledWith({ preview: false }));
    expect(await screen.findByText(
      /3 pedidos web rellenaron su método de pago \(artisjet-europe 2 · boprint 1\)\./,
    )).toBeInTheDocument();
  });
});
