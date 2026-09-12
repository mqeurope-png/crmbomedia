import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RegistrarCobroModal } from "./RegistrarCobroModal";
import {
  getOrderFactusolCobro,
  registerInvoiceCollection,
  waitForInvoiceCollectionJob,
} from "../../lib/erpApi";

jest.mock("../../lib/erpApi", () => ({
  getOrderFactusolCobro: jest.fn(),
  getContrapartidas: jest.fn(() => Promise.resolve([
    { codigo: "6", nombre: "Bomedia Sabadell" },
    { codigo: "8", nombre: "Streamtec Sabadell" },
    { codigo: "14", nombre: "Paypal Streamtec" },
  ])),
  getFactusolFormasPago: jest.fn(() => Promise.resolve([
    { codigo: "002", nombre: "Transferencia" }, { codigo: "005", nombre: "Paypal" },
  ])),
  registerInvoiceCollection: jest.fn(),
  waitForInvoiceCollectionJob: jest.fn(),
}));

const PENDIENTE = {
  order_id: "o-1", order_number: "FLUXLA-5789", status: "pendiente",
  invoice: { serie: 5, codigo: 260086, numero: "5-260086" },
  cliente: "PERSOREGALA SL", referencia: "FLE-005789",
  total: 121, total_cobrado: 0, saldo_pendiente: 121, estfac: "0", cobros: 0,
  fopfac: "002", forma_pago_nombre: "Transferencia",
  suggested_cuenta: { codigo: "8", nombre: "Streamtec Sabadell" },
  warnings: [], checked_at: "2026-09-12T10:00:00+00:00", persisted_status: "pendiente",
};
const COBRADA = {
  ...PENDIENTE, status: "cobrada", total_cobrado: 121, saldo_pendiente: 0, estfac: "2",
  cobros: 1, persisted_status: "cobrada",
};

beforeEach(() => {
  (getOrderFactusolCobro as jest.Mock).mockReset();
  (registerInvoiceCollection as jest.Mock).mockReset();
  (waitForInvoiceCollectionJob as jest.Mock).mockReset();
});

describe("ERP · modal «Registrar cobro en FACTUSOL» (compartido ficha / bandeja)", () => {
  it("factura pendiente: precarga factura, importe, cuenta sugerida y forma; exige confirmación; registra con el endpoint F-4-B y re-comprueba", async () => {
    (getOrderFactusolCobro as jest.Mock)
      .mockResolvedValueOnce(PENDIENTE)
      .mockResolvedValueOnce(COBRADA);
    (registerInvoiceCollection as jest.Mock).mockResolvedValue({
      status: "queued", job_id: "job-1", numero: "5-260086", cliente: "PERSOREGALA SL",
      referencia: "FLE-005789", total: 121, saldo_pendiente: 121, importe: 121,
      contrapartida: { codigo: "8", nombre: "Streamtec Sabadell" }, fecha: "2026-09-12",
      forma: "Transferencia",
    });
    (waitForInvoiceCollectionJob as jest.Mock).mockResolvedValue({
      status: "finished",
      result: { registered: true, status: "registered", importe: 121, linlco: 1, estfac_marked: true },
    });
    const onDone = jest.fn();
    const user = userEvent.setup();
    render(<RegistrarCobroModal orderId="o-1" orderNumber="FLUXLA-5789" onClose={() => undefined} onDone={onDone} />);
    expect(await screen.findByText("Factura 5-260086")).toBeInTheDocument();
    expect(screen.getByText(/saldo pendiente/)).toHaveTextContent("121.00 €");
    // Cuenta sugerida por la serie 5 (Streamtec) y forma de pago de la factura.
    await waitFor(() => expect(screen.getByLabelText("Cuenta del cobro")).toHaveValue("8"));
    expect(screen.getByLabelText("Forma de pago")).toHaveValue("Transferencia");
    expect(screen.getByLabelText("Fecha del cobro")).toHaveValue(new Date().toISOString().slice(0, 10));
    // Resumen de lo que se va a escribir + confirmación explícita.
    expect(screen.getByText(/1 línea de cobro en F_LCO/)).toBeInTheDocument();
    const btn = screen.getByRole("button", { name: "Registrar cobro" });
    expect(btn).toBeDisabled();
    await user.click(screen.getByLabelText("Confirmo el cobro"));
    expect(btn).toBeEnabled();
    await user.click(btn);
    await waitFor(() => expect(registerInvoiceCollection).toHaveBeenCalledWith(5, 260086, {
      confirm: true, cuenta: "8", fecha: new Date().toISOString().slice(0, 10),
      forma: "Transferencia", observaciones: null,
    }));
    expect(waitForInvoiceCollectionJob).toHaveBeenCalledWith("job-1");
    expect(await screen.findByRole("status")).toHaveTextContent(/registrado en FACTUSOL.*consta cobrada/);
    expect(getOrderFactusolCobro).toHaveBeenCalledTimes(2);   // re-chequeo saldo ≈ 0
    expect(onDone).toHaveBeenCalledWith(expect.objectContaining({ status: "cobrada" }));
    expect(screen.queryByRole("button", { name: "Registrar cobro" })).toBeNull();
    // El pie pasa de «Cancelar» a «Cerrar» (además del aspa de la cabecera).
    expect(screen.queryByRole("button", { name: "Cancelar" })).toBeNull();
    expect(screen.getAllByRole("button", { name: "Cerrar" }).length).toBe(2);
  });

  it("ya cobrada: estado «Cobrado», sin botón de registrar (idempotente, no doble cobro)", async () => {
    (getOrderFactusolCobro as jest.Mock).mockResolvedValue(COBRADA);
    render(<RegistrarCobroModal orderId="o-1" orderNumber="FLUXLA-5789" onClose={() => undefined} />);
    expect(await screen.findByText("Cobrado FACTUSOL")).toBeInTheDocument();
    expect(screen.getByText(/no se registra un segundo cobro/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Registrar cobro" })).toBeNull();
    expect(registerInvoiceCollection).not.toHaveBeenCalled();
  });

  it("sin factura: aviso «emite la factura primero», sin error rojo", async () => {
    (getOrderFactusolCobro as jest.Mock).mockResolvedValue({
      order_id: "o-2", order_number: "MANUAL-000001", status: "sin_factura", invoice: null,
      detail: "El pedido aún no tiene factura en FACTUSOL: emite la factura primero.",
    });
    render(<RegistrarCobroModal orderId="o-2" orderNumber="MANUAL-000001" onClose={() => undefined} />);
    expect(await screen.findByRole("status")).toHaveTextContent("emite la factura primero");
    expect(document.querySelector(".form-error")).toBeNull();
    expect(screen.queryByRole("button", { name: "Registrar cobro" })).toBeNull();
  });

  it("línea de cobro previa (anticipo / posible doble cobro): avisa pero deja registrar el saldo", async () => {
    (getOrderFactusolCobro as jest.Mock).mockResolvedValue({
      ...PENDIENTE, total_cobrado: 40, saldo_pendiente: 81, cobros: 1, estfac: "1",
      warnings: [
        "La factura ya tiene 1 línea(s) de cobro por 40.00 € (saldo 81.00 €): posible doble cobro o anticipo. Revisa antes de registrar.",
        "FACTUSOL la marca como cobro parcial (ESTFAC=1).",
      ],
    });
    (registerInvoiceCollection as jest.Mock).mockResolvedValue({ status: "already", numero: "5-260086" });
    const user = userEvent.setup();
    render(<RegistrarCobroModal orderId="o-1" orderNumber="FLUXLA-5789" onClose={() => undefined} />);
    const alerts = await screen.findAllByRole("alert");
    expect(alerts[0]).toHaveTextContent("posible doble cobro");
    expect(screen.getByText(/saldo pendiente/)).toHaveTextContent("81.00 €");
    await user.click(screen.getByLabelText("Confirmo el cobro"));
    const btn = screen.getByRole("button", { name: "Registrar cobro" });
    expect(btn).toBeEnabled();        // no se bloquea: Bart decide
    await user.click(btn);
    // `already` desde el endpoint → aviso, nada encolado, sin polling.
    expect(await screen.findByRole("status")).toHaveTextContent("ya constaba cobrada");
    expect(waitForInvoiceCollectionJob).not.toHaveBeenCalled();
  });

  it("fallo del job → error dentro del modal (sin estado de éxito)", async () => {
    (getOrderFactusolCobro as jest.Mock).mockResolvedValue(PENDIENTE);
    (registerInvoiceCollection as jest.Mock).mockResolvedValue({
      status: "queued", job_id: "job-9", numero: "5-260086", importe: 121,
      contrapartida: { codigo: "8", nombre: "Streamtec Sabadell" },
    });
    (waitForInvoiceCollectionJob as jest.Mock).mockResolvedValue({
      status: "finished",
      result: { registered: false, status: "write_failed", motivo: "DELSOL rechazó F_LCO" },
    });
    const user = userEvent.setup();
    render(<RegistrarCobroModal orderId="o-1" orderNumber="FLUXLA-5789" onClose={() => undefined} />);
    await screen.findByText("Factura 5-260086");
    await user.click(screen.getByLabelText("Confirmo el cobro"));
    await user.click(screen.getByRole("button", { name: "Registrar cobro" }));
    await waitFor(() => expect(document.querySelector(".form-error")).toHaveTextContent(/DELSOL rechazó F_LCO/));
    expect(screen.queryByRole("status")).toBeNull();
  });
});
