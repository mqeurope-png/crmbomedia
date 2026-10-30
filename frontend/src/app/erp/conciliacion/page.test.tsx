import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ConciliacionPage from "./page";
import { getCurrentUser, type User } from "../../lib/api";
import {
  downloadBankCobrosCsv,
  importBankStatement,
  listBankAccounts,
  listBankMovements,
  listBankRules,
  listSuggestedBankAccounts,
  saveBlob,
  type BankImportSummary,
  type BankMovement,
  type BankMovementsPage,
} from "../../lib/erpApi";

jest.mock("../../lib/api", () => ({
  getCurrentUser: jest.fn(),
}));
jest.mock("../../lib/errors", () => ({
  extractErrorMessage: (err: unknown, fallback: string) =>
    err instanceof Error && err.message ? err.message : fallback,
}));
jest.mock("../../lib/erpApi", () => ({
  getBankEjercicios: jest.fn(() => Promise.resolve({
    en_curso: "2026",
    anios: [
      { anio: "2026", movimientos: 3, pending: 2, reconciled: 1, discarded: 0, pending_amount: 10 },
      { anio: "2025", movimientos: 1, pending: 1, reconciled: 0, discarded: 0, pending_amount: 5 },
      { anio: "2019", movimientos: 4, pending: 4, reconciled: 0, discarded: 0, pending_amount: 7 },
    ],
    total: { movimientos: 8, pending: 7, reconciled: 1, discarded: 0, pending_amount: 22 },
  })),
  getEjercicios: jest.fn(() => Promise.resolve({ en_curso: "2026", ejercicios: ["2026", "2025"] })),
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
  id: "m1",
  account_id: "a1",
  fecha_oper: "2026-08-18",
  concepto: "ABONO TRANSFERENCIA DE CLARA",
  importe: 145.2,
  saldo: 955.25,
  referencia1: "271815809",
  referencia2: "CLARA FIGUERAS M",
  payer_name: "CLARA FIGUERAS M",
  status: "reconciled",
  discard_reason: null,
  anotacion_factura: "5-260083",
  anotacion_pedido: "BOP-099916",
  confidence: null,
  proposals: [],
  reconciled: [{
    id: "r1", serie: 5, codigo: 260083, numero: "5-260083", cliente_nombre: null,
    importe: 145.2, confidence: "alta", reason: "Venía anotado en el extracto («5-260083»)",
    status: "confirmed", origen: "extracto",
  }],
  ...over,
});

const PAGE: BankMovementsPage = {
  items: [
    mov({}),
    mov({
      id: "m2", concepto: "ABONO TRANSFERENCIA DE RAMON", status: "pending",
      anotacion_factura: "falta facturar", reconciled: [],
    }),
  ],
  total: 2,
  counters: { pending: 1, reconciled: 1, discarded: 0, pending_amount: 10 },
};

const RESUMEN: BankImportSummary = {
  ok: true,
  code: "imported",
  account_id: "a1",
  account_name: "Sabadell Streamtec",
  formato: "Sabadell",
  total_rows: 1273,
  imported: 1270,
  duplicates: 0,
  continuaciones: 1,
  no_entendidas_total: 3,
  no_entendidas: [
    { fila: 139, motivo: "fecha vacía", valores: ["956,46"] },
    { fila: 144, motivo: "importe no numérico: 'tres mil'", valores: ["OTRO", "tres mil"] },
    { fila: 802, motivo: "sin fecha ni importe, y no sigue a un movimiento", valores: ["NADIE"] },
  ],
  anotadas: { confirmadas: 119, propuestas: 11, sin_factura: 0 },
  resumen: "1.270 movimientos importados, 3 filas no entendidas (139, 144, 802)",
};

describe("Conciliación bancaria — los extractos de verdad", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    (getCurrentUser as jest.Mock).mockResolvedValue({ id: "u1", role: "admin" } as User);
    (listBankAccounts as jest.Mock).mockResolvedValue([]);
    (listSuggestedBankAccounts as jest.Mock).mockResolvedValue([]);
    (listBankRules as jest.Mock).mockResolvedValue([]);
    (listBankMovements as jest.Mock).mockResolvedValue(PAGE);
  });

  it("tras importar dice cuánto entró, lo anotado, y deja ver las filas no entendidas", async () => {
    const user = userEvent.setup();
    (importBankStatement as jest.Mock).mockResolvedValue(RESUMEN);
    render(<ConciliacionPage />);
    const input = await screen.findByLabelText("Extracto bancario");
    await user.upload(input, new File(["x"], "Streamtec movimientos SABADELL.xlsx"));
    expect(
      await screen.findByText("1.270 movimientos importados, 3 filas no entendidas (139, 144, 802)"),
    ).toBeInTheDocument();
    expect(screen.getByText(/Venían anotados en la columna factura/)).toHaveTextContent(
      "119 conciliados · 11 con una nota, para revisar",
    );
    expect(screen.queryByRole("table", { name: "Filas no entendidas" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Ver las 3 filas no entendidas" }));
    const tabla = screen.getByRole("table", { name: "Filas no entendidas" });
    expect(within(tabla).getAllByRole("row")).toHaveLength(4);
    expect(within(tabla).getByText("importe no numérico: 'tres mil'")).toBeInTheDocument();
    expect(within(tabla).getByText("OTRO · tres mil")).toBeInTheDocument();
  });

  it("enseña de dónde salió la conciliación y lo anotado que no es una factura", async () => {
    render(<ConciliacionPage />);
    expect(await screen.findByText("5-260083")).toBeInTheDocument();
    expect(screen.getByTitle("Bart lo anotó a mano en la columna factura del extracto"))
      .toHaveTextContent("anotado en el extracto");
    expect(screen.getByText("Venía anotado en el extracto («5-260083»)")).toBeInTheDocument();
    expect(screen.getByText("Anotado en el extracto: factura «falta facturar» · pedido «BOP-099916»"))
      .toBeInTheDocument();
  });

  it("descarga el CSV de cobros y dice cuántas facturas lleva y cuáles deja fuera", async () => {
    const user = userEvent.setup();
    const blob = new Blob(["serie,codigo,cuenta,forma,fecha,observaciones\n"]);
    (downloadBankCobrosCsv as jest.Mock).mockResolvedValue({
      blob, incluidas: 1, parciales: 2, noPendientes: 1, noCuadran: 0, sinContrapartida: 0,
    });
    render(<ConciliacionPage />);
    await user.click(await screen.findByRole("button", { name: "Descargar CSV de cobros" }));
    await waitFor(() => expect(saveBlob).toHaveBeenCalledWith(blob, "cobros_conciliados.csv"));
    expect(await screen.findByText(
      "CSV de cobros descargado: 1 factura. Fuera del CSV: 2 parciales (el script registraría "
      + "el saldo entero), 1 que no está pendiente en FACTUSOL; esas se registran a mano. "
      + "Pásalo a registrar_cobros_lote.ps1: simula primero y se salta lo ya cobrado.",
    )).toBeInTheDocument();
    expect(downloadBankCobrosCsv).toHaveBeenCalledWith({
      accountId: undefined, desde: undefined, hasta: undefined,
    });
  });

  it("sin FACTUSOL no hay CSV de cobros, y lo dice", async () => {
    const user = userEvent.setup();
    (downloadBankCobrosCsv as jest.Mock).mockRejectedValue(new Error(
      "No se ha podido consultar FACTUSOL: sin el saldo de cada factura no se sabe qué falta por cobrar.",
    ));
    render(<ConciliacionPage />);
    await user.click(await screen.findByRole("button", { name: "Descargar CSV de cobros" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/No se ha podido consultar FACTUSOL/);
    expect(saveBlob).not.toHaveBeenCalled();
  });
});
