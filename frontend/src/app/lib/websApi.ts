import { apiFetch } from "./api";

/** Índice de las webs propias (`/api/erp/webs`): buscar (para el redactor),
 *  el estado por web y tipo, «Reindexar ahora» y las reglas. Solo lee de las
 *  webs, por sus endpoints públicos. */

export type WebTipo = "pagina" | "entrada" | "producto";

export type WebFragmento = {
  id: string;
  sitio: string;
  dominio: string;
  tipo: WebTipo;
  idioma: string;
  titulo: string;
  url: string;
  /** Los párrafos que tienen que ver con la búsqueda, recortados. */
  fragmento: string;
  imagen: string | null;
  galeria: string[];
  categorias: string[];
  /** Precio de la WEB (lo que dice la web). Nunca es precio de oferta. */
  precio_web: number | null;
  precio_web_texto: string | null;
  precio_iva: "sin_iva" | "con_iva" | "desconocido" | null;
  moneda: string | null;
};

export type WebsEstadoFila = {
  sitio: string;
  dominio: string;
  pagina: number;
  entrada: number;
  producto: number;
  idos: number;
  idiomas: string[];
  ultima: string | null;
};

export type WebsPasada = {
  id: string;
  origen: "programada" | "manual";
  estado: "en_cola" | "corriendo" | "ok" | "parcial" | "error";
  inicio: string | null;
  fin: string | null;
  pendientes: string[];
  resumen: Record<string, {
    estado: string;
    paginas?: number;
    entradas?: number;
    productos?: number;
    nuevos?: number;
    actualizados?: number;
    idos?: number;
    error?: string | null;
  }>;
  error: string | null;
  creado_en: string | null;
};

export type WebsEstado = {
  filas: WebsEstadoFila[];
  total: { pagina: number; entrada: number; producto: number };
  ultima_pasada: WebsPasada | null;
  en_curso: WebsPasada | null;
};

export type WebRegla = {
  id: number;
  tipo: "pagina_excluir_slug" | "pagina_excluir_id" | "categoria_fuera"
    | "producto_excluir_id" | "producto_incluir_id";
  patron: string;
  sitio: string | null;
  activo: boolean;
  nota: string | null;
  posicion: number;
};

export type WebReglaIn = Partial<Omit<WebRegla, "id">>;

function qs(params: Record<string, string | number | null | undefined>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

export async function buscarEnWebs(
  q: string, opts: { idioma?: string | null; sitio?: string | null; tipo?: WebTipo | null } = {},
): Promise<WebFragmento[]> {
  const r = await apiFetch<{ items: WebFragmento[] }>(
    `/api/erp/webs/buscar${qs({ q, idioma: opts.idioma, sitio: opts.sitio, tipo: opts.tipo })}`,
  );
  return r.items;
}

export async function estadoWebs(): Promise<WebsEstado> {
  return apiFetch<WebsEstado>("/api/erp/webs/estado");
}

/** Encola un recorrido (o devuelve el que ya está en cola o en curso). */
export async function reindexarWebs(): Promise<WebsPasada> {
  return apiFetch<WebsPasada>("/api/erp/webs/reindexar", { method: "POST" });
}

export async function listarReglasWebs(): Promise<WebRegla[]> {
  const r = await apiFetch<{ items: WebRegla[] }>("/api/erp/webs/reglas");
  return r.items;
}

export async function crearReglaWebs(payload: WebReglaIn): Promise<WebRegla> {
  return apiFetch<WebRegla>("/api/erp/webs/reglas", { method: "POST", body: JSON.stringify(payload) });
}

export async function actualizarReglaWebs(id: number, patch: WebReglaIn): Promise<WebRegla> {
  return apiFetch<WebRegla>(`/api/erp/webs/reglas/${id}`, { method: "PATCH", body: JSON.stringify(patch) });
}

export async function borrarReglaWebs(id: number): Promise<{ ok: boolean }> {
  return apiFetch<{ ok: boolean }>(`/api/erp/webs/reglas/${id}`, { method: "DELETE" });
}
