import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SeguimientoPageView from "./page";
import {
  getLastReconcileWoo,
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
  getLastReconcileWoo: jest.fn(),
  describeReconcileProgress: jest.requireActual("../../lib/erpApi").describeReconcileProgress,
  syncSeguimientoDrive: jest.fn(),
  saveBlob: jest.fn(),
  downloadFacturasPdfZip: jest.fn(),
  downloadFactusolDocumentPdf: jest.fn(),
  getOrderFactusolInvoiceRef: jest.fn(),
}));

const RESULTADO = {
  ok: true, scanned: 86, unchanged: 84, capped: false, limit: 20, woo_calls: 90,
  to_cancel: 1, to_fail: 0, to_trash: 0, to_unpaid: 0, to_refunded: 1,
  removed_total: 1, unknown_total: 0, to_filled: 0, to_not_found: 0,
  to_payment_method: 0, payment_method_by_store: {}, payment_method_pending: 0,
  payment_method_samples: [], errors: [], samples: { cancelled: ["BOPRIN-1"] },
};

beforeEach(() => {
  (listSeguimiento as jest.Mock).mockResolvedValue({
    items: [], total: 0, columns: [],
    drive: { configured: false, service_account_email: null, spreadsheet_id: null },
  });
  (reconcileWooStatuses as jest.Mock).mockReset();
  (reconcileWooStatuses as jest.Mock).mockResolvedValue({ job_id: "j", status: "queued" });
  (waitForReconcileWoo as jest.Mock).mockReset();
  (getLastReconcileWoo as jest.Mock).mockReset();
  (getLastReconcileWoo as jest.Mock).mockResolvedValue({ job_id: null });
});

describe("ERP · Seguimiento — «Poner al día estados Woo…» espera a su trabajo", () => {
  it("dice por dónde va mientras trabaja y enseña el resultado al terminar", async () => {
    let terminar: (v: unknown) => void = () => undefined;
    (waitForReconcileWoo as jest.Mock).mockImplementation(
      (_id: string, opts: { onProgress?: (p: unknown) => void }) => {
        opts.onProgress?.({ fase: "Pedidos sin estado, uno a uno", hechos: 40, total: 86 });
        return new Promise((r) => { terminar = r; });
      });
    const user = userEvent.setup();
    render(<SeguimientoPageView />);
    await user.click(await screen.findByRole("button", { name: "Poner al día estados Woo…" }));
    expect(await screen.findByText(
      /Consultando WooCommerce… \(en segundo plano\) · Pedidos sin estado, uno a uno \(40 de 86\)/,
    )).toBeInTheDocument();
    terminar({ status: "finished", result: { ...RESULTADO, preview: true } });
    expect(await screen.findByRole("button", { name: /Aplicar \(\d+ cambios?\)/ }))
      .toBeInTheDocument();
    expect(screen.queryByText(/tardó demasiado/)).toBeNull();
  });

  it("al volver a entrar recupera el resultado de la última consulta", async () => {
    (getLastReconcileWoo as jest.Mock).mockResolvedValue({
      job_id: "j-ant", preview: true, status: "finished",
      result: { ...RESULTADO, preview: true }, ended_at: new Date().toISOString(),
    });
    render(<SeguimientoPageView />);
    expect(await screen.findByText(/Resultado de la última consulta \(hace un momento\)/))
      .toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Aplicar \(\d+ cambios?\)/ })).toBeInTheDocument();
    expect(reconcileWooStatuses).not.toHaveBeenCalled();
  });

  it("una pasada en marcha al entrar solo bloquea los botones de Woo", async () => {
    (getLastReconcileWoo as jest.Mock).mockResolvedValue({
      job_id: "j-otro", preview: true, status: "pending", progress: null,
    });
    (waitForReconcileWoo as jest.Mock).mockImplementation(() => new Promise(() => undefined));
    render(<SeguimientoPageView />);
    await waitFor(() => expect(waitForReconcileWoo).toHaveBeenCalled());
    expect(screen.getByRole("button", { name: "Trabajando…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Actualizar hoja de Drive|Actualizar la hoja/ }))
      .not.toBeDisabled();
  });

  it("si al entrar sigue trabajando, la pantalla sigue esperándolo", async () => {
    (getLastReconcileWoo as jest.Mock).mockResolvedValue({
      job_id: "j-vivo", preview: true, status: "pending",
      progress: { fase: "Estados de boprint: cancelled" },
    });
    (waitForReconcileWoo as jest.Mock).mockResolvedValue({
      status: "finished", result: { ...RESULTADO, preview: true },
    });
    render(<SeguimientoPageView />);
    await waitFor(() => expect(waitForReconcileWoo).toHaveBeenCalledWith(
      "j-vivo", expect.anything()));
    expect(await screen.findByRole("button", { name: /Aplicar \(\d+ cambios?\)/ }))
      .toBeInTheDocument();
  });

  it("si el trabajo ya no existe lo dice claro, sin «tardó demasiado»", async () => {
    (waitForReconcileWoo as jest.Mock).mockResolvedValue({ status: "missing" });
    const user = userEvent.setup();
    render(<SeguimientoPageView />);
    await user.click(await screen.findByRole("button", { name: "Poner al día estados Woo…" }));
    expect(await screen.findByText(/ya no está disponible/)).toBeInTheDocument();
  });
});
