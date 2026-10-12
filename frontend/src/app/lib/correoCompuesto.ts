/** Correos compuestos: la estructura de bloques que elige la IA (fichas de
 *  producto, tira de marca, vídeo, botones…) y que la persona retoca en la
 *  vista previa. El HTML del correo lo genera BoHub en el servidor, siempre a
 *  partir de la estructura (`renderCorreoCompuesto`): aquí solo se cambia la
 *  estructura (un texto, el orden de los bloques) y se lee lo editado. */

export type TipoBloque =
  | "texto"
  | "producto"
  | "productos_fila"
  | "marca"
  | "video"
  | "cta"
  | "destacado"
  | "separador";

/** Un texto de bloque: un texto o un texto por idioma (`{es: …, en: …}`). */
export type TextoBloque = string | Record<string, string>;

export type BloqueCompuesto = {
  id: string;
  tipo: TipoBloque;
  /** «texto»: HTML sencillo (párrafos, listas, negritas, enlaces). */
  html?: string;
  titulo?: TextoBloque;
  texto?: TextoBloque;
  boton?: TextoBloque;
  botones?: { texto: TextoBloque; url: TextoBloque }[];
  producto?: { fuente: string; origen_id: string };
  productos?: { fuente: string; origen_id: string }[];
  marca?: string;
  /** «vídeo»: QUÉ vídeo es; su URL, miniatura, título y duración los
   *  resuelve el servidor desde su origen (la URL pegada, la tabla de vídeos). */
  video?: { plataforma: "youtube" | "vimeo"; id: string };
  estilo?: string;
  [campo: string]: unknown;
};

export type EstructuraCompuesta = {
  version?: number;
  idioma: string;
  marca?: string | null;
  /** Las notas del pie (IVA…): si no está, las pone BoHub. */
  pie?: string | null;
  bloques: BloqueCompuesto[];
};

/** Lo que devuelve el servidor al generar el correo de una estructura. */
export type CorreoGenerado = {
  estructura: EstructuraCompuesta;
  html: string;
  texto: string;
  avisos: string[];
  precios: string[];
};

export const NOMBRE_BLOQUE: Record<TipoBloque, string> = {
  texto: "Texto",
  producto: "Ficha de producto",
  productos_fila: "Fichas en fila",
  marca: "Tira de marca",
  video: "Vídeo",
  cta: "Botones",
  destacado: "Destacado",
  separador: "Separador",
};

export function esEstructura(valor: unknown): valor is EstructuraCompuesta {
  return Boolean(valor) && typeof valor === "object"
    && Array.isArray((valor as EstructuraCompuesta).bloques);
}

export function estructuraVacia(e: EstructuraCompuesta | null | undefined): boolean {
  return !e || e.bloques.length === 0;
}

function enIdioma(valor: TextoBloque | undefined, idioma: string): string {
  if (!valor) return "";
  if (typeof valor === "string") return valor;
  return valor[idioma] ?? valor.es ?? Object.values(valor)[0] ?? "";
}

function textoPlano(html: string): string {
  return html.replace(/<[^>]+>/g, " ").replace(/&nbsp;/g, " ").replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/\s+/g, " ").trim();
}

/** «Texto · «Hola Marny, para el taller…»»: para la barra del bloque elegido. */
export function etiquetaBloque(b: BloqueCompuesto, idioma: string, max = 48): string {
  const nombre = NOMBRE_BLOQUE[b.tipo] ?? b.tipo;
  let resumen = "";
  if (b.tipo === "texto") resumen = textoPlano(b.html ?? "");
  else if (b.tipo === "productos_fila") resumen = `${b.productos?.length ?? 0} productos`;
  else if (b.tipo === "cta") resumen = (b.botones ?? []).map((x) => enIdioma(x.texto, idioma)).join(" · ");
  else resumen = enIdioma(b.titulo, idioma) || enIdioma(b.texto, idioma) || (b.marca ?? "");
  if (!resumen) return nombre;
  const corto = resumen.length > max ? `${resumen.slice(0, max - 1).trimEnd()}…` : resumen;
  return `${nombre} · «${corto}»`;
}

/** Un texto de bloque nuevo, respetando si el campo era por idioma. */
function conTexto(actual: unknown, valor: string, idioma: string): TextoBloque {
  if (actual && typeof actual === "object") {
    return { ...(actual as Record<string, string>), [idioma]: valor };
  }
  return valor;
}

/** La estructura con UN campo editado en la vista previa; todo lo demás (los
 *  bloques, su orden, sus id, sus productos) queda igual. `campo` es el de
 *  `data-bh-campo`: `"{id}:html"`, `"{id}:titulo"`, `"{id}:botones.0.texto"`,
 *  `":pie"`… Un campo que no existe deja la estructura como estaba. */
export function aplicarEdicion(
  e: EstructuraCompuesta, campo: string, valor: string,
): EstructuraCompuesta {
  const [id, nombre] = [campo.slice(0, campo.indexOf(":")), campo.slice(campo.indexOf(":") + 1)];
  if (campo.indexOf(":") < 0 || !nombre) return e;
  if (!id) return nombre === "pie" && (e.pie ?? "") !== valor ? { ...e, pie: valor } : e;
  const i = e.bloques.findIndex((b) => b.id === id);
  if (i < 0) return e;
  const bloque = e.bloques[i];
  let nuevo: BloqueCompuesto;
  const boton = /^botones\.(\d+)\.texto$/.exec(nombre);
  if (boton) {
    const n = Number(boton[1]);
    const botones = [...(bloque.botones ?? [])];
    if (!botones[n]) return e;
    if (enIdioma(botones[n].texto, e.idioma) === valor) return e;
    botones[n] = { ...botones[n], texto: conTexto(botones[n].texto, valor, e.idioma) };
    nuevo = { ...bloque, botones };
  } else if (nombre === "html") {
    if (bloque.html === valor) return e;
    nuevo = { ...bloque, html: valor };
  } else if (["titulo", "texto", "boton"].includes(nombre)) {
    if (enIdioma(bloque[nombre] as TextoBloque | undefined, e.idioma) === valor) return e;
    nuevo = { ...bloque, [nombre]: conTexto(bloque[nombre], valor, e.idioma) };
  } else {
    return e;
  }
  const bloques = [...e.bloques];
  bloques[i] = nuevo;
  return { ...e, bloques };
}

export function moverBloque(e: EstructuraCompuesta, id: string, delta: -1 | 1): EstructuraCompuesta {
  const i = e.bloques.findIndex((b) => b.id === id);
  const j = i + delta;
  if (i < 0 || j < 0 || j >= e.bloques.length) return e;
  const bloques = [...e.bloques];
  [bloques[i], bloques[j]] = [bloques[j], bloques[i]];
  return { ...e, bloques };
}

export function quitarBloque(e: EstructuraCompuesta, id: string): EstructuraCompuesta {
  return { ...e, bloques: e.bloques.filter((b) => b.id !== id) };
}

/** Un id de bloque nuevo, con la forma de los del servidor. */
export function nuevoIdBloque(): string {
  const azar = typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID().replace(/-/g, "")
    : Math.random().toString(16).slice(2) + Math.random().toString(16).slice(2);
  return `b${azar.slice(0, 8)}`;
}

/** El bloque copiado justo debajo, con un id nuevo. Devuelve también ese id. */
export function duplicarBloque(
  e: EstructuraCompuesta, id: string,
): { estructura: EstructuraCompuesta; id: string | null } {
  const i = e.bloques.findIndex((b) => b.id === id);
  if (i < 0) return { estructura: e, id: null };
  const copia: BloqueCompuesto = { ...JSON.parse(JSON.stringify(e.bloques[i])), id: nuevoIdBloque() };
  const bloques = [...e.bloques];
  bloques.splice(i + 1, 0, copia);
  return { estructura: { ...e, bloques }, id: copia.id };
}

/** El HTML final a partir del de la vista previa: el mismo, sin los
 *  atributos con los que la vista previa edita. */
export function htmlFinalDeVista(html: string): string {
  return html.replace(/\sdata-bh-[a-z]+="[^"]*"/g, "");
}

const ETIQUETAS_RICAS: Record<string, string> = {
  P: "p", BR: "br", STRONG: "strong", B: "strong", EM: "em", I: "em", U: "u",
  UL: "ul", OL: "ol", LI: "li", A: "a", DIV: "p",
};

function escapar(texto: string): string {
  return texto.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function escaparAtributo(texto: string): string {
  return escapar(texto).replace(/"/g, "&quot;");
}

function nodoRico(nodo: Node): string {
  if (nodo.nodeType === Node.TEXT_NODE) return escapar(nodo.textContent ?? "");
  if (nodo.nodeType !== Node.ELEMENT_NODE) return "";
  const el = nodo as HTMLElement;
  const hijos = Array.from(el.childNodes).map(nodoRico).join("");
  const etiqueta = ETIQUETAS_RICAS[el.tagName];
  if (!etiqueta) return hijos;                       // span, font…: fuera la etiqueta, queda el texto
  if (etiqueta === "br") return "<br>";
  if (etiqueta === "a") {
    const href = el.getAttribute("href") ?? "";
    return /^(https?:|mailto:)/i.test(href)
      ? `<a href="${escaparAtributo(href)}">${hijos}</a>`
      : hijos;
  }
  return `<${etiqueta}>${hijos}</${etiqueta}>`;
}

/** El texto rico de un bloque «texto» editado en la vista previa, limpio:
 *  solo párrafos, saltos, negritas, cursivas, subrayado, listas y enlaces,
 *  sin los estilos del correo (el servidor lo vuelve a limpiar). */
export function textoRicoDe(el: HTMLElement): string {
  return Array.from(el.childNodes).map(nodoRico).join("")
    .replace(/<p>(?:\s|<br>)*<\/p>/g, "")
    .trim();
}

/** El texto de un campo sencillo (un título, un botón, el pie): los saltos de
 *  línea se quedan. */
export function textoDeCampo(el: HTMLElement): string {
  const copia = el.cloneNode(true) as HTMLElement;
  copia.querySelectorAll("br").forEach((br) => br.replaceWith("\n"));
  return (copia.textContent ?? "").replace(/ /g, " ").replace(/[ \t]+\n/g, "\n").trim();
}

/** El texto plano de un compuesto pasado a un correo normal: párrafos. */
export function textoAHtml(texto: string): string {
  return texto
    .split(/\n{2,}/)
    .map((p) => p.trim())
    .filter(Boolean)
    .map((p) => `<p>${escapar(p).replace(/\n/g, "<br>")}</p>`)
    .join("");
}
