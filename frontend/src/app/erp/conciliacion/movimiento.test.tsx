import { render, screen, within } from "@testing-library/react";
import ConciliacionPage from "./page";
import { getCurrentUser, type User } from "../../lib/api";
import {
  listBankAccounts,
  listBankMovements,
  listBankRules,
  listSuggestedBankAccounts,
  type BankMovement,
  type BankMovementsPage,
} from "../../lib/erpApi";

/** El camino de vuelta del movimiento al pedido: el enlace a la ficha desde
 *  la factura, `?movimiento=` (el «Ver el movimiento» de la ficha), la fecha
 *  valor y lo confirmado desde el pedido. Datos figurantes. */

let mockSearchParams = new URLSearchParams();
jest.mock("next/navigation", () => ({
  useSearchParams: () => mockSearchParams,
}));
jest.mock("../../lib/api", () => ({ getCurrentUser: jest.fn() }));
jest.mock("../../lib/errors", () => ({
  extractErrorMessage: (err: unknown, fallback: string) =>
    err instanceof Error && err.message ? err.message : fallback,
}));
jest.mock("../../lib/erpApi", () => ({
  confirmAllHighBank: jest.fn(),
  confirmBankMovement: jest.fn(),
  createBankAccount: jest.fn(),
  createBankRule: jest.fn(),
  deleteBankAccount: jest.fn(),
  deleteBankRule: jest.fn(),
  discardBankMovement: jest.fn(),
  downloadBankCobrosCsv: jest.fn(),
  downloadBankExport: jest.fn(),
  escribirBankDrive: jest.fn(),
  getBankDrive: jest.fn().mockResolvedValue({
    configured: false, service_account_email: null, hora: "05:20",
  }),
  getContrapartidas: jest.fn().mockResolvedValue([]),
  importBankStatement: jest.fn(),
  listBankAccounts: jest.fn(),
  listBankMovements: jest.fn(),
  listBankRules: jest.fn(),
  listFactusolDocuments: jest.fn(),
  listSuggestedBankAccounts: jest.fn(),
  reassignBankMovement: jest.fn(),
  reopenBankMovement: jest.fn(),
  runBankMatch: jest.fn(),
  saveBlob: jest.fn(),
  traerBankDrive: jest.fn(),
  updateBankAccount: jest.fn(),
}));

const mov = (over: Partial<BankMovement>): BankMovement => ({
  id: "m1", account_id: "a1", fecha_oper: "2026-10-02", fecha_valor: "2026-10-03",
  concepto: "ABONO TRANSFERENCIA DE ROTULOS PEREZ", importe: 302.5, saldo: 1200,
  referencia1: null, referencia2: null, payer_name: "ROTULOS PEREZ SL",
  status: "reconciled", discard_reason: null, confidence: null, proposals: [],
  reconciled: [{
    id: "r1", serie: 5, codigo: 260120, numero: "5-260120", cliente_nombre: "Rótulos Pérez",
    importe: 302.5, confidence: "alta", reason: null, status: "confirmed", origen: "pedido",
    pedido: { id: "ord-15", order_number: "BOPRIN-100015" },
  }],
  ...over,
});

const PAGE: BankMovementsPage = {
  items: [
    mov({}),
    mov({
      id: "m2", fecha_oper: "2026-10-05", fecha_valor: "2026-10-05", status: "pending",
      concepto: "ABONO TRANSFERENCIA DE TALLER GIL", reconciled: [],
      proposals: [{
        id: "p2", serie: 5, codigo: 260121, numero: "5-260121", cliente_nombre: "Taller Gil",
        importe: 80, confidence: "media", reason: null, status: "proposed", origen: null,
        pedido: null,
      }],
    }),
  ],
  total: 2,
  counters: { pending: 1, reconciled: 1, discarded: 0, pending_amount: 80 },
};

beforeEach(() => {
  mockSearchParams = new URLSearchParams();
  (getCurrentUser as jest.Mock).mockResolvedValue({ id: "u1", role: "admin" } as User);
  (listBankAccounts as jest.Mock).mockResolvedValue([]);
  (listSuggestedBankAccounts as jest.Mock).mockResolvedValue([]);
  (listBankRules as jest.Mock).mockResolvedValue([]);
  (listBankMovements as jest.Mock).mockResolvedValue(PAGE);
});

describe("Conciliación bancaria — del movimiento al pedido", () => {
  it("la factura conciliada enlaza a la ficha de su pedido y dice que se confirmó desde ella", async () => {
    render(<ConciliacionPage />);
    const enlace = await screen.findByRole("link", { name: "BOPRIN-100015" });
    expect(enlace).toHaveAttribute("href", "/erp/orders/ord-15");
    expect(screen.getByTitle("Se confirmó desde la ficha del pedido"))
      .toHaveTextContent("desde la ficha del pedido");
    // La propuesta sin pedido de BoHub no lleva enlace.
    const fila = screen.getByText("ABONO TRANSFERENCIA DE TALLER GIL").closest("tr")!;
    expect(within(fila).getByText("5-260121")).toBeInTheDocument();
    expect(within(fila).queryByRole("link")).not.toBeInTheDocument();
  });

  it("enseña la fecha valor solo cuando la trae y es otra que la de operación", async () => {
    render(<ConciliacionPage />);
    expect(await screen.findByText("valor 03/10/2026")).toBeInTheDocument();
    const fila = screen.getByText("ABONO TRANSFERENCIA DE TALLER GIL").closest("tr")!;
    expect(within(fila).queryByText(/^valor /)).not.toBeInTheDocument();
  });

  it("sin ?movimiento= carga con los filtros de siempre", async () => {
    render(<ConciliacionPage />);
    await screen.findByRole("link", { name: "BOPRIN-100015" });
    expect(listBankMovements).toHaveBeenCalledWith({ status: "pending", limit: 200 });
    expect(screen.queryByText("Mostrando un solo movimiento")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Estado")).toBeInTheDocument();
  });

  it("con ?movimiento= carga solo ese, lo dice y «Ver todos» quita el filtro", async () => {
    mockSearchParams = new URLSearchParams("movimiento=m1");
    (listBankMovements as jest.Mock).mockResolvedValue({ ...PAGE, items: [PAGE.items[0]], total: 1 });
    render(<ConciliacionPage />);
    expect(await screen.findByText("Mostrando un solo movimiento")).toBeInTheDocument();
    // Sin el «Pendiente» por defecto: el movimiento puede estar ya conciliado.
    expect(listBankMovements).toHaveBeenCalledWith({ movimiento: "m1" });
    expect(listBankMovements).not.toHaveBeenCalledWith(expect.objectContaining({ status: "pending" }));
    expect(screen.getByRole("link", { name: "Ver todos" })).toHaveAttribute("href", "/erp/conciliacion");
    expect(screen.queryByLabelText("Estado")).not.toBeInTheDocument();
    expect(await screen.findByRole("link", { name: "BOPRIN-100015" })).toBeInTheDocument();
  });
});
