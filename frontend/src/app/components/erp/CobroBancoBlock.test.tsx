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
jest.mock("../../lib/erpApi", () => ({
  getOrderCobroBanco: (...a: unknown[]) => getOrderCobroBanco(...a),
}));
// LA herramienta de cobro (con sus propios tests): aquí, que se abre con el
// pedido y el movimiento elegido.
jest.mock("./RegistrarCobroModal", () => ({
  RegistrarCobroModal: ({ orderId, movimientoId, onDone, onClose }: {
    orderId: string; movimientoId: string; onDone?: () => void; onClose: () => void;
  }) => (
    <div role="dialog" aria-label="cobro">
      COBRO {orderId} {movimientoId}
      <button type="button" onClick={() => onDone?.()}>HECHO</button>
      <button type="button" onClick={onClose}>CERRAR</button>
    </div>
  ),
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
});

describe("CobroBancoBlock — el cobro que propone el banco", () => {
  it("«Marcar cobrado» abre la herramienta de cobro con ese movimiento; al terminar, recarga", async () => {
    getOrderCobroBanco.mockResolvedValue(candidatos([cand()]));
    const onDone = jest.fn();
    const user = userEvent.setup();
    render(<CobroBancoBlock orderId="o-1" canConfirm canConciliar onDone={onDone} />);
    await user.click(await screen.findByRole("button", { name: "Marcar cobrado con esta fecha" }));
    const dialogo = screen.getByRole("dialog", { name: "cobro" });
    expect(dialogo).toHaveTextContent("COBRO o-1 m-1");
    await user.click(within(dialogo).getByRole("button", { name: "HECHO" }));
    expect(onDone).toHaveBeenCalled();
    await waitFor(() => expect(getOrderCobroBanco).toHaveBeenCalledTimes(2));
  });

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

  it("un importe distinto (menor) es un cobro PARCIAL: se dice y no se da por cobrada", async () => {
    getOrderCobroBanco.mockResolvedValue(candidatos([{
      ...mov({ importe: 500 }), confianza: "media", motivo: "número de factura en el concepto",
      parcial: true, excede: false,
    }]));
    const user = userEvent.setup();
    render(<CobroBancoBlock orderId="o-1" canConfirm canConciliar />);
    expect(await screen.findByText(/sería un cobro parcial, la factura no quedaría cobrada/))
      .toBeInTheDocument();
    // El cobro parcial lo dice el diálogo de cobro (sus tests).
    await user.click(screen.getByRole("button", { name: "Marcar cobrado con esta fecha" }));
    expect(screen.getByRole("dialog", { name: "cobro" })).toHaveTextContent("COBRO o-1 m-1");
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
    render(<CobroBancoBlock orderId="o-1" canConfirm canConciliar={false} />);
    expect(await screen.findByText(/Es más que lo pendiente/)).toHaveTextContent(
      "Es más que lo pendiente (927,00 €): si paga varias facturas, pide a un administrador que lo reparta en Conciliación.",
    );
    expect(screen.queryByRole("link", { name: "Ver el movimiento" })).toBeNull();

  });

  it("con acceso a Conciliación: el reparto y la contrapartida se hacen allí", async () => {
    getOrderCobroBanco.mockResolvedValue(candidatos([
      cand({ importe: 1200, excede: true }),
      cand({ id: "m-2", cuenta: { ...CUENTA, nombre: "Cuenta Figurante B", contrapartida_codigo: null } }),
    ]));
    render(<CobroBancoBlock orderId="o-1" canConfirm canConciliar />);
    expect(await screen.findByText(/Es más que lo pendiente/)).toHaveTextContent(
      "si paga varias facturas, repártelo en Conciliación.",
    );
    expect(screen.getAllByRole("link", { name: "Ver el movimiento" })).toHaveLength(2);

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

});
