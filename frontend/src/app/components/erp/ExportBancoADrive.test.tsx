import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ExportBancoADrive } from "./ExportBancoADrive";
import { anadirExportBankDrive, type BankExportPlan } from "../../lib/erpApi";

/** El export del banco a la hoja de Drive: vista previa y confirmar.
 *  Movimientos figurantes. */

jest.mock("../../lib/erpApi", () => ({ anadirExportBankDrive: jest.fn() }));
jest.mock("../../lib/errors", () => ({
  extractErrorMessage: (err: unknown, fallback: string) =>
    err instanceof Error && err.message ? err.message : fallback,
}));

const PREVIA: BankExportPlan = {
  total: 34, ya: 21, nuevos: 2, aplicado: false,
  movimientos: [
    { fecha_oper: "2026-10-27", concepto: "ABONO TRANSFERENCIA DE CRISTALERIA PUIG SL",
      importe: 650, saldo: 10492.11 },
    { fecha_oper: "2026-10-18", concepto: "ABONO TRANSFERENCIA DE FERRETERIA VIDAL",
      importe: 44.77, saldo: 5328.1 },
  ],
};

const fichero = () => new File(["xls"], "29102026_0202.xls", { type: "application/vnd.ms-excel" });

function pinta(onAnadido = jest.fn()) {
  render(<ExportBancoADrive accountId="a1" accountName="Sabadell Streamtec"
    onAnadido={onAnadido} />);
  return onAnadido;
}

beforeEach(() => jest.clearAllMocks());

test("la vista previa cuenta lo que trae, lo que ya está y lo que se añadirá", async () => {
  (anadirExportBankDrive as jest.Mock).mockResolvedValue(PREVIA);
  pinta();
  await userEvent.upload(screen.getByLabelText("Export del banco para la hoja"), fichero());
  expect(await screen.findByText("34 movimientos en el fichero")).toBeInTheDocument();
  expect(screen.getByLabelText("Vista previa del export")).toHaveTextContent(
    "34 movimientos en el fichero · 21 ya están · 2 se añadirán a la hoja de Sabadell Streamtec");
  expect(screen.getByText("ABONO TRANSFERENCIA DE FERRETERIA VIDAL")).toBeInTheDocument();
  expect(screen.getByText("18/10/2026")).toBeInTheDocument();
  expect(anadirExportBankDrive).toHaveBeenCalledWith(expect.any(File), "a1", false);
});

test("al confirmar se añade con el mismo fichero y avisa a la página", async () => {
  (anadirExportBankDrive as jest.Mock)
    .mockResolvedValueOnce(PREVIA)
    .mockResolvedValueOnce({ ...PREVIA, aplicado: true });
  const onAnadido = pinta();
  const f = fichero();
  await userEvent.upload(screen.getByLabelText("Export del banco para la hoja"), f);
  await userEvent.click(await screen.findByRole("button", {
    name: "Añadir 2 movimientos a la hoja" }));
  await waitFor(() => expect(onAnadido).toHaveBeenCalledWith({ ...PREVIA, aplicado: true }));
  expect(anadirExportBankDrive).toHaveBeenLastCalledWith(f, "a1", true);
  expect(screen.queryByLabelText("Vista previa del export")).not.toBeInTheDocument();
});

test("sin nada nuevo no ofrece escribir", async () => {
  (anadirExportBankDrive as jest.Mock).mockResolvedValue(
    { total: 6, ya: 6, nuevos: 0, aplicado: false, movimientos: [] });
  pinta();
  await userEvent.upload(screen.getByLabelText("Export del banco para la hoja"), fichero());
  expect(await screen.findByText(/no hay nada que añadir/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Añadir/ })).not.toBeInTheDocument();
});

test("un error (p. ej. el export es de otra cuenta) se dice", async () => {
  (anadirExportBankDrive as jest.Mock).mockRejectedValue(
    new Error("El fichero es de la cuenta 0049…, no de Sabadell Streamtec."));
  pinta();
  await userEvent.upload(screen.getByLabelText("Export del banco para la hoja"), fichero());
  expect(await screen.findByRole("alert")).toHaveTextContent("no de Sabadell Streamtec");
});
