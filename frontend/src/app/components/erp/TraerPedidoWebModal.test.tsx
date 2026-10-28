import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ApiError } from "../../lib/api";
import {
  listarPedidosWebEnEspera,
  traerPedidoWeb,
  type PedidoWebEnEspera,
  type PedidosWebEnEspera,
} from "../../lib/pedidosWebApi";
import { TraerPedidoWebModal } from "./TraerPedidoWebModal";

const push = jest.fn();
jest.mock("next/navigation", () => ({
  useRouter: () => ({ push, replace: jest.fn() }),
}));
jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ children, href, ...rest }: { children: React.ReactNode; href: string }) => (
    <a href={href} {...rest}>{children}</a>
  ),
}));
// Las funciones puras (buscador, errores) son las de verdad; solo se simula la red.
jest.mock("../../lib/pedidosWebApi", () => ({
  ...jest.requireActual("../../lib/pedidosWebApi"),
  listarPedidosWebEnEspera: jest.fn(),
  traerPedidoWeb: jest.fn(),
}));

const listar = listarPedidosWebEnEspera as jest.Mock;
const traer = traerPedidoWeb as jest.Mock;

/** El 5873 de fluxlasers.es (Escola de Ponent, 44,77 €, transferencia). */
function pedido(over: Partial<PedidoWebEnEspera> = {}): PedidoWebEnEspera {
  return {
    store_id: 3, tienda: "fluxlasers", tienda_nombre: "fluxlasers.es", woo_id: 5873,
    numero: "5873", fecha: "2026-10-23T09:18:59+00:00", cliente: "Núria Vidal",
    empresa: "Escola de Ponent", email: "secretaria@ponent.cat", total: 44.77, moneda: "EUR",
    forma_pago: "Transferencia bancaria directa",
    enlace_tienda: "https://fluxlasers.es/wp-admin/post.php?post=5873&action=edit",
    en_bohub: null,
    ...over,
  };
}

const OTRO = pedido({
  store_id: 5, tienda: "mbolasers", tienda_nombre: "mbolasers.com", woo_id: 1204, numero: "1204",
  fecha: "2026-10-20T11:00:00+00:00", cliente: "Jordi López", empresa: null,
  email: "jordi@taller.es", total: 1210, forma_pago: "Transferencia bancaria directa",
  enlace_tienda: null,
});
const YA = pedido({
  woo_id: 5860, numero: "5860", cliente: "Marta Soler", empresa: "Gràfiques Soler",
  email: "marta@soler.cat", total: 99.5,
  en_bohub: { order_id: "o-9", order_number: "FLUXLA-5860" },
});

function respuesta(over: Partial<PedidosWebEnEspera> = {}): PedidosWebEnEspera {
  return {
    items: [pedido(), OTRO, YA],
    tiendas: [
      { store_id: 3, tienda: "fluxlasers", tienda_nombre: "fluxlasers.es", ok: true, error: null, en_espera: 2 },
      { store_id: 5, tienda: "mbolasers", tienda_nombre: "mbolasers.com", ok: true, error: null, en_espera: 1 },
    ],
    consulta: [],
    ...over,
  };
}

function tabla() {
  return within(screen.getByRole("table", { name: "Pedidos en espera de las tiendas" }));
}

async function abrir() {
  render(<TraerPedidoWebModal onClose={jest.fn()} />);
  expect(screen.getByRole("dialog", { name: "Traer un pedido de la web" })).toBeInTheDocument();
  return screen.findByRole("table", { name: "Pedidos en espera de las tiendas" });
}

beforeEach(() => {
  push.mockReset();
  listar.mockReset();
  traer.mockReset();
  listar.mockResolvedValue(respuesta());
});

describe("Traer un pedido de la web (modal)", () => {
  it("lee las tiendas al abrir y lista los pedidos en espera con sus datos", async () => {
    render(<TraerPedidoWebModal onClose={jest.fn()} />);
    expect(screen.getByRole("status")).toHaveTextContent("Leyendo los pedidos en espera de las tiendas");
    await screen.findByRole("table", { name: "Pedidos en espera de las tiendas" });
    expect(listar).toHaveBeenCalledWith();
    expect(screen.getByText(/Traerlo lo deja en BoHub como un pedido normal/)).toBeInTheDocument();
    const fila = within(tabla().getByText("Escola de Ponent").closest("tr") as HTMLElement);
    expect(fila.getByText("fluxlasers.es")).toBeInTheDocument();
    expect(fila.getByText("5873")).toBeInTheDocument();
    expect(fila.getByText("23/10/2026")).toBeInTheDocument();
    expect(fila.getByText("Núria Vidal")).toBeInTheDocument();
    expect(fila.getByText("44.77 EUR")).toBeInTheDocument();
    expect(fila.getByText("Transferencia bancaria directa")).toBeInTheDocument();
    expect(fila.getByRole("button", { name: "Traer el pedido 5873 de fluxlasers.es" })).toBeEnabled();
    expect(tabla().getAllByRole("row")).toHaveLength(4); // cabecera + 3
    expect(screen.getByText("En espera ahora: fluxlasers.es (2) · mbolasers.com (1)")).toBeInTheDocument();
  });

  it("filtra por número y por cliente, sin tildes ni mayúsculas", async () => {
    const user = userEvent.setup();
    await abrir();
    const buscar = screen.getByRole("searchbox", { name: /Buscar por nº de pedido/ });
    await user.type(buscar, "1204");
    expect(tabla().getAllByRole("row")).toHaveLength(2);
    expect(tabla().getByText("Jordi López")).toBeInTheDocument();
    await user.clear(buscar);
    await user.type(buscar, "LOPEZ");
    expect(tabla().getAllByRole("row")).toHaveLength(2);
    expect(tabla().getByText("Jordi López")).toBeInTheDocument();
    await user.clear(buscar);
    await user.type(buscar, "ponent");
    expect(tabla().getAllByRole("row")).toHaveLength(2);
    expect(tabla().getByText("Escola de Ponent")).toBeInTheDocument();
    // Un nº de la lista no pregunta a las tiendas.
    expect(listar).toHaveBeenCalledTimes(1);
  });

  it("dice qué tienda no responde sin dejar de listar las demás", async () => {
    listar.mockResolvedValue(respuesta({
      items: [pedido()],
      tiendas: [
        { store_id: 3, tienda: "fluxlasers", tienda_nombre: "fluxlasers.es", ok: true, error: null, en_espera: 1 },
        { store_id: 7, tienda: "artisjet", tienda_nombre: "artisjet-printers.eu", ok: false,
          error: "la tienda no ha respondido a tiempo (20 s)", en_espera: 0 },
      ],
    }));
    await abrir();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "artisjet-printers.eu no responde (la tienda no ha respondido a tiempo (20 s))",
    );
    expect(tabla().getByText("Escola de Ponent")).toBeInTheDocument();
  });

  it("un nº cancelado en la web: lo pregunta a las tiendas y dice que no se puede traer", async () => {
    const user = userEvent.setup();
    await abrir();
    listar.mockResolvedValueOnce(respuesta({
      items: [],
      consulta: [{
        ...pedido({ woo_id: 5888, numero: "5888", total: 120 }),
        estado: "cancelled", motivo: "cancelado en la web: no se puede traer", se_puede_traer: false,
      }],
    }));
    await user.type(screen.getByRole("searchbox", { name: /Buscar por nº de pedido/ }), "5888");
    const consulta = await screen.findByRole("region", { name: "El nº 5888 en las tiendas" });
    expect(await within(consulta).findByText("cancelado en la web: no se puede traer")).toBeInTheDocument();
    expect(listar).toHaveBeenLastCalledWith("5888");
    expect(within(consulta).queryByRole("button", { name: /Traer/ })).not.toBeInTheDocument();
    expect(screen.getByText("Ningún pedido en espera coincide con «5888».")).toBeInTheDocument();
  });

  it("un nº procesando que no ha llegado se puede traer desde la consulta", async () => {
    const user = userEvent.setup();
    await abrir();
    listar.mockResolvedValueOnce(respuesta({
      items: [],
      consulta: [{
        ...pedido({ woo_id: 5901, numero: "5901" }),
        estado: "processing", motivo: "procesando: entra solo (si no ha llegado, se puede traer)",
        se_puede_traer: true,
      }],
    }));
    traer.mockResolvedValueOnce({ order_id: "o-5901", order_number: "FLUXLA-5901", creado: true, factusol: null });
    await user.type(screen.getByRole("searchbox", { name: /Buscar por nº de pedido/ }), "5901");
    const consulta = await screen.findByRole("region", { name: "El nº 5901 en las tiendas" });
    await user.click(await within(consulta).findByRole("button", { name: "Traer el pedido 5901 de fluxlasers.es" }));
    expect(traer).toHaveBeenCalledWith(3, 5901);
    await waitFor(() => expect(push).toHaveBeenCalledWith("/erp/orders/o-5901"));
  });

  it("«Traer» bloquea, trae el pedido y abre el pedido creado", async () => {
    const user = userEvent.setup();
    let resolver: (v: unknown) => void = () => undefined;
    traer.mockReturnValue(new Promise((r) => { resolver = r; }));
    await abrir();
    const boton = tabla().getByRole("button", { name: "Traer el pedido 5873 de fluxlasers.es" });
    await user.click(boton);
    expect(traer).toHaveBeenCalledWith(3, 5873);
    expect(boton).toBeDisabled();
    expect(boton).toHaveTextContent("Trayendo…");
    expect(tabla().getByRole("button", { name: "Traer el pedido 1204 de mbolasers.com" })).toBeDisabled();
    resolver({
      order_id: "o-new", order_number: "FLUXLA-5873", creado: true,
      factusol: { codigo: "enlazado", mensaje: "…", referencia: "FLE-005873" },
    });
    await waitFor(() => expect(push).toHaveBeenCalledWith("/erp/orders/o-new"));
    expect(traer).toHaveBeenCalledTimes(1);
  });

  it("un 409 deja el mensaje del backend en la fila (con «Abrir» si ya hay pedido)", async () => {
    const user = userEvent.setup();
    traer.mockRejectedValueOnce(new ApiError(
      "Ya hay en BoHub un pedido para este pedido web (MAN-7001): no se trae otro.", 409, {
        code: "ya_hay_pedido_en_bohub",
        detail: "Ya hay en BoHub un pedido para este pedido web (MAN-7001): no se trae otro.",
        order_id: "o-7001",
      },
    ));
    await abrir();
    await user.click(tabla().getByRole("button", { name: "Traer el pedido 5873 de fluxlasers.es" }));
    const aviso = await screen.findByRole("alert");
    expect(aviso).toHaveTextContent("Ya hay en BoHub un pedido para este pedido web (MAN-7001)");
    expect(within(aviso).getByRole("link", { name: "Abrir" })).toHaveAttribute("href", "/erp/orders/o-7001");
    expect(push).not.toHaveBeenCalled();
    // Se puede volver a intentar (o traer otro).
    expect(tabla().getByRole("button", { name: "Traer el pedido 5873 de fluxlasers.es" })).toBeEnabled();

    traer.mockRejectedValueOnce(new ApiError(
      "El pedido 1204 está cancelado en la web: no se puede traer.", 409, {
        code: "cancelado_en_la_web",
        detail: "El pedido 1204 está cancelado en la web: no se puede traer.",
      },
    ));
    await user.click(tabla().getByRole("button", { name: "Traer el pedido 1204 de mbolasers.com" }));
    const otro = await screen.findByText("El pedido 1204 está cancelado en la web: no se puede traer.");
    expect(within(otro).queryByRole("link", { name: "Abrir" })).not.toBeInTheDocument();
  });

  it("el que ya está en BoHub sale marcado, con «Abrir» y sin «Traer»", async () => {
    await abrir();
    const fila = within(tabla().getByText("Gràfiques Soler").closest("tr") as HTMLElement);
    expect(fila.getByText("Ya está en BoHub")).toBeInTheDocument();
    expect(fila.getByRole("link", { name: "Abrir FLUXLA-5860" })).toHaveAttribute("href", "/erp/orders/o-9");
    expect(fila.queryByRole("button", { name: /Traer/ })).not.toBeInTheDocument();
  });

  it("si la lista no se puede leer, lo dice", async () => {
    listar.mockRejectedValueOnce(new ApiError("No tienes permiso.", 403, "No tienes permiso."));
    render(<TraerPedidoWebModal onClose={jest.fn()} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("No tienes permiso.");
  });
});
