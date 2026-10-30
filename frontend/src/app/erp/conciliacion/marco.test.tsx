import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ConciliacionPage from "./page";
import { getCurrentUser, type User } from "../../lib/api";
import {
  confirmAllHighBank,
  importBankStatement,
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

jest.mock("../../components/erp/RegistrarCobroModal", () => ({
  RegistrarCobroModal: ({ factura, movimientoId }: {
    factura: { numero: string }; movimientoId: string;
  }) => <div role="dialog" aria-label="cobro">COBRO {factura.numero} {movimientoId}</div>,
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

it("en un movimiento conciliado, «Registrar cobro» abre la herramienta con ese movimiento", async () => {
  const user = userEvent.setup();
  const rec = (numero: string, codigo: number, pedido: { id: string; order_number: string } | null) => ({
    id: `r-${codigo}`, serie: 5, codigo, numero, cliente_nombre: "Cliente figurante", importe: 100,
    confidence: "alta" as const, reason: "figurante", status: "confirmed" as const, origen: null, pedido,
  });
  (listBankMovements as jest.Mock).mockResolvedValue({
    ...PAGE,
    items: [
      mov({ id: "m9", concepto: "CONCILIADO CON PEDIDO", status: "reconciled",
            reconciled: [rec("5-260090", 260090, { id: "o-1", order_number: "BOPRIN-100090" })] }),
      mov({ id: "m8", concepto: "CONCILIADO CON EXTERNA", status: "reconciled",
            reconciled: [rec("5-260092", 260092, null)] }),
    ],
  });
  render(<ConciliacionPage />);
  await screen.findByText("CONCILIADO CON PEDIDO");
  // La externa, solo con la casilla.
  expect(screen.queryByRole("button", { name: "Registrar cobro de la 5-260092" })).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Registrar cobro de la 5-260090" }));
  expect(screen.getByRole("dialog", { name: "cobro" })).toHaveTextContent("COBRO 5-260090 m9");
});

it("el selector ofrece todos los años que tienen movimientos, con su recuento, y «Todos los años»", async () => {
  const user = userEvent.setup();
  render(<ConciliacionPage />);
  await screen.findByText("SOLO CASA CON UNA EXTERNA");
  const sel = screen.getByLabelText("Ejercicio");
  const opciones = within(sel).getAllByRole("option").map((o) => o.textContent);
  expect(opciones).toEqual([
    "Todos los años · 8 movimientos · 7 por conciliar",
    "2026 (en curso) · 3 movimientos · 2 por conciliar",
    "2025 · 1 movimientos · 1 por conciliar",
    "2019 · 4 movimientos · 4 por conciliar",   // un año que FACTUSOL no tiene
  ]);
  await user.selectOptions(sel, "todos");
  await waitFor(() => expect(ultima().ejercicio).toBeUndefined());
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  await user.selectOptions(sel, "2019");
  await waitFor(() => expect(ultima().ejercicio).toBe("2019"));
  expect(screen.getByRole("alert")).toHaveTextContent("Ejercicio 2019, no el en curso (2026)");
});

it("el campo de la hoja se ve sin cuenta elegida, deshabilitado y diciendo por qué", async () => {
  render(<ConciliacionPage />);
  await screen.findByText("SOLO CASA CON UNA EXTERNA");
  expect(screen.getByLabelText("Extracto bancario").closest("label")).toHaveTextContent(
    "Traer a BoHub · .xls / .xlsx / .csvMete los movimientos en BoHub. La hoja de Drive no se toca.");
  const hoja = screen.getByLabelText("Export del banco para la hoja");
  expect(hoja).toBeDisabled();
  expect(hoja.closest("label")).toHaveTextContent("Escribir en la hoja de Drive lo que falte");
  expect(hoja.closest("label")).toHaveTextContent("Elige arriba la cuenta");
});

it("traer a BoHub dice que en la hoja no se ha escrito nada si la cuenta tiene hoja", async () => {
  const user = userEvent.setup();
  (listBankAccounts as jest.Mock).mockResolvedValue([{
    id: "a1", name: "Sabadell Ejemplo", iban: "ES0000000000000000000001", drive_file_id: "hoja-1",
  }]);
  (importBankStatement as jest.Mock).mockResolvedValue({
    ok: true, code: "imported", account_id: "a1", account_name: "Sabadell Ejemplo",
    total_rows: 34, imported: 0, duplicates: 34, no_entendidas_total: 0, no_entendidas: [],
    resumen: "0 movimientos importados",
  });
  render(<ConciliacionPage />);
  await user.upload(await screen.findByLabelText("Extracto bancario"), new File(["x"], "export.xls"));
  expect(await screen.findByText(/no se ha escrito nada/)).toHaveTextContent(
    "En la hoja de Drive de Sabadell Ejemplo no se ha escrito nada: «Traer a BoHub» solo mete los movimientos en BoHub.");
});

it("con «Todos los años» no se ofrece cobrar un movimiento de otro año", async () => {
  const user = userEvent.setup();
  const rec = {
    id: "r-1", serie: 5, codigo: 250090, numero: "5-250090", cliente_nombre: "Cliente figurante",
    importe: 100, confidence: "alta" as const, reason: "figurante", status: "confirmed" as const,
    origen: null, pedido: { id: "o-1", order_number: "BOPRIN-100090" },
  };
  (listBankMovements as jest.Mock).mockResolvedValue({
    ...PAGE,
    items: [
      mov({ id: "m7", concepto: "CONCILIADO EN 2025", fecha_oper: "2025-12-20", status: "reconciled",
            reconciled: [rec] }),
      mov({ id: "m6", concepto: "CONCILIADO EN 2026", status: "reconciled",
            reconciled: [{ ...rec, id: "r-2", numero: "5-260090", codigo: 260090 }] }),
    ],
  });
  render(<ConciliacionPage />);
  await user.selectOptions(await screen.findByLabelText("Ejercicio"), "todos");
  await screen.findByText("CONCILIADO EN 2025");
  expect(screen.queryByRole("button", { name: "Registrar cobro de la 5-250090" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Registrar cobro de la 5-260090" })).toBeInTheDocument();
});
