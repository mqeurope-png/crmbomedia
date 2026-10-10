import type { LeadClasificacion } from "../../lib/erpApi";

/** Fixture de los tests de la ficha: el caso real del 10/10/2026
 *  (torracollons@elbarquito.net, clasificación 48cd85c8-…): formulario en DE
 *  con el texto en FR, confianza baja y todavía sin borrador. */
export function leadClasificacion(over: Partial<LeadClasificacion> = {}): LeadClasificacion {
  return {
    id: "48cd85c8-9ae8-45df-a645-17d62bbb11bd",
    contacto: { id: "c-torra", nombre: "Torra Collons", email: "torracollons@elbarquito.net" },
    fuente: "web_form", referencia: "env-9", lead_at: "2026-10-09T08:12:00Z",
    web: "pimpam-vending.com", cuenta_agile: null, productos: ["Vending"],
    texto: "Bonjour, je cherche un distributeur automatique…",
    idioma: "fr", idioma_fuente: "texto", idioma_formulario: "de", discrepancia_idioma: true,
    interes: "vending", interes_texto: "Vending", interes_fuente: "etiquetas",
    es_spam: false, confianza: 0.55, bajo_umbral: true,
    motivo: "Una sola palabra clave de vending en un texto largo; el formulario era alemán "
      + "pero el texto está en francés, así que la plantilla alemana no encaja.",
    proveedor: "anthropic", modelo: "claude-haiku",
    estado: "clasificado", estado_detalle: null,
    plantilla: null, remitente: null, borrador_id: null, borrador_url: null,
    tarea_id: null, run_id: "run-9",
    efectivo: { idioma: "fr", interes: "vending", interes_texto: "Vending", es_spam: false },
    correccion: { corregida: false, idioma: null, interes: null, es_spam: null, nota: null,
                  por: null, cuando: null },
    creado: "2026-10-09T08:12:30Z",
    texto_completo: "Bonjour, je cherche un distributeur automatique pour notre bureau de 40 "
      + "personnes.\n\nMerci,\nTorra",
    contexto: { fuente: "web_form", referencia: "env-9", sitio: "pimpam", formulario: "Contacto",
                idioma_formulario: "de", productos: ["Vending"], pais: "FR", cuenta_agile: null,
                dominio_email: "elbarquito.net" },
    idioma_discrepancia_texto: "el formulario era DE pero el texto está en FR",
    ...over,
  };
}
