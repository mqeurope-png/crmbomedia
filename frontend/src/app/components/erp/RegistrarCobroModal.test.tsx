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
    { codigo: "2", nombre: "MQ Europe Belfius" },
    { codigo: "6", nombre: "Bomedia Sabadell" },
    { codigo: "8", nombre: "Streamtec Sabadell" },
    { codigo: "14", nombre: "Paypal Streamtec" },
    { codigo: "15", nombre: "Tarjetas Mollie Belfius" },
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

  it("pedido artisJet pagado con «Carte» (Mollie): 15 sugerida con su porqué; editable y se registra con la elegida", async () => {
    (getOrderFactusolCobro as jest.Mock).mockResolvedValue({
      ...PENDIENTE, order_number: "ARTISJ-9530",
      invoice: { serie: 2, codigo: 526200, numero: "2-526200" },
      suggested_cuenta: { codigo: "15", nombre: "Tarjetas Mollie Belfius" },
      suggested_reason: "tienda artisJet · método Carte",
      payment_method_title: "Carte",
    });
    (registerInvoiceCollection as jest.Mock).mockResolvedValue({
      status: "queued", job_id: "job-2", numero: "2-526200", importe: 121,
      contrapartida: { codigo: "2", nombre: "MQ Europe Belfius" }, fecha: "2026-09-12",
    });
    (waitForInvoiceCollectionJob as jest.Mock).mockResolvedValue({
      status: "finished", result: { registered: true, status: "registered", importe: 121 },
    });
    const user = userEvent.setup();
    render(<RegistrarCobroModal orderId="o-9" orderNumber="ARTISJ-9530" onClose={() => undefined} />);
    const cuenta = await screen.findByLabelText("Cuenta del cobro");
    await waitFor(() => expect(cuenta).toHaveValue("15"));
    expect(screen.getByRole("note")).toHaveTextContent("Sugerida por: tienda artisJet · método Carte");
    // Se puede cambiar: la nota sigue diciendo cuál era la sugerida.
    await user.selectOptions(cuenta, "2");
    expect(screen.getByRole("note"))
      .toHaveTextContent("Sugerida: 15 · Tarjetas Mollie Belfius por: tienda artisJet · método Carte");
    await user.click(screen.getByLabelText("Confirmo el cobro"));
    await user.click(screen.getByRole("button", { name: "Registrar cobro" }));
    await waitFor(() => expect(registerInvoiceCollection).toHaveBeenCalledWith(
      2, 526200, expect.objectContaining({ cuenta: "2" }),
    ));
  });

  it("importe editable: cobro PARCIAL (325,49 de 333,96), tope al pendiente y aviso de descuadre", async () => {
    (getOrderFactusolCobro as jest.Mock).mockResolvedValue({
      ...PENDIENTE, order_number: "BOPRIN-99940",
      invoice: { serie: 5, codigo: 260108, numero: "5-260108" },
      total: 333.96, saldo_pendiente: 333.96,
      total_mismatch: { pedido: 325.49, factura: 333.96, diferencia: 8.47 },
    });
    (registerInvoiceCollection as jest.Mock).mockResolvedValue({
      status: "queued", job_id: "job-p", numero: "5-260108", importe: 325.49,
      contrapartida: { codigo: "8", nombre: "Streamtec Sabadell" }, fecha: "2026-09-30",
      parcial: true,
    });
    (waitForInvoiceCollectionJob as jest.Mock).mockResolvedValue({
      status: "finished", result: { registered: true, status: "registered", importe: 325.49,
                                    parcial: true, saldo_pendiente: 8.47 },
    });
    const user = userEvent.setup();
    render(<RegistrarCobroModal orderId="o-940" orderNumber="BOPRIN-99940" onClose={() => undefined} />);
    // Aviso de descuadre pedido / factura y el importe propuesto = lo pendiente.
    expect(await screen.findByText(/Pedido 325.49 € · Factura/)).toHaveTextContent("diferencia 8.47 €");
    const importe = screen.getByLabelText("Importe del cobro");
    expect(importe).toHaveValue(333.96);
    await user.click(screen.getByLabelText("Confirmo el cobro"));
    // Más que lo pendiente → no se deja.
    await user.clear(importe);
    await user.type(importe, "400");
    expect(screen.getByText(/No puede ser más que lo pendiente/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Registrar cobro" })).toBeDisabled();
    // Parcial: se avisa del resto y se manda el importe.
    await user.clear(importe);
    await user.type(importe, "325.49");
    expect(screen.getByText(/Cobro parcial: la factura quedará con 8.47 € pendientes/)).toBeInTheDocument();
    expect(screen.getByText(/cobro parcial \(ESTFAC=1\)/)).toBeInTheDocument();
    (getOrderFactusolCobro as jest.Mock).mockResolvedValue({
      ...PENDIENTE, invoice: { serie: 5, codigo: 260108, numero: "5-260108" },
      total: 333.96, total_cobrado: 325.49, saldo_pendiente: 8.47, estfac: "1",
    });
    await user.click(screen.getByRole("button", { name: "Registrar cobro" }));
    await waitFor(() => expect(registerInvoiceCollection).toHaveBeenCalledWith(
      5, 260108, expect.objectContaining({ importe: 325.49 }),
    ));
    expect(await screen.findByText(/Cobro parcial de 325.49 € registrado/)).toHaveTextContent(
      "Quedan 8.47 € pendientes",
    );
  });
});
