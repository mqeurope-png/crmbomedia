import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CobroBancoBlock } from "./CobroBancoBlock";

/** El cobro, propuesto desde el banco (paso «Cobro» de la ficha). Datos
 *  inventados: ningún pagador, cuenta ni concepto real. */

jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ children, href, className }: {
    children: React.ReactNode; href: string; className?: string;
  }) => <a href={href} className={className}>{children}</a>,
}));

const getOrderCobroBanco = jest.fn();
const confirmarCobroBanco = jest.fn();
const waitForInvoiceCollectionJob = jest.fn();
jest.mock("../../lib/erpApi", () => ({
  getOrderCobroBanco: (...a: unknown[]) => getOrderCobroBanco(...a),
  confirmarCobroBanco: (...a: unknown[]) => confirmarCobroBanco(...a),
  waitForInvoiceCollectionJob: (...a: unknown[]) => waitForInvoiceCollectionJob(...a),
}));

const CUENTA = {
  id: "acc-1", nombre: "Banco Figurante MQ", contrapartida_codigo: "572001",
  contrapartida_nombre: "Banco Figurante",
};

function mov(over: Record<string, unknown> = {}) {
  return {
    id: "m-1", fecha_oper: "2026-08-25", fecha_valor: "2026-08-26", importe: 927,
    concepto: "TRANSFERENCIA DE TALLER EJEMPLO SL FACTURA 2-526081", payer_name: "Taller Ejemplo",
    status: "pending", cuenta: CUENTA, ...over,
  };
}

function candidatos(lista: unknown[], saldo = 927) {
  return {
    aplica: true, estado: "candidatos", cobrada: false, por_registrar: 0,
    factura: { serie: 2, codigo: 526081, numero: "2-526081", total: 927, saldo_pendiente: saldo },
    conciliados: [], candidatos: lista,
  };
}

beforeEach(() => {
  getOrderCobroBanco.mockReset();
  confirmarCobroBanco.mockReset();
  waitForInvoiceCollectionJob.mockReset();
});

describe("CobroBancoBlock — el cobro que propone el banco", () => {
  it("enseña el movimiento con su fecha valor, importe, cuenta y concepto, la confianza y el porqué", async () => {
    getOrderCobroBanco.mockResolvedValue(candidatos([{
      ...mov(), confianza: "alta", motivo: "número de factura en el concepto + importe exacto",
      parcial: false, excede: false,
    }]));
    render(<CobroBancoBlock orderId="o-1" canConfirm />);
    const bloque = within(await screen.findByLabelText("Cobro en el banco"));
    expect(bloque.getByText("Lo hemos visto en el banco:")).toBeInTheDocument();
    const linea = bloque.getByText("26/08/2026").closest("p") as HTMLElement;
    expect(linea).toHaveTextContent("26/08/2026 · 927,00 € · Banco Figurante MQ");
    expect(linea).toHaveTextContent("«TRANSFERENCIA DE TALLER EJEMPLO SL FACTURA 2-526081»");
    expect(bloque.getByText("Confianza alta")).toBeInTheDocument();
    expect(bloque.getByText(/número de factura en el concepto \+ importe exacto/)).toBeInTheDocument();
    expect(bloque.getByRole("button", { name: "Marcar cobrado con esta fecha" })).toBeInTheDocument();
    expect(bloque.getByRole("link", { name: "Ver el movimiento" }))
      .toHaveAttribute("href", "/erp/conciliacion?movimiento=m-1");
  });

  it("confirmar dice lo que va a pasar y registra el cobro con ese movimiento (fecha valor, cuenta, hoja)", async () => {
    getOrderCobroBanco.mockResolvedValue(candidatos([{
      ...mov(), confianza: "alta", motivo: "número de factura en el concepto", parcial: false,
      excede: false,
    }]));
    confirmarCobroBanco.mockResolvedValue({
      status: "queued", job_id: "j-1", numero: "2-526081", importe: 927, parcial: false,
      movimiento: mov(),
    });
    waitForInvoiceCollectionJob.mockResolvedValue({
      status: "finished", result: { registered: true, status: "registered", importe: 927 },
    });
    const onDone = jest.fn();
    const user = userEvent.setup();
    render(<CobroBancoBlock orderId="o-1" canConfirm onDone={onDone} />);
    await user.click(await screen.findByRole("button", { name: "Marcar cobrado con esta fecha" }));
    const panel = within(screen.getByRole("group", { name: "Confirmar el cobro" }));
    expect(panel.getByText(/se registra en FACTUSOL un cobro de/)).toHaveTextContent(
      "se registra en FACTUSOL un cobro de 927,00 € de la 2-526081 con fecha 26/08/2026 (la fecha valor del banco) en la cuenta 572001 · Banco Figurante",
    );
    expect(panel.getByText(/el movimiento queda conciliado con la 2-526081/)).toBeInTheDocument();
    expect(panel.getByText(/la factura se escribe en la hoja de Drive del banco/)).toBeInTheDocument();
    const registrar = panel.getByRole("button", { name: "Registrar el cobro" });
    expect(registrar).toBeDisabled();
    await user.click(panel.getByLabelText("Confirmo el cobro con este movimiento"));
    await user.click(registrar);
    await waitFor(() => expect(confirmarCobroBanco).toHaveBeenCalledWith("o-1", "m-1"));
    expect(waitForInvoiceCollectionJob).toHaveBeenCalledWith("j-1");
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Cobro de 927,00 € registrado con fecha 26/08/2026: la 2-526081 consta cobrada.",
    );
    expect(onDone).toHaveBeenCalled();
  });

  it("un importe distinto (menor) es un cobro PARCIAL: se dice y no se da por cobrada", async () => {
    getOrderCobroBanco.mockResolvedValue(candidatos([{
      ...mov({ importe: 500 }), confianza: "media", motivo: "número de factura en el concepto",
      parcial: true, excede: false,
    }]));
    const user = userEvent.setup();
    render(<CobroBancoBlock orderId="o-1" canConfirm />);
    expect(await screen.findByText(/sería un cobro parcial, la factura no quedaría cobrada/))
      .toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Marcar cobrado con esta fecha" }));
    expect(screen.getByText(/Es un cobro PARCIAL/)).toHaveTextContent(
      "Es un cobro PARCIAL: quedarán 427,00 € pendientes y la factura no se marca cobrada.",
    );
  });

  it("varios candidatos: se enseñan todos y no se preselecciona ninguno", async () => {
    getOrderCobroBanco.mockResolvedValue(candidatos([
      { ...mov(), confianza: "media", motivo: "importe exacto + nombre del cliente", parcial: false, excede: false },
      { ...mov({ id: "m-2", fecha_valor: "2026-08-28" }), confianza: "media",
        motivo: "importe exacto + nombre del cliente", parcial: false, excede: false },
    ]));
    render(<CobroBancoBlock orderId="o-1" canConfirm />);
    expect(await screen.findByText(/Hay 2 movimientos del banco que pueden ser este cobro/))
      .toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Es este: marcar cobrado con esta fecha" }))
      .toHaveLength(2);
    expect(screen.queryByRole("group", { name: "Confirmar el cobro" })).toBeNull();
    expect(screen.queryByRole("checkbox")).toBeNull();
  });

  it("sin movimiento no se dice nada", async () => {
    getOrderCobroBanco.mockResolvedValue({ ...candidatos([]), estado: "nada" });
    const { container } = render(<CobroBancoBlock orderId="o-1" canConfirm />);
    await waitFor(() => expect(getOrderCobroBanco).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it("ya cobrada: no propone nada; como mucho dice con qué movimiento se cobró", async () => {
    getOrderCobroBanco.mockResolvedValue({
      ...candidatos([]), estado: "cobrada", cobrada: true,
      conciliados: [{ ...mov({ status: "reconciled" }), importe_conciliado: 927, origen: null }],
    });
    render(<CobroBancoBlock orderId="o-1" canConfirm />);
    expect(await screen.findByText("Cobrado con este movimiento del banco:")).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByRole("link", { name: "Ver el movimiento" })).toBeInTheDocument();
  });

  it("ya conciliado en Conciliación y sin cobro en FACTUSOL: ofrece registrarlo con esa fecha (el caso de un cobro visto el 29/10)", async () => {
    getOrderCobroBanco.mockResolvedValue({
      ...candidatos([]), estado: "conciliado", por_registrar: 927,
      conciliados: [{ ...mov({ status: "reconciled" }), importe_conciliado: 927, origen: null,
                      registrado: null }],
    });
    render(<CobroBancoBlock orderId="o-1" canConfirm />);
    expect(await screen.findByText("Lo hemos visto en el banco (ya conciliado):")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Registrar el cobro con esta fecha" })).toBeInTheDocument();
  });

  it("conciliado cuyo cobro ya consta en FACTUSOL: no se ofrece otra vez", async () => {
    getOrderCobroBanco.mockResolvedValue({
      ...candidatos([]), estado: "conciliado", por_registrar: 0,
      conciliados: [{ ...mov({ status: "reconciled" }), importe_conciliado: 927, origen: null,
                      registrado: null }],
    });
    render(<CobroBancoBlock orderId="o-1" canConfirm />);
    expect(await screen.findByText("Lo conciliado ya consta cobrado en FACTUSOL.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Registrar el cobro/ })).toBeNull();
  });

  it("quien no registra cobros ve el movimiento pero no el botón", async () => {
    getOrderCobroBanco.mockResolvedValue(candidatos([{
      ...mov(), confianza: "alta", motivo: "número de factura en el concepto", parcial: false,
      excede: false,
    }]));
    render(<CobroBancoBlock orderId="o-1" canConfirm={false} />);
    expect(await screen.findByText("Lo hemos visto en el banco:")).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
  });
});
