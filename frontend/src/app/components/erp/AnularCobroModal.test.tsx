import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AnularCobroModal } from "./AnularCobroModal";
import {
  annulOrderCobro,
  correctOrderCobro,
  getOrderFactusolCobro,
  waitForAnnulCobroJob,
} from "../../lib/erpApi";

jest.mock("../../lib/erpApi", () => ({
  getOrderFactusolCobro: jest.fn(),
  getContrapartidas: jest.fn(() => Promise.resolve([
    { codigo: "2", nombre: "MQ Europe Belfius" },
    { codigo: "8", nombre: "Streamtec Sabadell" },
  ])),
  annulOrderCobro: jest.fn(),
  correctOrderCobro: jest.fn(),
  waitForAnnulCobroJob: jest.fn(),
}));

const COBRO = {
  id: "ev-1", numero: "5-260108", serie: 5, codigo: 260108, linlco: 1,
  fecha: "2026-09-23", importe: 333.96, contrapartida: "8",
  contrapartida_nombre: "Streamtec Sabadell", registrado_at: "2026-09-23T10:00:00",
  registrado_por: "pedidos@example.com", anulado: false, anulado_at: null, anulable: true,
};
const COBRADA = {
  order_id: "o-940", order_number: "BOPRIN-99940", status: "cobrada",
  invoice: { serie: 5, codigo: 260108, numero: "5-260108" },
  total: 333.96, total_cobrado: 333.96, saldo_pendiente: 0, estfac: "2", cobros: 1,
  bohub_cobros: [COBRO],
};
const PENDIENTE = {
  ...COBRADA, status: "pendiente", total_cobrado: 0, saldo_pendiente: 333.96, estfac: "0",
  cobros: 0, bohub_cobros: [{ ...COBRO, anulado: true, anulable: false,
                               anulado_at: "2026-10-01T09:00:00" }],
};

beforeEach(() => {
  [getOrderFactusolCobro, annulOrderCobro, correctOrderCobro, waitForAnnulCobroJob]
    .forEach((m) => (m as jest.Mock).mockReset());
});

describe("«Anular / corregir cobro» (cobros registrados por BoHub)", () => {
  it("anular: enseña el cobro, avisa de la escritura en FACTUSOL, pide confirmación y deja la factura pendiente", async () => {
    (getOrderFactusolCobro as jest.Mock)
      .mockResolvedValueOnce(COBRADA).mockResolvedValueOnce(PENDIENTE);
    (annulOrderCobro as jest.Mock).mockResolvedValue({ status: "queued", job_id: "job-a", cobro: COBRO });
    (waitForAnnulCobroJob as jest.Mock).mockResolvedValue({
      status: "finished", result: { annulled: true, status: "annulled", saldo_pendiente: 333.96 },
    });
    const onDone = jest.fn();
    const user = userEvent.setup();
    render(<AnularCobroModal orderId="o-940" orderNumber="BOPRIN-99940"
                             onClose={() => undefined} onDone={onDone} />);
    const lista = await screen.findByRole("list", { name: "Cobros registrados por BoHub" });
    expect(lista).toHaveTextContent("333,96 € del 23/09/2026 · contrapartida 8 · Streamtec Sabadell");
    await user.click(within(lista).getByRole("button", { name: "Anular cobro línea 1" }));
    expect(screen.getByRole("note")).toHaveTextContent("Se BORRARÁ en FACTUSOL la línea de cobro");
    const btn = screen.getByRole("button", { name: "Anular cobro" });
    expect(btn).toBeDisabled();
    await user.click(screen.getByLabelText("Confirmo la anulación"));
    await user.click(btn);
    await waitFor(() => expect(annulOrderCobro).toHaveBeenCalledWith("o-940", "ev-1"));
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Cobro de 333,96 € del 23/09/2026 · contrapartida 8 · Streamtec Sabadell anulado en FACTUSOL. "
      + "La factura queda con 333,96 € pendientes.",
    );
    expect(onDone).toHaveBeenCalledWith(expect.objectContaining({ status: "pendiente" }));
  });

  it("si la línea se editó en FACTUSOL, no se anula y se explica", async () => {
    (getOrderFactusolCobro as jest.Mock).mockResolvedValue(COBRADA);
    (annulOrderCobro as jest.Mock).mockResolvedValue({ status: "queued", job_id: "job-b", cobro: COBRO });
    (waitForAnnulCobroJob as jest.Mock).mockResolvedValue({
      status: "finished", result: { annulled: false, status: "mismatch",
        motivo: "La línea 1 de cobro de 5-260108 no coincide con lo que registró BoHub: "
          + "importe 330.00 € (registrado 333.96 €). No se ha borrado nada." },
    });
    const user = userEvent.setup();
    render(<AnularCobroModal orderId="o-940" orderNumber="BOPRIN-99940" onClose={() => undefined} />);
    await user.click(await screen.findByRole("button", { name: "Anular cobro línea 1" }));
    await user.click(screen.getByLabelText("Confirmo la anulación"));
    await user.click(screen.getByRole("button", { name: "Anular cobro" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("No se ha borrado nada");
  });

  it("corregir: anula y registra con la fecha, cuenta e importe nuevos", async () => {
    (getOrderFactusolCobro as jest.Mock).mockResolvedValue(COBRADA);
    (correctOrderCobro as jest.Mock).mockResolvedValue({ status: "queued", job_id: "job-c", cobro: COBRO });
    (waitForAnnulCobroJob as jest.Mock).mockResolvedValue({
      status: "finished", result: { annulled: true, status: "annulled",
        correccion: { registered: true, status: "registered", importe: 325.49 } },
    });
    const user = userEvent.setup();
    render(<AnularCobroModal orderId="o-940" orderNumber="BOPRIN-99940" onClose={() => undefined} />);
    await user.click(await screen.findByRole("button", { name: "Corregir cobro línea 1" }));
    const fecha = screen.getByLabelText("Nueva fecha del cobro");
    expect(fecha).toHaveValue("2026-09-23");
    await user.clear(fecha);
    await user.type(fecha, "2026-09-30");
    await user.selectOptions(screen.getByLabelText("Nueva cuenta del cobro"), "2");
    const importe = screen.getByLabelText("Nuevo importe del cobro");
    await user.clear(importe);
    await user.type(importe, "325.49");
    await user.click(screen.getByLabelText("Confirmo la anulación"));
    await user.click(screen.getByRole("button", { name: "Corregir cobro" }));
    await waitFor(() => expect(correctOrderCobro).toHaveBeenCalledWith("o-940", "ev-1", {
      cuenta: "2", fecha: "2026-09-30", importe: 325.49,
    }));
    expect(await screen.findByRole("status")).toHaveTextContent("Cobro corregido");
  });

  it("sin cobros de BoHub (hechos a mano en FACTUSOL): no hay nada que anular", async () => {
    (getOrderFactusolCobro as jest.Mock).mockResolvedValue({ ...COBRADA, bohub_cobros: [] });
    render(<AnularCobroModal orderId="o-940" orderNumber="BOPRIN-99940" onClose={() => undefined} />);
    expect(await screen.findByRole("status")).toHaveTextContent(
      "No hay cobros registrados por BoHub en esta factura",
    );
    expect(screen.queryByRole("button", { name: /Anular cobro/ })).toBeNull();
  });
});
