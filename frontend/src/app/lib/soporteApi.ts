import { apiFetch } from "./api";

/** Soporte de terceros (`/api/erp/soporte`): buscar y leer artículos de los
 *  centros de soporte de la lista blanca. Es CONSULTA, no envía nada: lo usa
 *  la oficina desde el redactor («Buscar en soporte»; el mismo ámbito que el
 *  asistente). La lista blanca es de administración (`erp.config`). */

export type SoporteResultado = {
  fuente: string;
  fuente_nombre: string;
  articulo_id: string;
  titulo: string;
  /** Texto plano, recortado. */
  extracto: string;
  url: string;
  idioma: string | null;
};

export type SoporteBusqueda = {
  consulta: string;
  resultados: SoporteResultado[];
  /** Lo que conviene saber (una fuente no respondió, sin resultados…). */
  avisos: string[];
  /** Las fuentes que han respondido (clave): en ellas se ha mirado. */
  fuentes: string[];
  /** Las que no se han podido consultar (fallaron o no dio tiempo). */
  fallidas?: string[];
};

export type SoporteArticulo = {
  fuente: string;
  fuente_nombre: string;
  articulo_id: string;
  titulo: string;
  url: string;
  idioma: string | null;
  /** Texto limpio (sin menús, pies ni laterales), con saltos de párrafo. */
  texto: string;
  /** HTML saneado para leer (se pinta en un iframe sin scripts). */
  html: string;
  actualizado: string | null;
};

export type SoporteFuente = {
  clave: string;
  nombre: string;
  dominio: string;
  tipo: "zendesk" | "wordpress" | "html";
  base_url: string;
  marca: string | null;
  locales: Record<string, string>;
  activo: boolean;
  posicion: number;
  notas: string | null;
  comprobado_en: string | null;
  comprobado_estado: "ok" | "vacio" | "error" | null;
  comprobado_detalle: string | null;
};

export type SoporteFuenteIn = {
  clave?: string;
  nombre?: string;
  dominio?: string;
  tipo?: SoporteFuente["tipo"];
  base_url?: string;
  marca?: string | null;
  locales?: Record<string, string>;
  activo?: boolean;
  posicion?: number;
  notas?: string | null;
};

function qs(params: Record<string, string | number | null | undefined>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

export async function buscarSoporte(
  q: string, opts: { idioma?: string | null; fuente?: string | null } = {},
): Promise<SoporteBusqueda> {
  return apiFetch<SoporteBusqueda>(`/api/erp/soporte/buscar${qs({ q, idioma: opts.idioma, fuente: opts.fuente })}`);
}

export async function leerArticuloSoporte(
  fuente: string, articuloId: string, idioma?: string | null,
): Promise<SoporteArticulo> {
  return apiFetch<SoporteArticulo>(
    `/api/erp/soporte/articulo${qs({ fuente, id: articuloId, idioma })}`,
  );
}

export async function listarFuentesSoporte(): Promise<SoporteFuente[]> {
  const r = await apiFetch<{ items: SoporteFuente[] }>("/api/erp/soporte/fuentes");
  return r.items;
}

export async function crearFuenteSoporte(payload: SoporteFuenteIn): Promise<SoporteFuente> {
  return apiFetch<SoporteFuente>("/api/erp/soporte/fuentes", {
    method: "POST", body: JSON.stringify(payload),
  });
}

export async function actualizarFuenteSoporte(
  clave: string, patch: SoporteFuenteIn,
): Promise<SoporteFuente> {
  return apiFetch<SoporteFuente>(`/api/erp/soporte/fuentes/${encodeURIComponent(clave)}`, {
    method: "PATCH", body: JSON.stringify(patch),
  });
}

/** Comprueba qué devuelve de verdad la fuente (contenido, cáscara vacía o error). */
export async function comprobarFuenteSoporte(clave: string): Promise<SoporteFuente> {
  return apiFetch<SoporteFuente>(
    `/api/erp/soporte/fuentes/${encodeURIComponent(clave)}/comprobar`, { method: "POST" },
  );
}
