import { ApiError, apiFetch } from "./api";
import { extractErrorMessage } from "./errors";

/** «Traer un pedido de la web» (`/api/erp/pedidos-web`): los pedidos «en
 *  espera» (`on-hold`: transferencia que la tienda aún no ha cobrado) de las
 *  tiendas WooCommerce, leídos en vivo, y traer uno a BoHub por el mismo
 *  camino que el importador. Solo quien crea pedidos Y ve los pedidos web
 *  (`erp.orders.create` + `erp.orders.view_web`: pedidos y admin). Nada de
 *  esto escribe en las tiendas ni en FACTUSOL. */

/** El pedido de BoHub que ya corresponde a un pedido de la web. */
export type PedidoWebEnBohub = { order_id: string; order_number: string };

/** Una fila de la lista: un pedido `on-hold` de una tienda. */
export type PedidoWebEnEspera = {
  /** Id de la tienda en BoHub (`integration_accounts.id`). */
  store_id: string | number;
  /** Clave de la tienda (`fluxlasers`) y su nombre visible (`fluxlasers.es`). */
  tienda: string;
  tienda_nombre: string;
  /** Id del pedido en WooCommerce (lo que se manda al traer). */
  woo_id: number;
  /** Nº de pedido que ve el cliente en la web (casi siempre = `woo_id`). */
  numero: string;
  /** Fecha del pedido (ISO con zona). */
  fecha: string | null;
  cliente: string | null;
  empresa: string | null;
  email: string | null;
  total: number;
  moneda: string;
  forma_pago: string | null;
  /** El pedido en el administrador de la tienda. */
  enlace_tienda: string | null;
  /** Ya está en BoHub (se ve en la lista, marcado): `{order_id, order_number}`. */
  en_bohub: PedidoWebEnBohub | null;
};

/** Qué es en cada tienda un nº buscado que no está entre los `on-hold`. */
export type PedidoWebConsulta = PedidoWebEnEspera & {
  /** Estado de WooCommerce (`cancelled`, `pending`, `processing`…). */
  estado: string;
  /** Motivo legible: «cancelado en la web: no se puede traer»… */
  motivo: string;
  /** `processing`/`completed` que aún no ha llegado: se puede traer. */
  se_puede_traer?: boolean;
};

/** Cómo ha ido cada tienda: una que no responde no tumba a las demás. */
export type TiendaEnEspera = {
  store_id: string | number;
  tienda: string;
  tienda_nombre: string;
  ok: boolean;
  error: string | null;
  en_espera: number;
};

export type PedidosWebEnEspera = {
  items: PedidoWebEnEspera[];
  tiendas: TiendaEnEspera[];
  consulta: PedidoWebConsulta[];
};

/** Veredicto de FACTUSOL al traer (solo LECTURA: nunca se crea nada allí). */
export type VeredictoFactusolCodigo =
  | "enlazado" | "posible" | "no_encontrado" | "dudoso" | "sin_factusol";

export type VeredictoFactusol = {
  codigo: VeredictoFactusolCodigo | string;
  mensaje: string;
  /** Referencia común del pedido de cliente (`REFPCL`, p. ej. `FLE-005873`). */
  referencia: string | null;
};

export type PedidoWebTraido = {
  order_id: string;
  order_number: string;
  /** false = ya estaba en BoHub (traer dos veces no crea dos). */
  creado: boolean;
  factusol: VeredictoFactusol | null;
};

/** Bloque `traido_a_mano` del detalle del pedido (`GET /api/erp/orders/{id}`):
 *  solo lo llevan los pedidos web traídos a mano. */
export type TraidoAMano = {
  /** Cuándo se trajo (ISO). */
  at: string;
  /** Quién lo trajo (nombre), si se sabe. */
  por: string | null;
  factusol: VeredictoFactusol | null;
  /** Lo que cambió en la web entre el `on-hold` y el cobro (no se toca el
   *  pedido: se deja constancia para revisarlo). */
  diferencias: string[] | null;
};

export async function listarPedidosWebEnEspera(q?: string | null): Promise<PedidosWebEnEspera> {
  const texto = (q ?? "").trim();
  const qs = texto ? `?q=${encodeURIComponent(texto)}` : "";
  return apiFetch<PedidosWebEnEspera>(`/api/erp/pedidos-web/en-espera${qs}`);
}

export async function traerPedidoWeb(
  storeId: string | number, wooId: number,
): Promise<PedidoWebTraido> {
  return apiFetch<PedidoWebTraido>("/api/erp/pedidos-web/traer", {
    method: "POST",
    body: JSON.stringify({ store_id: storeId, woo_id: wooId }),
  });
}

/** El error de «Traer» para enseñarlo en la fila: el mensaje del backend, su
 *  código y, si ya hay un pedido en BoHub (409 `ya_hay_pedido_en_bohub`), su
 *  id para ofrecer «Abrir». */
export function errorAlTraer(e: unknown): {
  mensaje: string; code: string | null; orderId: string | null;
} {
  const mensaje = extractErrorMessage(e, "No se pudo traer el pedido.");
  if (!(e instanceof ApiError)) return { mensaje, code: null, orderId: null };
  const det = e.detail && typeof e.detail === "object" && !Array.isArray(e.detail)
    ? (e.detail as { order_id?: unknown })
    : {};
  const orderId = typeof det.order_id === "string" && det.order_id ? det.order_id : null;
  return { mensaje, code: e.code, orderId };
}

/** Minúsculas y sin tildes (como `_sin_tildes` del backend): «lopez»
 *  encuentra «López». */
export function sinTildes(texto: string | null | undefined): string {
  return (texto ?? "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().trim();
}

/** El texto del buscador, listo para comparar (sin «#» delante). */
export function textoBusqueda(q: string): string {
  return sinTildes(q).replace(/^#+/, "").trim();
}

/** ¿La fila casa con el buscador? Por número, cliente, empresa o email. */
export function coincidePedidoWeb(item: PedidoWebEnEspera, q: string): boolean {
  const texto = textoBusqueda(q);
  if (!texto) return true;
  return [item.numero, item.cliente, item.empresa, item.email]
    .some((campo) => sinTildes(campo).includes(texto));
}

/** Si el buscador es un nº que NO está entre los `on-hold` cargados, ese nº
 *  (hay que preguntar a las tiendas qué es); si no, null. */
export function numeroAConsultar(items: PedidoWebEnEspera[], q: string): string | null {
  const texto = textoBusqueda(q);
  if (!/^\d+$/.test(texto)) return null;
  return items.some((it) => it.numero === texto) ? null : texto;
}
