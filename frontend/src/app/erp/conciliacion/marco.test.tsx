import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ConciliacionPage from "./page";
import { getCurrentUser, type User } from "../../lib/api";
import {
  confirmAllHighBank,
  listBankAccounts,
  listBankMovements,
  listBankRules,
  listSuggestedBankAccounts,
  type BankMovement,
  type BankMovementsPage,
} from "../../lib/erpApi";

/** El marco de los cobros en Conciliación (29/10/2026): el ejercicio en curso
 *  por defecto y, sin la casilla de facturas externas, solo se ofrecen
 *  facturas con pedido. El extracto se enseña entero. Datos figurantes. */

jest.mock("../../lib/api", () => ({ getCurrentUser: jest.fn() }));
jest.mock("../../lib/errors", () => ({
  extractErrorMessage: (_e: unknown, fallback: string) => fallback,
}));
jest.mock("../../lib/erpApi", () => ({
  getEjercicios: jest.fn(() => Promise.resolve({ en_curso: "2026", ejercicios: ["2026", "2025"] })),
  anadirExportBankDrive: jest.fn(),
  confirmAllHighBank: jest.fn().mockResolvedValue({ confirmed: 0 }),
  confirmBankMovement: jest.fn(),
  createBankAccount: jest.fn(),
  createBankRule: jest.fn(),
  deleteBankAccount: jest.fn(),
  deleteBankRule: jest.fn(),
  discardBankMovement: jest.fn(),
  downloadBankCobrosCsv: jest.fn(),
  downloadBankExport: jest.fn(),
  escribirBankDrive: jest.fn(),
  getBankDrive: jest.fn().mockResolvedValue({ configured: false, service_account_email: null, hora: "05:20" }),
  getContrapartidas: jest.fn().mockResolvedValue([]),
  importBankStatement: jest.fn(),
  listBankAccounts: jest.fn(),
  listBankMovements: jest.fn(),
  listBankRules: jest.fn(),
  listFactusolDocuments: jest.fn().mockResolvedValue({ items: [], total: 0 }),
  listSuggestedBankAccounts: jest.fn(),
  reassignBankMovement: jest.fn(),
  reopenBankMovement: jest.fn(),
  runBankMatch: jest.fn(),
  saveBlob: jest.fn(),
  traerBankDrive: jest.fn(),
  updateBankAccount: jest.fn(),
}));

const mov = (over: Partial<BankMovement>): BankMovement => ({
  id: "m1", account_id: "a1", fecha_oper: "2026-10-20", fecha_valor: "2026-10-20",
  concepto: "ABONO TRANSFERENCIA FIGURANTE", importe: 408.48, saldo: 5691.81,
  referencia1: null, referencia2: null, payer_name: null, status: "pending",
  discard_reason: null, anotacion_factura: null, anotacion_pedido: null,
  confidence: null, proposals: [], reconciled: [], externas_ocultas: 0,
  ...over,
});

const PAGE: BankMovementsPage = {
  items: [
    mov({ id: "m1", concepto: "SOLO CASA CON UNA EXTERNA", externas_ocultas: 1 }),
    mov({ id: "m2", concepto: "DE VERDAD SIN NADA" }),
  ],
  total: 2,
  counters: { pending: 2, reconciled: 0, discarded: 0, pending_amount: 816.96, solo_externas: 1 },
};

const ultima = () => (listBankMovements as jest.Mock).mock.calls.at(-1)?.[0];

beforeEach(() => {
  jest.clearAllMocks();
  (getCurrentUser as jest.Mock).mockResolvedValue({ id: "u1", role: "admin" } as User);
  (listBankAccounts as jest.Mock).mockResolvedValue([]);
  (listSuggestedBankAccounts as jest.Mock).mockResolvedValue([]);
  (listBankRules as jest.Mock).mockResolvedValue([]);
  (listBankMovements as jest.Mock).mockResolvedValue(PAGE);
});

it("arranca en el ejercicio en curso; uno anterior se pide y se avisa", async () => {
  const user = userEvent.setup();
  render(<ConciliacionPage />);
  await screen.findByText("SOLO CASA CON UNA EXTERNA");
  expect(ultima()).toEqual(expect.objectContaining({ ejercicio: "2026", externas: false }));
  expect(screen.getByLabelText("Ejercicio")).toHaveValue("2026");
  await user.selectOptions(screen.getByLabelText("Ejercicio"), "2025");
  await waitFor(() => expect(ultima().ejercicio).toBe("2025"));
  expect(screen.getByRole("alert")).toHaveTextContent(
    "Ejercicio 2025, no el en curso (2026). Los movimientos son de 2025.");
  // Solo consulta: no se confirma nada.
  expect(screen.queryByRole("button", { name: "Confirmar todas las de confianza alta" }))
    .not.toBeInTheDocument();
});

it("sin la casilla, un movimiento que solo casa con externas dice que las hay; el contador filtra", async () => {
  const user = userEvent.setup();
  render(<ConciliacionPage />);
  const fila = (await screen.findByText("SOLO CASA CON UNA EXTERNA")).closest("tr") as HTMLElement;
  expect(within(fila).getByText("Sin propuesta")).toBeInTheDocument();
  expect(fila).toHaveTextContent(
    "Hay 1 factura externa (solo FACTUSOL, sin pedido) que encaja: no se ofrece con «Incluir facturas externas» apagada.");
  const otra = screen.getByText("DE VERDAD SIN NADA").closest("tr") as HTMLElement;
  expect(otra).not.toHaveTextContent("factura externa");
  // El extracto entero cuenta en «por conciliar».
  expect(screen.getByText(/^Por conciliar ·/)).toHaveTextContent(/816[.,]96/);
  // El contador se pulsa y deja la lista en esos.
  const chip = screen.getByRole("button", { name: /1 por conciliar solo casan con facturas externas/ });
  await user.click(chip);
  await waitFor(() => expect(ultima().solo_externas).toBe(true));
  expect(chip).toHaveAttribute("aria-pressed", "true");
  // «Confirmar las de confianza alta» confirma solo lo que se ve.
  jest.spyOn(window, "confirm").mockReturnValue(true);
  await user.click(screen.getByRole("button", { name: "Confirmar todas las de confianza alta" }));
  await waitFor(() => expect(confirmAllHighBank).toHaveBeenCalledWith(
    undefined, { externas: false, ejercicio: "2026" }));
});

it("con la casilla se piden también las externas, se distinguen, y no se apaga al recargar", async () => {
  const user = userEvent.setup();
  (listBankMovements as jest.Mock).mockResolvedValue({
    ...PAGE,
    items: [mov({ id: "m1", concepto: "SOLO CASA CON UNA EXTERNA", proposals: [{
      id: "p1", serie: 5, codigo: 260091, numero: "5-260091", cliente_nombre: "Cliente figurante",
      importe: 408.48, confidence: "alta", reason: "importe exacto", status: "proposed",
      origen: null, pedido: null,
    }] })],
    counters: { ...PAGE.counters, solo_externas: 0 },
  });
  render(<ConciliacionPage />);
  await screen.findByText("SOLO CASA CON UNA EXTERNA");
  const casilla = screen.getByRole("checkbox", { name: /Incluir facturas externas/ });
  expect(casilla).not.toBeChecked();
  await user.click(casilla);
  await waitFor(() => expect(ultima().externas).toBe(true));
  const fila = screen.getByText("SOLO CASA CON UNA EXTERNA").closest("tr") as HTMLElement;
  expect(within(fila).getByText("Externa · sin pedido")).toBeInTheDocument();
  // Otro filtro recarga la lista; la casilla sigue encendida.
  await user.selectOptions(screen.getByLabelText("Estado"), "reconciled");
  await waitFor(() => expect(ultima().status).toBe("reconciled"));
  expect(ultima().externas).toBe(true);
  expect(casilla).toBeChecked();
});
