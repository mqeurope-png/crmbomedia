import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ApiError } from "../../lib/api";
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

/** Un candidato sin su cobro en FACTUSOL. */
function cand(over: Record<string, unknown> = {}) {
  return {
    ...mov(), confianza: "alta", motivo: "número de factura en el concepto", parcial: false,
    excede: false, ya_en_factusol: null, ...over,
  };
}

/** Un movimiento ya conciliado con la factura. */
function conc(over: Record<string, unknown> = {}) {
  return {
    ...mov({ status: "reconciled" }), importe_conciliado: 927, origen: null, registrado: null,
    en_cola: false, ...over,
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
    render(<CobroBancoBlock orderId="o-1" canConfirm canConciliar />);
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
    render(<CobroBancoBlock orderId="o-1" canConfirm canConciliar onDone={onDone} />);
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
    await waitFor(() => expect(confirmarCobroBanco).toHaveBeenCalledWith("o-1", "m-1", { forzar: false }));
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
    render(<CobroBancoBlock orderId="o-1" canConfirm canConciliar />);
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
    render(<CobroBancoBlock orderId="o-1" canConfirm canConciliar />);
    expect(await screen.findByText(/Hay 2 movimientos del banco que pueden ser este cobro/))
      .toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Es este: marcar cobrado con esta fecha" }))
      .toHaveLength(2);
    expect(screen.queryByRole("group", { name: "Confirmar el cobro" })).toBeNull();
    expect(screen.queryByRole("checkbox")).toBeNull();
  });

  it("sin movimiento no se dice nada", async () => {
    getOrderCobroBanco.mockResolvedValue({ ...candidatos([]), estado: "nada" });
    const { container } = render(<CobroBancoBlock orderId="o-1" canConfirm canConciliar />);
    await waitFor(() => expect(getOrderCobroBanco).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it("ya cobrada: no propone nada; como mucho dice con qué movimiento se cobró", async () => {
    getOrderCobroBanco.mockResolvedValue({
      ...candidatos([]), estado: "cobrada", cobrada: true,
      conciliados: [{ ...mov({ status: "reconciled" }), importe_conciliado: 927, origen: null }],
    });
    render(<CobroBancoBlock orderId="o-1" canConfirm canConciliar />);
    expect(await screen.findByText("Cobrado con este movimiento del banco:")).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByRole("link", { name: "Ver el movimiento" })).toBeInTheDocument();
  });

  it("ya conciliado en Conciliación y sin cobro en FACTUSOL: ofrece registrarlo con esa fecha (el caso de un cobro visto el 29/10)", async () => {
    getOrderCobroBanco.mockResolvedValue({
      ...candidatos([]), estado: "conciliado", por_registrar: 927,
      conciliados: [conc()],
    });
    render(<CobroBancoBlock orderId="o-1" canConfirm canConciliar />);
    expect(await screen.findByText("Lo hemos visto en el banco (ya conciliado):")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Registrar el cobro con esta fecha" })).toBeInTheDocument();
  });

  it("conciliado cuyo cobro ya consta en FACTUSOL: dice cuándo y no se ofrece otra vez", async () => {
    getOrderCobroBanco.mockResolvedValue({
      ...candidatos([]), estado: "conciliado", por_registrar: 0,
      conciliados: [conc({ registrado: "2026-08-27" })],
    });
    render(<CobroBancoBlock orderId="o-1" canConfirm canConciliar />);
    expect(await screen.findByText("Cobro registrado en FACTUSOL el 27/08/2026")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Registrar el cobro/ })).toBeNull();
    expect(screen.queryByText("Lo conciliado ya consta cobrado en FACTUSOL.")).toBeNull();
  });

  it("conciliado con su cobro aún en cola: lo dice y no se ofrece registrarlo", async () => {
    getOrderCobroBanco.mockResolvedValue({
      ...candidatos([]), estado: "conciliado", por_registrar: 0,
      conciliados: [conc({ en_cola: true })],
    });
    render(<CobroBancoBlock orderId="o-1" canConfirm canConciliar />);
    expect(await screen.findByText(/^En cola en FACTUSOL/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Registrar el cobro/ })).toBeNull();
    expect(screen.queryByText("Lo conciliado ya consta cobrado en FACTUSOL.")).toBeNull();
  });

  it("dos conciliados, uno ya en FACTUSOL: solo se ofrece registrar el otro", async () => {
    getOrderCobroBanco.mockResolvedValue({
      ...candidatos([], 427), estado: "conciliado", por_registrar: 427,
      conciliados: [
        conc({ importe_conciliado: 500, registrado: "2026-08-26" }),
        conc({ id: "m-2", fecha_valor: "2026-09-10", importe_conciliado: 427 }),
      ],
    });
    render(<CobroBancoBlock orderId="o-1" canConfirm canConciliar />);
    expect(await screen.findByText("Cobro registrado en FACTUSOL el 26/08/2026")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Registrar el cobro con esta fecha" })).toHaveLength(1);
  });

  it("quien no registra cobros ve el movimiento pero no el botón", async () => {
    getOrderCobroBanco.mockResolvedValue(candidatos([{
      ...mov(), confianza: "alta", motivo: "número de factura en el concepto", parcial: false,
      excede: false,
    }]));
    render(<CobroBancoBlock orderId="o-1" canConfirm={false} canConciliar />);
    expect(await screen.findByText("Lo hemos visto en el banco:")).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("sin acceso a Conciliación: sin «Ver el movimiento» y lo de allí se pide a un administrador", async () => {
    getOrderCobroBanco.mockResolvedValue(candidatos([
      cand({ importe: 1200, excede: true }),
      cand({ id: "m-2", cuenta: { ...CUENTA, nombre: "Cuenta Figurante B", contrapartida_codigo: null } }),
    ]));
    const user = userEvent.setup();
    render(<CobroBancoBlock orderId="o-1" canConfirm canConciliar={false} />);
    expect(await screen.findByText(/Es más que lo pendiente/)).toHaveTextContent(
      "Es más que lo pendiente (927,00 €): si paga varias facturas, pide a un administrador que lo reparta en Conciliación.",
    );
    expect(screen.queryByRole("link", { name: "Ver el movimiento" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Es este: marcar cobrado con esta fecha" }));
    expect(screen.getByText(/no tiene contrapartida de cobro/)).toHaveTextContent(
      "La cuenta Cuenta Figurante B no tiene contrapartida de cobro: pide a un administrador que la enlace en Conciliación → Cuentas.",
    );
  });

  it("con acceso a Conciliación: el reparto y la contrapartida se hacen allí", async () => {
    getOrderCobroBanco.mockResolvedValue(candidatos([
      cand({ importe: 1200, excede: true }),
      cand({ id: "m-2", cuenta: { ...CUENTA, nombre: "Cuenta Figurante B", contrapartida_codigo: null } }),
    ]));
    const user = userEvent.setup();
    render(<CobroBancoBlock orderId="o-1" canConfirm canConciliar />);
    expect(await screen.findByText(/Es más que lo pendiente/)).toHaveTextContent(
      "si paga varias facturas, repártelo en Conciliación.",
    );
    expect(screen.getAllByRole("link", { name: "Ver el movimiento" })).toHaveLength(2);
    await user.click(screen.getByRole("button", { name: "Es este: marcar cobrado con esta fecha" }));
    expect(screen.getByText(/no tiene contrapartida de cobro/))
      .toHaveTextContent("enlázala en Conciliación → Cuentas antes.");
  });

  it("candidato cuyo cobro ya está en FACTUSOL: solo se concilia (sin registrar otro cobro)", async () => {
    getOrderCobroBanco.mockResolvedValue(candidatos([cand({ ya_en_factusol: "2026-08-27" })]));
    confirmarCobroBanco.mockResolvedValue({
      status: "already", numero: "2-526081", importe: 927, parcial: false,
      fecha_factusol: "2026-08-27", movimiento: mov(),
    });
    const onDone = jest.fn();
    const user = userEvent.setup();
    render(<CobroBancoBlock orderId="o-1" canConfirm canConciliar onDone={onDone} />);
    expect(await screen.findByText(/Este cobro ya consta en FACTUSOL/)).toHaveTextContent(
      "Este cobro ya consta en FACTUSOL (27/08/2026): al confirmar solo se concilia el movimiento.",
    );
    expect(screen.queryByRole("button", { name: /Marcar cobrado/ })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Conciliar con esta factura" }));
    const panel = within(screen.getByRole("group", { name: "Confirmar el cobro" }));
    expect(panel.queryByText(/se registra en FACTUSOL/)).toBeNull();
    expect(panel.getAllByRole("listitem")).toHaveLength(2);
    expect(panel.getByText(/el movimiento queda conciliado con la 2-526081/)).toBeInTheDocument();
    expect(panel.getByText(/la factura se escribe en la hoja de Drive del banco/)).toBeInTheDocument();
    await user.click(panel.getByLabelText("Confirmo el cobro con este movimiento"));
    await user.click(panel.getByRole("button", { name: "Conciliar" }));
    expect(await screen.findByRole("status")).toHaveTextContent(
      "El cobro ya constaba en FACTUSOL (registrado el 27/08/2026): el movimiento queda conciliado con la 2-526081.",
    );
    expect(waitForInvoiceCollectionJob).not.toHaveBeenCalled();
    expect(onDone).toHaveBeenCalled();
    expect(screen.queryByRole("group", { name: "Confirmar el cobro" })).toBeNull();
  });

  it("ya en FACTUSOL y mayor que lo que queda (lo pendiente ya lo descuenta): se concilia igual, sin avisos de parcial ni reparto", async () => {
    getOrderCobroBanco.mockResolvedValue(candidatos([
      cand({ importe: 500, excede: true, ya_en_factusol: "2026-08-27" }),
    ], 427));
    render(<CobroBancoBlock orderId="o-1" canConfirm canConciliar />);
    expect(await screen.findByRole("button", { name: "Conciliar con esta factura" })).toBeInTheDocument();
    expect(screen.queryByText(/Es más que lo pendiente/)).toBeNull();
    expect(screen.queryByText(/cobro parcial/)).toBeNull();
  });

  it("si el cobro queda en cola en FACTUSOL: cierra el panel, recarga y lo dice", async () => {
    getOrderCobroBanco.mockResolvedValue(candidatos([cand()]));
    confirmarCobroBanco.mockResolvedValue({
      status: "queued", job_id: "j-1", numero: "2-526081", importe: 927, parcial: false,
      movimiento: mov(),
    });
    waitForInvoiceCollectionJob.mockResolvedValue({ status: "pending" });
    const onDone = jest.fn();
    const user = userEvent.setup();
    render(<CobroBancoBlock orderId="o-1" canConfirm canConciliar onDone={onDone} />);
    await user.click(await screen.findByRole("button", { name: "Marcar cobrado con esta fecha" }));
    await user.click(screen.getByLabelText("Confirmo el cobro con este movimiento"));
    // Tras confirmar, el banco ya lo da por conciliado y en cola.
    getOrderCobroBanco.mockResolvedValue({
      ...candidatos([]), estado: "conciliado", conciliados: [conc({ en_cola: true })],
    });
    await user.click(screen.getByRole("button", { name: "Registrar el cobro" }));
    await waitFor(() => expect(getOrderCobroBanco).toHaveBeenCalledTimes(2));
    expect(await screen.findByText(/^En cola en FACTUSOL/)).toBeInTheDocument();
    expect(screen.getByText(/El cobro sigue en cola en FACTUSOL/)).toBeInTheDocument();
    expect(onDone).toHaveBeenCalled();
    expect(screen.queryByRole("group", { name: "Confirmar el cobro" })).toBeNull();
    expect(screen.queryByRole("checkbox")).toBeNull();
  });

  it("si FACTUSOL falla: cierra el panel, recarga y ofrece reintentar desde lo conciliado", async () => {
    getOrderCobroBanco.mockResolvedValue(candidatos([cand()]));
    confirmarCobroBanco.mockResolvedValue({
      status: "queued", job_id: "j-1", numero: "2-526081", importe: 927, parcial: false,
      movimiento: mov(),
    });
    waitForInvoiceCollectionJob.mockResolvedValue({ status: "failed", error: "FACTUSOL caído" });
    const onDone = jest.fn();
    const user = userEvent.setup();
    render(<CobroBancoBlock orderId="o-1" canConfirm canConciliar onDone={onDone} />);
    await user.click(await screen.findByRole("button", { name: "Marcar cobrado con esta fecha" }));
    await user.click(screen.getByLabelText("Confirmo el cobro con este movimiento"));
    getOrderCobroBanco.mockResolvedValue({
      ...candidatos([]), estado: "conciliado", por_registrar: 927, conciliados: [conc()],
    });
    await user.click(screen.getByRole("button", { name: "Registrar el cobro" }));
    expect(await screen.findByRole("button", { name: "Registrar el cobro con esta fecha" }))
      .toBeInTheDocument();
    expect(screen.getByText(/FACTUSOL no ha registrado el cobro/)).toHaveTextContent(
      "El movimiento queda conciliado con la 2-526081, pero FACTUSOL no ha registrado el cobro (FACTUSOL caído). Vuelve a intentarlo con «Registrar el cobro con esta fecha».",
    );
    expect(onDone).toHaveBeenCalled();
    expect(screen.queryByRole("group", { name: "Confirmar el cobro" })).toBeNull();
  });

  it("si el backend no deja (cobro en cola, 409): enseña su motivo y el panel sigue abierto", async () => {
    getOrderCobroBanco.mockResolvedValue(candidatos([cand()]));
    confirmarCobroBanco.mockRejectedValue(new ApiError(
      "El cobro de ese movimiento ya se mandó a FACTUSOL desde la ficha y sigue en cola: espera un momento y vuelve a mirar.",
      409, { code: "en_cola", detail: "El cobro de ese movimiento ya se mandó a FACTUSOL desde la ficha y sigue en cola: espera un momento y vuelve a mirar." },
    ));
    const onDone = jest.fn();
    const user = userEvent.setup();
    render(<CobroBancoBlock orderId="o-1" canConfirm canConciliar onDone={onDone} />);
    await user.click(await screen.findByRole("button", { name: "Marcar cobrado con esta fecha" }));
    await user.click(screen.getByLabelText("Confirmo el cobro con este movimiento"));
    await user.click(screen.getByRole("button", { name: "Registrar el cobro" }));
    expect(await screen.findByText(/ya se mandó a FACTUSOL desde la ficha y sigue en cola/))
      .toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Confirmar el cobro" })).toBeInTheDocument();
    expect(onDone).not.toHaveBeenCalled();
    expect(waitForInvoiceCollectionJob).not.toHaveBeenCalled();
  });

  it("si no se puede encolar el cobro (cobro_no_encolado), el movimiento SÍ quedó conciliado: cierra el panel y recarga", async () => {
    getOrderCobroBanco.mockResolvedValue(candidatos([cand()]));
    const detalle = "El movimiento queda conciliado con la 2-526081, pero el cobro no se ha "
      + "podido mandar a FACTUSOL (la cola no responde).";
    confirmarCobroBanco.mockRejectedValue(new ApiError(detalle, 503, {
      code: "cobro_no_encolado", detail: detalle,
    }));
    const onDone = jest.fn();
    const user = userEvent.setup();
    render(<CobroBancoBlock orderId="o-1" canConfirm canConciliar onDone={onDone} />);
    await user.click(await screen.findByRole("button", { name: "Marcar cobrado con esta fecha" }));
    await user.click(screen.getByLabelText("Confirmo el cobro con este movimiento"));
    await user.click(screen.getByRole("button", { name: "Registrar el cobro" }));
    expect(await screen.findByText(/queda conciliado con la 2-526081, pero el cobro no/))
      .toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Confirmar el cobro" })).toBeNull();
    expect(onDone).toHaveBeenCalled();
    await waitFor(() => expect(getOrderCobroBanco).toHaveBeenCalledTimes(2));
  });

  it("ya en FACTUSOL pero en OTRA cuenta: lo dice (puede ser otro pago del mismo importe)", async () => {
    getOrderCobroBanco.mockResolvedValue(candidatos([cand({
      importe: 500, parcial: true, ya_en_factusol: "2026-08-27",
      ya_en_factusol_cuenta: "1 · Caja", ya_en_factusol_otra_cuenta: true,
    })], 1000));
    render(<CobroBancoBlock orderId="o-1" canConfirm canConciliar />);
    const nota = await screen.findByText(/Este cobro ya consta en FACTUSOL/);
    expect(nota).toHaveTextContent("(27/08/2026, cuenta 1 · Caja)");
    expect(nota).toHaveTextContent("está apuntado en otra cuenta que la de este banco");
  });

  it("un cobro igual ya apuntado a mano en FACTUSOL (con otra fecha): lo enseña y no deja registrar sin decir que es otro pago", async () => {
    getOrderCobroBanco.mockResolvedValue({
      ...candidatos([cand({ importe: 427, parcial: true, posible_doble: true })]),
      cobros_sueltos: [{ fecha: "2026-09-20", importe: 427, cuenta: "6 · Banco Figurante" }],
    });
    confirmarCobroBanco.mockResolvedValue({
      status: "queued", job_id: "j-1", numero: "2-526081", importe: 427, parcial: true,
      movimiento: mov({ importe: 427 }),
    });
    waitForInvoiceCollectionJob.mockResolvedValue({
      status: "finished", result: { registered: true, status: "registered", importe: 427 },
    });
    const user = userEvent.setup();
    render(<CobroBancoBlock orderId="o-1" canConfirm canConciliar />);
    expect(await screen.findByLabelText("Cobros ya en FACTUSOL")).toHaveTextContent(
      "20/09/2026 · 427,00 € · cuenta 6 · Banco Figurante",
    );
    await user.click(screen.getByRole("button", { name: "Marcar cobrado con esta fecha" }));
    expect(screen.getByText(/Puede ser este mismo pago, apuntado a mano/)).toBeInTheDocument();
    await user.click(screen.getByLabelText("Confirmo el cobro con este movimiento"));
    const registrar = screen.getByRole("button", { name: "Registrar el cobro" });
    expect(registrar).toBeDisabled();
    await user.click(screen.getByLabelText("Es otro pago: registrarlo igualmente"));
    expect(registrar).toBeEnabled();
    await user.click(registrar);
    await waitFor(() => expect(confirmarCobroBanco).toHaveBeenCalledWith(
      "o-1", "m-1", { forzar: true },
    ));
  });
});
