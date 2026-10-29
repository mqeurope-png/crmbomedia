import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ConciliacionPage from "./page";
import { getCurrentUser, type User } from "../../lib/api";
import {
  escribirBankDrive,
  getBankDrive,
  importBankStatement,
  listBankAccounts,
  listBankMovements,
  listBankRules,
  listSuggestedBankAccounts,
  traerBankDrive,
  updateBankAccount,
  type BankAccount,
  type BankDriveTraida,
  type BankMovement,
} from "../../lib/erpApi";

/** El extracto del banco, por Drive: «Traer de Drive», el aviso con la
 *  cuenta de servicio, la hoja de cada cuenta y lo escrito en ella. Datos
 *  figurantes. */

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
  anadirExportBankDrive: jest.fn(),
  escribirBankDrive: jest.fn(),
  getBankDrive: jest.fn(),
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

const EMAIL = "bohub-seguimiento@ejemplo.iam.gserviceaccount.com";

const CUENTA: BankAccount = {
  id: "a1", name: "Sabadell Streamtec", bank_name: null, iban: "ES9121000418450200051332",
  bic: null, currency: "EUR", serie: 5, contrapartida_codigo: "8", contrapartida_nombre: null,
  column_mapping: null, has_statement_header: true,
  drive_file_id: "1HojaDeEjemploDelBancoSabadell0001", drive_pestana: null,
  drive_escribir: true, drive_formato: "hoja", drive_leida_at: "2026-10-30T04:20:00",
  drive_resultado: {
    origen: "programada", at: "2026-10-30T04:20:00", ok: true, formato: "hoja", imported: 2,
    duplicates: 1265, no_entendidas_total: 0,
    escritura: { escritas: 3, ocupadas: 1, at: "2026-10-30T04:20:05" },
  },
  drive_pendientes: 2,
};

const mov = (over: Partial<BankMovement>): BankMovement => ({
  id: "m1", account_id: "a1", fecha_oper: "2026-10-21",
  concepto: "ABONO TRANSFERENCIA DE IMPRENTA SOLER", importe: 2310, saldo: 8001.81,
  referencia1: null, referencia2: "IMPRENTA SOLER SL", payer_name: "IMPRENTA SOLER SL",
  status: "reconciled", discard_reason: null, anotacion_factura: null, anotacion_pedido: null,
  hoja_estado: "escrita", hoja_factura: "5-260090", hoja_pedido: "BOPRIN-100044",
  confidence: null, proposals: [],
  reconciled: [{
    id: "r1", serie: 5, codigo: 260090, numero: "5-260090", cliente_nombre: "Imprenta Soler",
    importe: 2310, confidence: null, reason: "elegida a mano", status: "confirmed", origen: null,
  }],
  ...over,
});

const TRAIDA: BankDriveTraida = {
  ok: true, code: "imported", account_id: "a1", account_name: "Sabadell Streamtec",
  total_rows: 8, imported: 2, duplicates: 6, formato: "Sabadell", no_entendidas_total: 0,
  no_entendidas: [], resumen: "2 movimientos importados",
  drive: { formato: "hoja", escritura: { escritas: 1, ocupadas: 1 } },
  matching: { ok: false, detail: "FACTUSOL no disponible: sin conexión" },
};

describe("Conciliación — el extracto por Drive", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    (getCurrentUser as jest.Mock).mockResolvedValue({ id: "u1", role: "admin" } as User);
    (getBankDrive as jest.Mock).mockResolvedValue({
      configured: true, service_account_email: EMAIL, hora: "05:20",
    });
    (listBankAccounts as jest.Mock).mockResolvedValue([CUENTA]);
    (listSuggestedBankAccounts as jest.Mock).mockResolvedValue([]);
    (listBankRules as jest.Mock).mockResolvedValue([]);
    (listBankMovements as jest.Mock).mockResolvedValue({
      items: [
        mov({}),
        mov({
          id: "m2", concepto: "ABONO TRANSFERENCIA DE TALLERES ROCA", hoja_estado: "no_encontrada",
          hoja_factura: null, hoja_pedido: null,
        }),
      ],
      total: 2, counters: { pending: 0, reconciled: 2, discarded: 0, pending_amount: 0 },
    });
  });

  it("«Traer de Drive» importa la hoja y dice lo leído y lo escrito, con el aviso de la cuenta de servicio", async () => {
    const user = userEvent.setup();
    (traerBankDrive as jest.Mock).mockResolvedValue([TRAIDA]);
    render(<ConciliacionPage />);
    expect(await screen.findByText(/Hojas de Drive conectadas con bohub-seguimiento@/))
      .toHaveTextContent("solo escribe en celdas vacías: nunca borra filas, ni reordena, ni pisa lo escrito a mano");
    await user.click(screen.getByRole("button", { name: "Traer de Drive" }));
    await waitFor(() => expect(traerBankDrive).toHaveBeenCalledWith(undefined));
    expect(await screen.findByText("2 movimientos importados")).toBeInTheDocument();
    expect(screen.getByText(/Leído de Drive \(hoja de Google\)/)).toHaveTextContent(
      "6 ya estaban · 1 escrito en la hoja · 1 con la celda «factura» ya ocupada (no se tocan)"
      + " · FACTUSOL no disponible: sin conexión",
    );
  });

  it("si Drive no responde, lo dice, y el fichero a mano sigue funcionando", async () => {
    const user = userEvent.setup();
    (traerBankDrive as jest.Mock).mockResolvedValue([{
      ok: false, code: "drive_caido", account_id: "a1", account_name: "Sabadell Streamtec",
      detail: "Drive no responde ahora (HTTP 503). Prueba en un rato; mientras, el fichero a mano sigue funcionando.",
      total_rows: 0, imported: 0, duplicates: 0,
    }]);
    (importBankStatement as jest.Mock).mockResolvedValue({
      ok: true, code: "imported", total_rows: 6, imported: 6, duplicates: 0,
      resumen: "6 movimientos importados",
    });
    render(<ConciliacionPage />);
    await user.click(await screen.findByRole("button", { name: "Traer de Drive" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Sabadell Streamtec: Drive no responde ahora (HTTP 503)",
    );
    await user.upload(screen.getByLabelText("Extracto bancario"), new File(["x"], "extracto.xlsx"));
    expect(await screen.findByText("6 movimientos importados")).toBeInTheDocument();
  });

  it("cada movimiento dice qué se escribió en la hoja, o por qué no", async () => {
    render(<ConciliacionPage />);
    expect(await screen.findByText("Escrito en la hoja: factura «5-260090» · pedido «BOPRIN-100044»"))
      .toBeInTheDocument();
    expect(screen.getByText("Este movimiento ya no está en la hoja de Drive: no se ha escrito nada."))
      .toBeInTheDocument();
  });

  it("en Cuentas: la hoja conectada, con quién compartirla, y cambiarla o escribir lo pendiente", async () => {
    const user = userEvent.setup();
    (updateBankAccount as jest.Mock).mockResolvedValue(CUENTA);
    (escribirBankDrive as jest.Mock).mockResolvedValue({ escritas: 2 });
    render(<ConciliacionPage />);
    await user.click(await screen.findByRole("button", { name: "Cuentas" }));
    const bloque = await screen.findByLabelText("Hoja de Drive de Sabadell Streamtec");
    expect(within(bloque).getByRole("link", { name: "hoja de Google" }))
      .toHaveAttribute("href", "https://docs.google.com/spreadsheets/d/1HojaDeEjemploDelBancoSabadell0001/edit");
    expect(bloque).toHaveTextContent("BoHub la lee y escribe la factura y el pedido");
    expect(bloque).toHaveTextContent("Última lectura (repaso diario");
    expect(bloque).toHaveTextContent("2 nuevos · 1265 ya estaban");
    expect(bloque).toHaveTextContent("2 conciliados pendientes de escribir en la hoja.");
    expect(screen.getByText(EMAIL)).toBeInTheDocument();

    await user.click(within(bloque).getByRole("button", { name: "Escribir ahora" }));
    expect(escribirBankDrive).toHaveBeenCalledWith("a1");
    expect(await within(bloque).findByText("2 escritos en la hoja")).toHaveAttribute("role", "status");
    // Lo que se hace en Cuentas refresca también la lista de Revisión.
    expect(listBankMovements).toHaveBeenCalledTimes(2);

    await user.click(within(bloque).getByRole("button", { name: "Cambiar la hoja de Drive" }));
    expect(within(bloque).getByText("Editor")).toBeInTheDocument();
    await user.click(within(bloque).getByLabelText(/Escribir la factura y el pedido en la hoja/));
    expect(within(bloque).getByText("Lector")).toBeInTheDocument();
    await user.type(within(bloque).getByLabelText(/Pestaña/), "Movimientos");
    await user.click(within(bloque).getByRole("button", { name: "Guardar" }));
    expect(updateBankAccount).toHaveBeenCalledWith("a1", {
      drive_file_id: "1HojaDeEjemploDelBancoSabadell0001",
      drive_pestana: "Movimientos",
      drive_escribir: false,
    });
  });

  it("un .xlsx subido: se lee, y avisa de que hay que convertirlo para que BoHub escriba", async () => {
    const user = userEvent.setup();
    (listBankAccounts as jest.Mock).mockResolvedValue([{ ...CUENTA, drive_formato: "xlsx" }]);
    render(<ConciliacionPage />);
    await user.click(await screen.findByRole("button", { name: "Cuentas" }));
    const bloque = await screen.findByLabelText("Hoja de Drive de Sabadell Streamtec");
    expect(within(bloque).getByRole("link", { name: ".xlsx subido" }))
      .toHaveAttribute("href", "https://drive.google.com/file/d/1HojaDeEjemploDelBancoSabadell0001/view");
    expect(bloque).toHaveTextContent("BoHub la lee; no puede escribir en un .xlsx");
    expect(bloque).toHaveTextContent("Archivo → Guardar como hoja de cálculo de Google");
    expect(within(bloque).queryByRole("button", { name: "Escribir ahora" })).not.toBeInTheDocument();
  });
});

it("si «Escribir ahora» falla, lo dice en rojo; y sin ninguna lectura no hay «Última lectura»", async () => {
  const user = userEvent.setup();
  jest.clearAllMocks();
  (getCurrentUser as jest.Mock).mockResolvedValue({ id: "u1", role: "admin" } as User);
  (getBankDrive as jest.Mock).mockResolvedValue({
    configured: true, service_account_email: EMAIL, hora: "05:20",
  });
  (listBankAccounts as jest.Mock).mockResolvedValue([{
    ...CUENTA,
    drive_resultado: { escritura: { error: "no compartida", at: "2026-10-30T09:00:00" } },
  }]);
  (listSuggestedBankAccounts as jest.Mock).mockResolvedValue([]);
  (listBankRules as jest.Mock).mockResolvedValue([]);
  (listBankMovements as jest.Mock).mockResolvedValue({ items: [], total: 0, counters: null });
  (escribirBankDrive as jest.Mock).mockResolvedValue({
    error: "bohub-seguimiento@… solo puede leer la hoja: compártela con ella como Editor",
  });
  render(<ConciliacionPage />);
  await user.click(await screen.findByRole("button", { name: "Cuentas" }));
  const bloque = await screen.findByRole("group", { name: "Hoja de Drive de Sabadell Streamtec" });
  expect(within(bloque).queryByText(/Última lectura/)).not.toBeInTheDocument();
  expect(within(bloque).getByText(/Última escritura/)).toHaveTextContent("no compartida");
  await user.click(within(bloque).getByRole("button", { name: "Escribir ahora" }));
  expect(await within(bloque).findByRole("alert")).toHaveTextContent("solo puede leer la hoja");
});
