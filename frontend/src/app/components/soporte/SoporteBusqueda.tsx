"use client";

import { BookOpen, ExternalLink, Search } from "lucide-react";
import { type FormEvent, useEffect, useRef, useState } from "react";
import { extractErrorMessage } from "../../lib/errors";
import {
  buscarSoporte,
  leerArticuloSoporte,
  type SoporteArticulo,
  type SoporteBusqueda as SoporteRespuesta,
  type SoporteResultado,
} from "../../lib/soporteApi";

/** «Buscar en soporte»: el buscador de los centros de soporte de los
 *  fabricantes que están en la lista blanca (FLUX…). Cada resultado enseña
 *  título, extracto, de qué centro es y el enlace al artículo; «Leer» lo trae
 *  limpio para leerlo aquí mismo.
 *
 *  Dos usos:
 *  - Consulta (sin `onUsar`): la Cola SAT. Se busca y se lee; no hay nada que
 *    enviar ni que meter en ningún correo.
 *  - Redactor (con `onUsar` / `onSinResultados`): la persona elige el
 *    artículo que explica el procedimiento y el borrador lo cita con su
 *    enlace. Si la búsqueda no encuentra nada, puede pedir que el borrador lo
 *    diga en vez de inventarse los pasos. */
export type SoporteBusquedaProps = {
  /** El idioma en el que se busca de entrada (el del correo, si se sabe). */
  idioma?: string | null;
  /** Con texto, se busca nada más abrir. */
  consultaInicial?: string;
  onUsar?: (r: SoporteResultado) => void;
  onSinResultados?: (consulta: string) => void;
};

/** Lo mismo que admite el backend (`servicio.MAX_CONSULTA`). */
const MAX_CONSULTA = 300;

/** Los idiomas que se pueden pedir. El centro de soporte traduce el código a
 *  su «locale» (la lista blanca dice cuál); el que no tenga ese idioma
 *  contesta en el suyo por defecto. */
const IDIOMAS: { codigo: string; nombre: string }[] = [
  { codigo: "es", nombre: "castellano" },
  { codigo: "en", nombre: "inglés" },
  { codigo: "fr", nombre: "francés" },
  { codigo: "de", nombre: "alemán" },
  { codigo: "it", nombre: "italiano" },
  { codigo: "pt", nombre: "portugués" },
  { codigo: "nl", nombre: "neerlandés" },
];

function idiomaInicial(idioma: string | null | undefined): string {
  // «es-ES», «pt-PT», «EN»: se busca por las dos letras del idioma.
  const codigo = (idioma ?? "").trim().toLowerCase().slice(0, 2);
  return /^[a-z]{2}$/.test(codigo) ? codigo : "es";
}

/** Solo enlaces web: lo que venga de fuera nunca acaba en un `href` raro. */
function esWeb(url: string | null | undefined): url is string {
  return typeof url === "string" && /^https?:\/\//i.test(url);
}

function claveDe(r: Pick<SoporteResultado, "fuente" | "articulo_id">): string {
  return `${r.fuente}:${r.articulo_id}`;
}

function fechaCorta(iso: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleDateString("es-ES", { day: "2-digit", month: "2-digit", year: "numeric" });
}

/** El documento del iframe: el HTML ya viene saneado del servidor (sin
 *  scripts ni estilos de la página) y además el iframe va con `sandbox=""`,
 *  así que nada de lo que traiga puede ejecutarse ni navegar. Solo se le da
 *  una tipografía legible y que las imágenes no se salgan. */
function documentoArticulo(html: string): string {
  return [
    "<!doctype html><html><head><meta charset=\"utf-8\">",
    "<style>",
    "body{margin:12px;font-family:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;",
    "font-size:0.9rem;line-height:1.5;color:#1b2330;background:#fff;overflow-wrap:anywhere}",
    "img,video{max-width:100%;height:auto}",
    "table{border-collapse:collapse;max-width:100%}td,th{border:1px solid #d5dae1;padding:4px 6px}",
    "pre,code{white-space:pre-wrap}",
    "</style></head><body>",
    html,
    "</body></html>",
  ].join("");
}

export function SoporteBusqueda({
  idioma = null,
  consultaInicial = "",
  onUsar,
  onSinResultados,
}: SoporteBusquedaProps) {
  const [consulta, setConsulta] = useState(consultaInicial);
  const [idiomaSel, setIdiomaSel] = useState(() => idiomaInicial(idioma));
  const [busqueda, setBusqueda] = useState<SoporteRespuesta | null>(null);
  const [buscando, setBuscando] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // El artículo abierto (uno a la vez), por `fuente:id`.
  const [abierto, setAbierto] = useState<string | null>(null);
  const [articulo, setArticulo] = useState<SoporteArticulo | null>(null);
  const [leyendo, setLeyendo] = useState(false);
  const [errorArticulo, setErrorArticulo] = useState<string | null>(null);
  // Solo cuenta la respuesta de la última petición: una búsqueda o una
  // lectura lenta no pisa a la que se pidió después.
  const turnoBusqueda = useRef(0);
  const turnoLectura = useRef(0);

  async function buscar(texto: string) {
    const q = texto.trim();
    if (!q) {
      setError("Escribe qué buscar: el modelo de la máquina y lo que le pasa o lo que hay que hacer.");
      return;
    }
    const mio = ++turnoBusqueda.current;
    turnoLectura.current += 1;
    setBuscando(true);
    setError(null);
    setAbierto(null);
    setArticulo(null);
    setErrorArticulo(null);
    setLeyendo(false);
    try {
      const r = await buscarSoporte(q, { idioma: idiomaSel });
      if (mio !== turnoBusqueda.current) return;
      setBusqueda(r);
    } catch (err) {
      if (mio !== turnoBusqueda.current) return;
      // Sin respuesta no es «sin resultados»: no se ofrece decir que no hay
      // procedimiento cuando no se ha podido mirar.
      setBusqueda(null);
      setError(extractErrorMessage(err, "No se ha podido buscar en el soporte."));
    } finally {
      if (mio === turnoBusqueda.current) setBuscando(false);
    }
  }

  // Con una consulta de partida, se busca al abrir.
  const inicial = useRef(consultaInicial);
  useEffect(() => {
    if (inicial.current.trim()) void buscar(inicial.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- solo al montar
  }, []);

  function enviar(e: FormEvent<HTMLFormElement>) {
    // El buscador puede acabar dentro de otro formulario (el compositor de
    // correo): su envío no puede llegar al de fuera.
    e.preventDefault();
    e.stopPropagation();
    void buscar(consulta);
  }

  async function leer(r: SoporteResultado) {
    const clave = claveDe(r);
    if (abierto === clave) {
      // Segundo clic: se cierra.
      turnoLectura.current += 1;
      setAbierto(null);
      setArticulo(null);
      setErrorArticulo(null);
      setLeyendo(false);
      return;
    }
    const mio = ++turnoLectura.current;
    setAbierto(clave);
    setArticulo(null);
    setErrorArticulo(null);
    setLeyendo(true);
    try {
      const a = await leerArticuloSoporte(r.fuente, r.articulo_id, r.idioma ?? idiomaSel);
      if (mio !== turnoLectura.current) return;
      setArticulo(a);
    } catch (err) {
      if (mio !== turnoLectura.current) return;
      setErrorArticulo(extractErrorMessage(err, "No se ha podido leer el artículo."));
    } finally {
      if (mio === turnoLectura.current) setLeyendo(false);
    }
  }

  const resultados = busqueda?.resultados ?? [];
  const vacia = busqueda !== null && resultados.length === 0;
  // Sin resultados, el aviso «No se ha encontrado nada…» del servidor repite
  // lo que ya dice la línea de abajo: se quita para no decirlo dos veces.
  const avisos = (busqueda?.avisos ?? []).filter(
    (a) => !(vacia && a.startsWith("No se ha encontrado nada")),
  );
  // Solo se puede decir «no hay procedimiento» si se ha mirado en algún sitio.
  const sePuedeDecirQueNoHay = vacia && (busqueda?.fuentes.length ?? 0) > 0;
  const opcionesIdioma = IDIOMAS.some((i) => i.codigo === idiomaSel)
    ? IDIOMAS : [...IDIOMAS, { codigo: idiomaSel, nombre: idiomaSel }];

  return (
    <div className="soporte-busqueda" aria-busy={buscando}>
      <form className="soporte-busqueda-form" role="search" aria-label="Buscar en el soporte de los fabricantes"
        onSubmit={enviar}>
        <input
          type="search"
          aria-label="Qué buscar en el soporte"
          placeholder="Modelo y lo que pasa: «Beambox calibrar cámara»…"
          maxLength={MAX_CONSULTA}
          value={consulta}
          onChange={(e) => setConsulta(e.target.value)}
          autoFocus
        />
        <select aria-label="Idioma de los artículos" value={idiomaSel}
          onChange={(e) => setIdiomaSel(e.target.value)}>
          {opcionesIdioma.map((i) => <option key={i.codigo} value={i.codigo}>{i.nombre}</option>)}
        </select>
        <button type="submit" className="button small" disabled={buscando}>
          <Search size={12} aria-hidden /> {buscando ? "Buscando…" : "Buscar"}
        </button>
      </form>
      {onUsar ? (
        <p className="muted small soporte-busqueda-nota">
          Elige el artículo que explica lo que hay que hacer: el borrador lo cita con su enlace y
          no se inventa los pasos (las referencias y los números de pieza van tal cual).
        </p>
      ) : null}
      {error ? <p className="form-error" role="alert">{error}</p> : null}
      {avisos.length > 0 ? (
        <ul className="soporte-avisos" aria-label="Avisos de la búsqueda">
          {avisos.map((a) => <li key={a}>{a}</li>)}
        </ul>
      ) : null}
      {vacia && !buscando ? (
        <div className="soporte-vacio">
          <p role="status"><strong>No hay ningún artículo para «{busqueda.consulta}».</strong></p>
          {onSinResultados && sePuedeDecirQueNoHay ? (
            <>
              <p className="muted small">
                Si hay que contestar igualmente, el borrador dirá que no hay un procedimiento
                publicado para esto y no se inventará ninguno.
              </p>
              <div className="soporte-vacio-acciones">
                <button type="button" className="button secondary small"
                  onClick={() => onSinResultados(busqueda.consulta)}>
                  Escribir diciendo que no hay procedimiento
                </button>
              </div>
            </>
          ) : (
            <p className="muted small">Prueba con otras palabras, con el modelo de la máquina o en otro idioma.</p>
          )}
        </div>
      ) : null}
      {resultados.length > 0 ? (
        <ul className="soporte-resultados" aria-label="Artículos de soporte">
          {resultados.map((r) => {
            const clave = claveDe(r);
            const esteAbierto = abierto === clave;
            return (
              <li key={clave} className={`soporte-resultado${esteAbierto ? " is-abierto" : ""}`}>
                <div className="soporte-resultado-cabeza">
                  <p className="soporte-resultado-titulo">
                    {esWeb(r.url) ? (
                      <a href={r.url} target="_blank" rel="noreferrer">
                        {r.titulo || r.url}
                        <ExternalLink size={12} aria-hidden className="soporte-icono-enlace" />
                      </a>
                    ) : <strong>{r.titulo}</strong>}
                  </p>
                  <p className="soporte-resultado-meta">
                    {r.fuente_nombre || r.fuente}
                    {r.idioma ? ` · ${r.idioma.toUpperCase()}` : ""}
                  </p>
                </div>
                {r.extracto ? <p className="soporte-resultado-extracto">{r.extracto}</p> : null}
                <div className="soporte-resultado-acciones">
                  <button type="button" className="button ghost small" aria-expanded={esteAbierto}
                    onClick={() => void leer(r)}>
                    <BookOpen size={12} aria-hidden /> {esteAbierto ? "Cerrar el artículo" : "Leer"}
                  </button>
                  {onUsar ? (
                    <button type="button" className="button secondary small" onClick={() => onUsar(r)}
                      title="El borrador citará este artículo con su enlace">
                      Usar en el borrador
                    </button>
                  ) : null}
                </div>
                {esteAbierto ? (
                  <ArticuloLeido articulo={articulo} leyendo={leyendo} error={errorArticulo}
                    titulo={r.titulo} />
                ) : null}
              </li>
            );
          })}
        </ul>
      ) : null}
    </div>
  );
}

function ArticuloLeido({
  articulo, leyendo, error, titulo,
}: { articulo: SoporteArticulo | null; leyendo: boolean; error: string | null; titulo: string }) {
  if (leyendo) return <p className="muted small soporte-articulo-cargando">Leyendo el artículo…</p>;
  if (error) return <p className="form-error" role="alert">{error}</p>;
  if (!articulo) return null;
  const actualizado = fechaCorta(articulo.actualizado);
  const parrafos = articulo.texto.split(/\n{2,}/).map((p) => p.trim()).filter(Boolean);
  return (
    <section className="soporte-articulo" aria-label={`Artículo: ${articulo.titulo || titulo}`}>
      <p className="soporte-articulo-meta">
        {articulo.fuente_nombre}
        {articulo.idioma ? ` · ${articulo.idioma.toUpperCase()}` : ""}
        {actualizado ? ` · actualizado el ${actualizado}` : ""}
        {esWeb(articulo.url) ? (
          <>
            {" · "}
            <a href={articulo.url} target="_blank" rel="noreferrer">Abrir en su web</a>
          </>
        ) : null}
      </p>
      {articulo.html.trim() ? (
        <iframe
          className="soporte-articulo-marco"
          title={`Artículo: ${articulo.titulo || titulo}`}
          sandbox=""
          srcDoc={documentoArticulo(articulo.html)}
        />
      ) : parrafos.length > 0 ? (
        <div className="soporte-articulo-texto">
          {parrafos.map((p, i) => <p key={i}>{p}</p>)}
        </div>
      ) : (
        <p className="muted small">El artículo no trae texto: ábrelo en su web.</p>
      )}
    </section>
  );
}
