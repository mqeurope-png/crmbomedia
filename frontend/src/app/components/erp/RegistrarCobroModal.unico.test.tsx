import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RegistrarCobroModal } from "./RegistrarCobroModal";
import { ApiError } from "../../lib/api";
import {
  cobrarFacturaConMovimiento,
  confirmarCobroBanco,
  getFactusolFacturaCobro,
  getOrderFactusolCobro,
  registerInvoiceCollection,
  waitForInvoiceCollectionJob,
  type OrderCobroInfo,
} from "../../lib/erpApi";

/** LA herramienta de cobro (29/10/2026): el mismo diálogo desde la ficha del
 *  pedido, Conciliación y ERP · Documentos. Con el movimiento del banco, su
 *  fecha valor y la hoja de Drive; externa; cobrada; parcial. Figurantes. */

jest.mock("../../lib/erpApi", () => ({
  getOrderFactusolCobro: jest.fn(),
  getFactusolFacturaCobro: jest.fn(),
  getContrapartidas: jest.fn(() => Promise.resolve([
    { codigo: "6", nombre: "Banco Figurante" }, { codigo: "8", nombre: "Otra cuenta" },
  ])),
  getFactusolFormasPago: jest.fn(() => Promise.resolve([{ codigo: "002", nombre: "Transferencia" }])),
  registerInvoiceCollection: jest.fn(),
  waitForInvoiceCollectionJob: jest.fn(),
  confirmarCobroBanco: jest.fn(),
  cobrarFacturaConMovimiento: jest.fn(),
}));

const MOV = {
  id: "m-1", fecha_oper: "2026-08-25", fecha_valor: "2026-08-26", importe: 927,
  concepto: "TRANSFERENCIA FIGURANTE FACTURA 2-526081", payer_name: null, status: "reconciled",
  cuenta: { id: "acc-1", nombre: "Banco Figurante MQ", contrapartida_codigo: "6",
            contrapartida_nombre: "Banco Figurante" },
  importe_factura: 927, conciliado: true, ya_en_factusol: false,
} as const;

const INFO: OrderCobroInfo = {
  status: "pendiente", invoice: { serie: 2, codigo: 526081, numero: "2-526081" },
  cliente: "TALLER EJEMPLO SL", referencia: "BOP-526081", total: 927, total_cobrado: 0,
  saldo_pendiente: 927, estfac: "0", cobros: 0, forma_pago_nombre: "Transferencia",
  suggested_cuenta: { codigo: "8", nombre: "Otra cuenta" }, warnings: [],
  externa: false, movimiento: MOV,
  hoja: { cuenta: "Banco Figurante MQ", pestana: "Movimientos", factura: "2-526081" },
  cobrado_con: [],
};

const RESULTADO = {
  status: "queued", job_id: "j-1", numero: "2-526081", cliente: "TALLER EJEMPLO SL",
  referencia: "BOP-526081", total: 927, saldo_pendiente: 927, importe: 927,
  contrapartida: { codigo: "6", nombre: "Banco Figurante" }, fecha: "2026-08-26",
  forma: "Transferencia", parcial: false, movimiento: MOV,
  hoja: { cuenta: "Banco Figurante MQ", pestana: "Movimientos", estado: "escrita", factura: "2-526081" },
};

beforeEach(() => {
  jest.clearAllMocks();
  (waitForInvoiceCollectionJob as jest.Mock).mockResolvedValue({
    status: "finished", result: { registered: true, status: "registered", importe: 927 },
  });
});

async function abrirDesdeDocumentos(info: OrderCobroInfo = INFO) {
  (getFactusolFacturaCobro as jest.Mock).mockResolvedValue(info);
  const utils = render(<RegistrarCobroModal factura={{ serie: 2, codigo: 526081, numero: "2-526081" }}
    onClose={() => undefined} />);
  await screen.findByText("Factura 2-526081");
  return utils;
}

it("con el movimiento del banco: su fecha valor, la cuenta de su banco y que también escribe en la hoja", async () => {
  await abrirDesdeDocumentos();
  await waitFor(() => expect(screen.getByLabelText("Fecha del cobro")).toHaveValue("2026-08-26"));
  expect(screen.getByLabelText("Cuenta del cobro")).toHaveValue("6");
  expect(screen.getByLabelText("Fecha del cobro")).toBeDisabled();
  expect(screen.getByLabelText("Importe del cobro")).toHaveValue(927);
  expect(screen.getByLabelText("Movimiento del banco")).toHaveTextContent(
    "Con el movimiento del banco del 26/08/2026 (fecha valor) · 927.00 € · Banco Figurante MQ");
  expect(screen.getByText(/También se escribirá/)).toHaveTextContent(
    "También se escribirá 2-526081 en la columna «factura» de la fila de este movimiento en la hoja de Drive de Banco Figurante MQ (pestaña Movimientos), solo si esa celda está vacía. Si la hoja falla, el cobro en FACTUSOL no se deshace.");
  // Lo de siempre: la frase exacta, cobrada (ESTFAC=2) y la casilla aparte.
  expect(screen.getByText(/1 línea de cobro en F_LCO/).closest("p")).toHaveTextContent(
    "con fecha 2026-08-26 en la cuenta Banco Figurante (Transferencia), y la factura pasará a cobrada (ESTFAC=2).");
  expect(screen.getByText(/Confirmo que quiero registrar este cobro en FACTUSOL \(escritura contable\)/))
    .toBeInTheDocument();
});

it("al registrar, concilia y cobra con el movimiento y dice qué pasó en la hoja", async () => {
  (cobrarFacturaConMovimiento as jest.Mock).mockResolvedValue(RESULTADO);
  const user = userEvent.setup();
  await abrirDesdeDocumentos();
  await user.click(screen.getByLabelText("Confirmo el cobro"));
  await user.click(screen.getByRole("button", { name: "Registrar cobro" }));
  await waitFor(() => expect(cobrarFacturaConMovimiento).toHaveBeenCalledWith(2, 526081, "m-1", { forzar: false }));
  expect(registerInvoiceCollection).not.toHaveBeenCalled();
  expect(await screen.findByLabelText("La hoja de Drive")).toHaveTextContent(
    "Escrita la 2-526081 en la columna «factura» de la hoja de Drive de Banco Figurante MQ (pestaña Movimientos).");
  expect(screen.getByRole("status", { name: "" })).toBeTruthy();
});

it("desde la ficha y desde Documentos enseña lo mismo y escribe lo mismo", async () => {
  (getOrderFactusolCobro as jest.Mock).mockResolvedValue(INFO);
  (confirmarCobroBanco as jest.Mock).mockResolvedValue(RESULTADO);
  (cobrarFacturaConMovimiento as jest.Mock).mockResolvedValue(RESULTADO);
  const user = userEvent.setup();
  const { unmount } = await abrirDesdeDocumentos();
  await waitFor(() => expect(screen.getByLabelText("Fecha del cobro")).toHaveValue("2026-08-26"));
  const desdeDocumentos = screen.getByRole("dialog").querySelector(".modal-body")?.textContent;
  unmount();
  render(<RegistrarCobroModal orderId="o-1" movimientoId="m-1" onClose={() => undefined} />);
  await screen.findByText("Factura 2-526081");
  await waitFor(() => expect(screen.getByLabelText("Fecha del cobro")).toHaveValue("2026-08-26"));
  expect(getOrderFactusolCobro).toHaveBeenCalledWith("o-1", "m-1");
  expect(screen.getByRole("dialog").querySelector(".modal-body")?.textContent).toBe(desdeDocumentos);
  await user.click(screen.getByLabelText("Confirmo el cobro"));
  await user.click(screen.getByRole("button", { name: "Registrar cobro" }));
  await waitFor(() => expect(confirmarCobroBanco).toHaveBeenCalledWith("o-1", "m-1", { forzar: false }));
});

it("si la hoja no se pudo escribir, el cobro está registrado y se avisa", async () => {
  (cobrarFacturaConMovimiento as jest.Mock).mockResolvedValue({
    ...RESULTADO, hoja: { cuenta: "Banco Figurante MQ", pestana: "Movimientos", estado: "error",
                          detalle: "Drive no responde ahora (HTTP 503)." },
  });
  const user = userEvent.setup();
  await abrirDesdeDocumentos();
  await user.click(screen.getByLabelText("Confirmo el cobro"));
  await user.click(screen.getByRole("button", { name: "Registrar cobro" }));
  expect(await screen.findByLabelText("La hoja de Drive")).toHaveTextContent(
    "El cobro está registrado, pero la hoja de Drive de Banco Figurante MQ (pestaña Movimientos) no se ha podido escribir: Drive no responde ahora (HTTP 503).");
  expect(await screen.findByText(/Cobro de 927.00 € registrado en FACTUSOL con fecha 26\/08\/2026/))
    .toBeInTheDocument();
});

it("por menos de lo pendiente sigue diciendo «cobro parcial»", async () => {
  await abrirDesdeDocumentos({ ...INFO, movimiento: { ...MOV, importe_factura: 500, importe: 500 } });
  await waitFor(() => expect(screen.getByLabelText("Importe del cobro")).toHaveValue(500));
  expect(screen.getByText(/Cobro parcial: la factura quedará con 427.00 € pendientes/)).toBeInTheDocument();
  expect(screen.getByText(/1 línea de cobro en F_LCO/).closest("p")).toHaveTextContent("cobro parcial (ESTFAC=1)");
});

it("una factura externa lo dice en una línea", async () => {
  await abrirDesdeDocumentos({ ...INFO, externa: true, movimiento: null, hoja: null });
  expect(screen.getByText(/Factura externa/)).toHaveTextContent(
    "Factura externa: no hay pedido de BoHub detrás. La trazabilidad se queda en FACTUSOL.");
  expect(screen.queryByText(/También se escribirá/)).not.toBeInTheDocument();
});

it("ya cobrada: no ofrece registrar y enseña contra qué se cobró", async () => {
  await abrirDesdeDocumentos({
    ...INFO, status: "cobrada", total_cobrado: 927, saldo_pendiente: 0, estfac: "2", cobros: 1,
    movimiento: null, hoja: null,
    cobrado_con: [{ fecha: "2026-08-26", importe: 927, cuenta: "Banco Figurante", movimiento: MOV }],
  });
  expect(screen.queryByRole("button", { name: "Registrar cobro" })).not.toBeInTheDocument();
  const lista = screen.getByRole("list", { name: "Contra qué se cobró" });
  expect(within(lista).getByRole("listitem")).toHaveTextContent(
    "26/08/2026 · 927.00 € · cuenta Banco Figurante · movimiento del banco del 26/08/2026 (Banco Figurante MQ)");
});

it("un cobro igual suelto en FACTUSOL: no registra sin decir que es otro pago", async () => {
  (cobrarFacturaConMovimiento as jest.Mock)
    .mockRejectedValueOnce(new ApiError("dup", 409, {
      code: "posible_doble_cobro", detail: "La 2-526081 ya tiene en FACTUSOL un cobro de 927.00 € del 20/08/2026.",
    }))
    .mockResolvedValueOnce(RESULTADO);
  const user = userEvent.setup();
  await abrirDesdeDocumentos();
  await user.click(screen.getByLabelText("Confirmo el cobro"));
  await user.click(screen.getByRole("button", { name: "Registrar cobro" }));
  const otro = await screen.findByLabelText("Es otro pago: registrarlo igualmente");
  expect(screen.getByRole("button", { name: "Registrar cobro" })).toBeDisabled();
  // Ya no se ofrece saltarse la comprobación registrándolo «sin el movimiento».
  expect(screen.queryByRole("button", { name: "Registrar sin este movimiento" })).not.toBeInTheDocument();
  await user.click(otro);
  await user.click(screen.getByRole("button", { name: "Registrar cobro" }));
  await waitFor(() => expect(cobrarFacturaConMovimiento).toHaveBeenLastCalledWith(
    2, 526081, "m-1", { forzar: true }));
});

it("«Registrar sin este movimiento» vuelve al cobro de siempre, sin hoja", async () => {
  (registerInvoiceCollection as jest.Mock).mockResolvedValue({ ...RESULTADO, hoja: undefined });
  const user = userEvent.setup();
  await abrirDesdeDocumentos();
  await user.click(screen.getByRole("button", { name: "Registrar sin este movimiento" }));
  expect(screen.queryByText(/También se escribirá/)).not.toBeInTheDocument();
  expect(screen.getByLabelText("Fecha del cobro")).toBeEnabled();
  await user.click(screen.getByLabelText("Confirmo el cobro"));
  await user.click(screen.getByRole("button", { name: "Registrar cobro" }));
  await waitFor(() => expect(registerInvoiceCollection).toHaveBeenCalled());
  expect(cobrarFacturaConMovimiento).not.toHaveBeenCalled();
});

it("si su cobro ya está en FACTUSOL solo concilia: no promete otra línea ni deja registrarlo aparte", async () => {
  await abrirDesdeDocumentos({ ...INFO, movimiento: { ...MOV, ya_en_factusol: true } });
  expect(screen.getByText(/Este cobro ya consta en FACTUSOL/)).toBeInTheDocument();
  expect(screen.queryByText(/1 línea de cobro en F_LCO/)).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Conciliar" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Registrar sin este movimiento" })).not.toBeInTheDocument();
});

it("si FACTUSOL ya la tenía cobrada al llegar el cobro, no dice que lo ha registrado", async () => {
  (cobrarFacturaConMovimiento as jest.Mock).mockResolvedValue({
    ...RESULTADO, hoja: { cuenta: "Banco Figurante MQ", pestana: null, estado: "no_se_escribe" },
  });
  (waitForInvoiceCollectionJob as jest.Mock).mockResolvedValue({
    status: "finished", result: { registered: false, status: "already" },
  });
  const user = userEvent.setup();
  await abrirDesdeDocumentos();
  await user.click(screen.getByLabelText("Confirmo el cobro"));
  await user.click(screen.getByRole("button", { name: "Registrar cobro" }));
  expect(await screen.findByText(/ya constaba cobrada en FACTUSOL: no se ha registrado nada/))
    .toBeInTheDocument();
  expect(screen.getByLabelText("La hoja de Drive")).toHaveTextContent("es un .xlsx subido");
  expect(screen.getByLabelText("La hoja de Drive").className).not.toContain("form-error");
});
