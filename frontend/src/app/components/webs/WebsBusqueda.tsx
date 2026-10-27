"use client";

import { Search } from "lucide-react";
import { type FormEvent, useEffect, useRef, useState } from "react";
import { extractErrorMessage } from "../../lib/errors";
import { type WebFragmento, type WebTipo, buscarEnWebs } from "../../lib/websApi";

/** «Buscar en las webs»: el buscador del índice de nuestras webs (páginas,
 *  entradas y productos que se recorren una vez al mes). Cada resultado dice
 *  de qué web es, qué es y en qué idioma, con el trozo que tiene que ver con
 *  la búsqueda, la imagen principal y el enlace. Con `onAnadir`, cada uno lleva
 *  «Añadir al borrador»: el redactor lo usa con su URL (es lo que la persona
 *  eligió, no algo que la IA vaya a buscar sola).
 *
 *  El precio es el que ENSEÑA la web, y se dice así: nunca es un precio de
 *  oferta (el de una oferta sale de FACTUSOL). */
export type WebsBusquedaProps = {
  /** El idioma del correo, si se sabe: se busca solo en ese (se puede quitar). */
  idioma?: string | null;
  /** Con texto, se busca nada más abrir. */
  consultaInicial?: string;
  /** Ids ya elegidos: salen como añadidos y no se repiten. */
  elegidos?: string[];
  /** Cuántos caben en el borrador (el redactor admite 4). */
  maximo?: number;
  onAnadir?: (fragmento: WebFragmento) => void;
};

const TIPOS: { id: WebTipo | ""; label: string }[] = [
  { id: "", label: "Todo" },
  { id: "producto", label: "Productos" },
  { id: "pagina", label: "Páginas" },
  { id: "entrada", label: "Entradas" },
];

const TIPO_LABEL: Record<string, string> = { pagina: "Página", entrada: "Entrada", producto: "Producto" };

/** Solo enlaces web: lo que venga del índice nunca acaba en un `href` raro. */
function esWeb(url: string | null | undefined): url is string {
  return typeof url === "string" && /^https?:\/\//i.test(url);
}

/** El precio tal cual lo enseña la web y de qué tipo es. */
export function precioWeb(f: Pick<WebFragmento, "precio_web" | "precio_web_texto" | "precio_iva" | "moneda">):
  { importe: string; etiqueta: string } | null {
  let importe = (f.precio_web_texto ?? "").trim();
  if (!importe && typeof f.precio_web === "number" && f.precio_web > 0) {
    try {
      importe = new Intl.NumberFormat("es-ES", { style: "currency", currency: f.moneda || "EUR" })
        .format(f.precio_web);
    } catch {
      importe = `${f.precio_web.toFixed(2).replace(".", ",")} ${f.moneda ?? ""}`.trim();
    }
  }
  if (!importe) return null;
  const etiqueta = f.precio_iva === "sin_iva"
    ? "precio en la web (sin IVA)"
    : f.precio_iva === "con_iva"
      ? "precio en la web (con IVA)"
      : "precio en la web";
  return { importe, etiqueta };
}

export function WebsBusqueda({
  idioma = null,
  consultaInicial = "",
  elegidos = [],
  maximo = 4,
  onAnadir,
}: WebsBusquedaProps) {
  const [consulta, setConsulta] = useState(consultaInicial);
  const [tipo, setTipo] = useState<WebTipo | "">("");
  const [soloIdioma, setSoloIdioma] = useState(Boolean(idioma));
  const [resultados, setResultados] = useState<WebFragmento[] | null>(null);
  const [buscada, setBuscada] = useState("");
  const [buscando, setBuscando] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Solo cuenta la respuesta de la última búsqueda (una lenta no pisa a otra).
  const turno = useRef(0);

  async function buscar(texto: string) {
    const q = texto.trim();
    if (!q) {
      setError("Escribe qué buscar: un modelo, un accesorio, una página…");
      return;
    }
    const mio = ++turno.current;
    setBuscando(true);
    setError(null);
    try {
      const items = await buscarEnWebs(q, {
        idioma: soloIdioma ? idioma : null, tipo: tipo || null,
      });
      if (mio !== turno.current) return;
      setResultados(items);
      setBuscada(q);
    } catch (err) {
      if (mio !== turno.current) return;
      setError(extractErrorMessage(err, "No se ha podido buscar en las webs."));
    } finally {
      if (mio === turno.current) setBuscando(false);
    }
  }

  // Con una consulta de partida, se busca al abrir.
  const inicial = useRef(consultaInicial);
  useEffect(() => {
    if (inicial.current.trim()) void buscar(inicial.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- solo al montar
  }, []);

  function enviar(e: FormEvent<HTMLFormElement>) {
    // Este buscador puede ir dentro de otro formulario (el compositor de
    // correo): su envío no puede llegar al de fuera.
    e.preventDefault();
    e.stopPropagation();
    void buscar(consulta);
  }

  const lleno = elegidos.length >= maximo;

  return (
    <div className="webs-busqueda">
      <form className="webs-busqueda-form" role="search" aria-label="Buscar en nuestras webs" onSubmit={enviar}>
        <input
          type="search"
          aria-label="Qué buscar en nuestras webs"
          placeholder="Un modelo, un accesorio, una página…"
          value={consulta}
          onChange={(e) => setConsulta(e.target.value)}
          autoFocus
        />
        <select aria-label="Qué buscar" value={tipo} onChange={(e) => setTipo(e.target.value as WebTipo | "")}>
          {TIPOS.map((t) => <option key={t.id} value={t.id}>{t.label}</option>)}
        </select>
        <button type="submit" className="button small" disabled={buscando}>
          <Search size={12} aria-hidden /> {buscando ? "Buscando…" : "Buscar"}
        </button>
      </form>
      {idioma ? (
        <label className="webs-busqueda-idioma">
          <input type="checkbox" checked={soloIdioma} onChange={(e) => setSoloIdioma(e.target.checked)} />
          Solo en el idioma del correo ({idioma.toUpperCase()})
        </label>
      ) : null}
      {onAnadir ? (
        <p className="muted small webs-busqueda-nota">
          {lleno
            ? `Ya hay ${maximo} páginas elegidas: quita una en la franja para añadir otra.`
            : `Lo que añadas va al borrador con su enlace (hasta ${maximo}; llevas ${elegidos.length}).`}
        </p>
      ) : null}
      {error ? <p className="form-error" role="alert">{error}</p> : null}
      {resultados !== null && resultados.length === 0 && !buscando ? (
        <p className="muted small webs-busqueda-vacio" role="status">
          No hay nada en las webs con «{buscada}». Prueba con otras palabras o con el nombre del modelo.
        </p>
      ) : null}
      {resultados && resultados.length > 0 ? (
        <ul className="webs-busqueda-lista" aria-label="Resultados de las webs">
          {resultados.map((f) => {
            const precio = precioWeb(f);
            const elegido = elegidos.includes(f.id);
            return (
              <li key={f.id} className="webs-resultado">
                {esWeb(f.imagen) ? (
                  // eslint-disable-next-line @next/next/no-img-element -- imagen de otra web, enlazada tal cual
                  <img className="webs-resultado-imagen" src={f.imagen} alt="" loading="lazy" referrerPolicy="no-referrer" />
                ) : <span className="webs-resultado-imagen is-vacia" aria-hidden />}
                <div className="webs-resultado-cuerpo">
                  <p className="webs-resultado-titulo">
                    {esWeb(f.url)
                      ? <a href={f.url} target="_blank" rel="noreferrer">{f.titulo || f.url}</a>
                      : <strong>{f.titulo}</strong>}
                  </p>
                  <p className="webs-resultado-meta">
                    {f.dominio || f.sitio} · {TIPO_LABEL[f.tipo] ?? f.tipo}
                    {f.idioma ? ` · ${f.idioma.toUpperCase()}` : ""}
                  </p>
                  {f.fragmento ? <p className="webs-resultado-fragmento">{f.fragmento}</p> : null}
                  {precio ? (
                    <p className="webs-resultado-precio">
                      <span>{precio.etiqueta}: </span><strong>{precio.importe}</strong>
                      <span className="webs-resultado-precio-nota"> · lo que dice la web, no es precio de oferta</span>
                    </p>
                  ) : null}
                </div>
                {onAnadir ? (
                  <button
                    type="button"
                    className="button secondary small webs-resultado-anadir"
                    disabled={elegido || lleno}
                    onClick={() => onAnadir(f)}
                    title={elegido ? "Ya está en el borrador" : lleno ? `Caben ${maximo}: quita una para añadir otra` : "La IA la tendrá delante, con su enlace"}
                  >
                    {elegido ? "Añadida al borrador" : "Añadir al borrador"}
                  </button>
                ) : null}
              </li>
            );
          })}
        </ul>
      ) : null}
    </div>
  );
}
