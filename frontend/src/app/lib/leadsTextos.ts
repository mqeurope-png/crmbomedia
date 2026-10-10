/** Textos de la respuesta a leads que comparten la lista ERP · Leads y la
 *  ficha del contacto (recuadro del Resumen y pestaña «Análisis IA»): la misma
 *  clasificación tiene que decir lo mismo en los dos sitios. */

export const FUENTE_LABEL: Record<string, string> = {
  web_form: "Formulario web", agilecrm: "AgileCRM", manual: "Manual",
};

export const ESTADO_LABEL: Record<string, string> = {
  clasificado: "Clasificado", spam: "Spam", preparado: "Borrador preparado",
  sin_plantilla: "Sin plantilla", omitido: "Omitido",
};

export const ESTADO_TONE: Record<string, string> = {
  clasificado: "muted", spam: "bad", preparado: "active", sin_plantilla: "warn", omitido: "muted",
};

export const ORIGEN_DATO: Record<string, string> = {
  etiquetas: "por los productos marcados", formulario: "por el formulario",
  texto: "por el texto", ia: "por la IA", palabras_clave: "por palabras clave",
  pais: "por el país", contacto: "por el contacto",
};

export function porcentaje(c: number): string {
  return `${Math.round(c * 100)}%`;
}

export function fuenteTexto(fuente: string, web: string | null, cuenta: string | null): string {
  const base = FUENTE_LABEL[fuente] ?? fuente;
  if (web) return `${base} · ${web}`;
  if (cuenta) return `${base} · ${cuenta}`;
  return base;
}

export function origenDato(valor: string | null | undefined): string {
  return valor ? (ORIGEN_DATO[valor] ?? valor) : "";
}

export function estadoLabel(estado: string): string {
  return ESTADO_LABEL[estado] ?? estado;
}

export function estadoTone(estado: string): string {
  return ESTADO_TONE[estado] ?? "muted";
}
