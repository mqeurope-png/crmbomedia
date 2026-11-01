import { apiFetch } from "./api";
import type { HistoricoTipoDocumento } from "./erpApi";

/** El análisis del parque de una empresa (1/11/2026). Los hechos los calcula
 *  el servidor (con su documento o su fecha); la IA solo escribe la lectura y
 *  «por dónde entrar», citando los hechos por su id. */

export type BloqueAnalisis = "tiene" | "consume" | "no_compra" | "cambio" | "no_se_sabe";

export type FuenteHecho =
  | { tipo: "documento"; doc: { tipo: HistoricoTipoDocumento; serie: string; codigo: number;
                                ejercicio: string; numero: string } }
  | { tipo: "fecha"; fecha: string | null }
  | { tipo: "manual"; fecha: string | null };

export type HechoAnalisis = {
  id: string;
  bloque: BloqueAnalisis;
  texto: string;
  fuente: FuenteHecho | null;
  gama: string | null;
};

export type ProductoAnalisis = {
  id: string; codart: string; descripcion: string; gama: string; gama_nombre: string;
  ventas: number; ultima_venta: string | null;
};

export type HechosAnalisis = {
  estado: "sin_cliente" | "sin_datos" | "con_datos" | "no_cliente_final";
  hechos: HechoAnalisis[];
  productos: ProductoAnalisis[];
};

export type FraseCitada = { texto: string; citas: string[] };

export type AnalisisParque = {
  company_id: string;
  datos: HechosAnalisis["estado"];
  estado: "sin_generar" | "al_dia" | "desactualizado" | "caducado";
  hechos_actuales: HechosAnalisis;
  analisis: {
    hechos: HechosAnalisis;
    lectura: FraseCitada[];
    entrada: (FraseCitada & { productos: { id: string; codart: string; descripcion: string }[] })
      | null;
    descartado: string[];
  } | null;
  generado_at: string | null;
  modelo: string | null;
  coste_usd: number | null;
  caducidad_meses: number;
};

export type AnalisisParqueConfig = {
  modelo: string;
  caducidad_meses: number;
  anios_sin_confirmar: number;
  meses_catalogo: number;
  solo_admin: boolean;
};

export async function getPermisoAnalisis(): Promise<{ puede: boolean; solo_admin: boolean }> {
  return apiFetch("/api/analisis-parque/permiso");
}

export async function getAnalisisParque(companyId: string): Promise<AnalisisParque> {
  return apiFetch(`/api/companies/${encodeURIComponent(companyId)}/analisis-parque`);
}

export async function generarAnalisisParque(companyId: string, forzar = false):
    Promise<AnalisisParque> {
  return apiFetch(`/api/companies/${encodeURIComponent(companyId)}/analisis-parque/generar`, {
    method: "POST", body: JSON.stringify({ forzar }),
  });
}

export async function generarAnalisisLote(companyIds: string[]): Promise<{ encolados: number }> {
  return apiFetch("/api/analisis-parque/generar-lote", {
    method: "POST", body: JSON.stringify({ company_ids: companyIds }),
  });
}
