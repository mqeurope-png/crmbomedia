import { ApiError, apiFetch } from "./api";
import {
  coincidePedidoWeb,
  errorAlTraer,
  listarPedidosWebEnEspera,
  numeroAConsultar,
  traerPedidoWeb,
  type PedidoWebEnEspera,
} from "./pedidosWebApi";

jest.mock("./api", () => ({
  ...jest.requireActual("./api"),
  apiFetch: jest.fn(),
}));

const mockFetch = apiFetch as jest.Mock;

/** El 5873 de fluxlasers.es (el figurante de los tests del backend). */
function pedido(over: Partial<PedidoWebEnEspera> = {}): PedidoWebEnEspera {
  return {
    store_id: 3, tienda: "fluxlasers", tienda_nombre: "fluxlasers.es", woo_id: 5873,
    numero: "5873", fecha: "2026-10-23T09:18:59+00:00", cliente: "Núria Vidal Pérez",
    empresa: "Escola de Ponent", email: "secretaria@ponent.cat", total: 44.77, moneda: "EUR",
    forma_pago: "Transferencia bancaria directa", enlace_tienda: null, en_bohub: null,
    ...over,
  };
}

beforeEach(() => mockFetch.mockReset());

describe("cliente de «Traer un pedido de la web»", () => {
  it("lee los pedidos en espera, con y sin búsqueda", async () => {
    mockFetch.mockResolvedValue({ items: [], tiendas: [], consulta: [] });
    await listarPedidosWebEnEspera();
    expect(mockFetch).toHaveBeenLastCalledWith("/api/erp/pedidos-web/en-espera");
    await listarPedidosWebEnEspera("  Escola de Ponent ");
    expect(mockFetch).toHaveBeenLastCalledWith(
      "/api/erp/pedidos-web/en-espera?q=Escola%20de%20Ponent",
    );
  });

  it("trae un pedido con POST {store_id, woo_id}", async () => {
    mockFetch.mockResolvedValue({ order_id: "o-1", order_number: "FLUXLA-5873", creado: true, factusol: null });
    const r = await traerPedidoWeb(3, 5873);
    expect(r.order_number).toBe("FLUXLA-5873");
    expect(mockFetch).toHaveBeenCalledWith("/api/erp/pedidos-web/traer", {
      method: "POST", body: JSON.stringify({ store_id: 3, woo_id: 5873 }),
    });
  });

  it("el 409 de «ya hay pedido en BoHub» da su mensaje y el pedido para abrir", () => {
    const e = new ApiError("Ya hay en BoHub un pedido para este pedido web (FLUXLA-5873).", 409, {
      code: "ya_hay_pedido_en_bohub",
      detail: "Ya hay en BoHub un pedido para este pedido web (FLUXLA-5873).",
      order_id: "o-9",
    });
    expect(errorAlTraer(e)).toEqual({
      mensaje: "Ya hay en BoHub un pedido para este pedido web (FLUXLA-5873).",
      code: "ya_hay_pedido_en_bohub",
      orderId: "o-9",
    });
    const cancelado = new ApiError("El pedido 5888 está cancelado en la web: no se puede traer.", 409, {
      code: "cancelado_en_la_web", detail: "El pedido 5888 está cancelado en la web: no se puede traer.",
    });
    expect(errorAlTraer(cancelado).orderId).toBeNull();
    expect(errorAlTraer(new TypeError("Failed to fetch")).mensaje)
      .toBe("No se pudo conectar con el servidor.");
  });

  it("el buscador casa por número, cliente, empresa o email, sin tildes ni mayúsculas", () => {
    const p = pedido();
    expect(coincidePedidoWeb(p, "")).toBe(true);
    expect(coincidePedidoWeb(p, "#5873")).toBe(true);
    expect(coincidePedidoWeb(p, "587")).toBe(true);
    expect(coincidePedidoWeb(p, "nuria")).toBe(true);
    expect(coincidePedidoWeb(p, "PÓNENT")).toBe(true);
    expect(coincidePedidoWeb(p, "secretaria@")).toBe(true);
    expect(coincidePedidoWeb(p, "artisjet")).toBe(false);
  });

  it("solo se pregunta a las tiendas por un nº que no está en la lista", () => {
    const items = [pedido()];
    expect(numeroAConsultar(items, "5873")).toBeNull();
    expect(numeroAConsultar(items, " #5888 ")).toBe("5888");
    expect(numeroAConsultar(items, "Ponent")).toBeNull();
    expect(numeroAConsultar(items, "")).toBeNull();
  });
});
