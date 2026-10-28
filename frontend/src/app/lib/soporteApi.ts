import { apiFetch } from "./api";

/** Soporte de terceros (`/api/erp/soporte/fuentes`): la lista blanca de los
 *  centros de soporte de los fabricantes y su «Comprobar». Es de
 *  administración (`erp.config`). Buscar y leer los artículos no pasa por
 *  aquí: lo hace el asistente solo, en el servidor, al redactar con la casilla
 *  «Soporte» marcada. */

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
