"use client";

import {
  ArrowDown,
  ArrowUp,
  ClipboardCopy,
  CopyPlus,
  Download,
  FileText,
  Monitor,
  Smartphone,
  Trash2,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import {
  type CorreoGenerado,
  type EstructuraCompuesta,
  aplicarEdicion,
  duplicarBloque,
  etiquetaBloque,
  moverBloque,
  quitarBloque,
  textoDeCampo,
  textoRicoDe,
} from "../../lib/correoCompuesto";
import { renderCorreoCompuesto } from "../../lib/emailsApi";
import { extractErrorMessage } from "../../lib/errors";

/** La vista previa de un correo compuesto, dentro del compositor: el correo
 *  tal cual lo genera BoHub (a 600 px, en escritorio o en móvil), con los
 *  textos editables pulsando encima y los controles del bloque elegido
 *  (subir, bajar, duplicar, quitar). Editar un texto cambia ESE campo de la
 *  estructura y nada más; la vista no se vuelve a generar mientras se escribe
 *  (se perdería el cursor), sí al mover, duplicar o quitar un bloque, al
 *  cambiar la firma o al llegar otro turno de la IA (`version`). «Copiar
 *  HTML» y «Descargar .html» piden al servidor el correo final: el mismo que
 *  se ve, sin los atributos de edición. */
export type CorreoCompuestoVistaProps = {
  estructura: EstructuraCompuesta;
  /** Sube cuando la estructura cambia por fuera (un turno de la IA, un borrador
   *  reabierto): la vista se vuelve a generar. */
  version: number;
  asunto: string;
  firmaHtml: string | null;
  bloqueada?: boolean;
  /** Un texto editado en la vista: la estructura con ese campo cambiado. */
  onEditar: (estructura: EstructuraCompuesta) => void;
  /** Un bloque movido, duplicado o quitado: la estructura nueva (el
   *  compositor sube `version` y la vista se vuelve a generar). */
  onCambiar: (estructura: EstructuraCompuesta) => void;
  /** «Pasar a texto»: el texto plano del correo, para el editor normal. */
  onPasarATexto?: (texto: string) => void;
  /** Cada vez que la vista se genera: el correo y sus avisos. */
  onGenerado?: (correo: CorreoGenerado) => void;
};

/** Ancho del marco en escritorio: más de 600 px, para que la consulta de medio
 *  del correo (las columnas que se apilan) no salte. En móvil, un teléfono. */
const ANCHO_ESCRITORIO = 640;
const ANCHO_MOVIL = 375;

/** Lo que la vista previa añade al documento para editar (solo aquí: ni la
 *  estructura ni el correo final lo llevan). */
const ESTILO_EDICION = `
[data-bh-bloque] { cursor: pointer; }
[data-bh-bloque]:hover > td { box-shadow: inset 0 0 0 1px #93c5fd; }
[data-bh-bloque].bh-elegido > td { box-shadow: inset 0 0 0 2px #2563eb; }
[data-bh-campo] { cursor: text; border-radius: 2px; }
[data-bh-campo]:hover { outline: 1px dashed #94a3b8; outline-offset: 2px; }
[data-bh-campo]:focus { outline: 2px solid #f59e0b; outline-offset: 2px; }
a { cursor: text; }
`;

function nombreDeFichero(asunto: string): string {
  const base = asunto
    .normalize("NFD").replace(/[̀-ͯ]/g, "")
    .replace(/^(re|fwd?):\s*/i, "")
    .toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "")
    .slice(0, 60);
  return `${base || "correo"}.html`;
}

async function copiarAlPortapapeles(texto: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(texto);
    return;
  }
  const area = document.createElement("textarea");
  area.value = texto;
  document.body.appendChild(area);
  area.select();
  document.execCommand("copy");
  area.remove();
}

export function CorreoCompuestoVista({
  estructura,
  version,
  asunto,
  firmaHtml,
  bloqueada = false,
  onEditar,
  onCambiar,
  onPasarATexto,
  onGenerado,
}: CorreoCompuestoVistaProps) {
  const [dispositivo, setDispositivo] = useState<"escritorio" | "movil">("escritorio");
  const [html, setHtml] = useState<string | null>(null);
  const [avisos, setAvisos] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [elegido, setElegido] = useState<string | null>(null);
  const [ocupado, setOcupado] = useState(false);
  const [hecho, setHecho] = useState<string | null>(null);
  const marcoRef = useRef<HTMLIFrameElement>(null);
  // Lo último de cada cosa, para los manejadores del documento de la vista.
  const estructuraRef = useRef(estructura);
  const asuntoRef = useRef(asunto);
  const onEditarRef = useRef(onEditar);
  const onGeneradoRef = useRef(onGenerado);
  const bloqueadaRef = useRef(bloqueada);
  useEffect(() => {
    estructuraRef.current = estructura;
    asuntoRef.current = asunto;
    onEditarRef.current = onEditar;
    onGeneradoRef.current = onGenerado;
    bloqueadaRef.current = bloqueada;
  });

  // La vista se genera con la estructura de ese momento: al empezar, al
  // cambiar `version` (bloques movidos, otro turno) o la firma.
  useEffect(() => {
    let vivo = true;
    renderCorreoCompuesto({
      estructura: estructuraRef.current, asunto: asuntoRef.current, firma_html: firmaHtml,
      preview: true,
    })
      .then((correo) => {
        if (!vivo) return;
        setHtml(correo.html);
        setAvisos(correo.avisos);
        setError(null);
        onGeneradoRef.current?.(correo);
      })
      .catch((err) => {
        if (vivo) setError(extractErrorMessage(err, "No se ha podido generar la vista previa."));
      });
    return () => { vivo = false; };
  }, [version, firmaHtml]);

  // El documento de la vista: se escribe en el marco (sin scripts: el marco
  // no los ejecuta) y se le ponen los textos editables y los manejadores.
  useEffect(() => {
    const doc = marcoRef.current?.contentDocument;
    if (!doc || html === null) return;
    doc.open();
    doc.write(html);
    doc.close();
    const estilo = doc.createElement("style");
    estilo.textContent = ESTILO_EDICION;
    doc.head?.appendChild(estilo);
    doc.querySelectorAll<HTMLElement>("[data-bh-campo]").forEach((el) => {
      el.setAttribute("contenteditable", bloqueadaRef.current ? "false" : "true");
      el.setAttribute("spellcheck", "true");
    });

    // Lo último leído de cada campo (al entrar en él, y tras cada cambio):
    // entrar y salir sin escribir no es una edición.
    const leido = new WeakMap<HTMLElement, string>();
    const valorDe = (el: HTMLElement) => (
      (el.getAttribute("data-bh-campo") ?? "").endsWith(":html") ? textoRicoDe(el) : textoDeCampo(el));
    const leer = (el: HTMLElement) => {
      const valor = valorDe(el);
      if (leido.get(el) === valor) return;
      leido.set(el, valor);
      const nueva = aplicarEdicion(estructuraRef.current, el.getAttribute("data-bh-campo") ?? "", valor);
      if (nueva !== estructuraRef.current) {
        estructuraRef.current = nueva;
        onEditarRef.current(nueva);
      }
    };
    // Los nodos del marco son de OTRA ventana: nada de `instanceof HTMLElement`.
    const campoDe = (objetivo: EventTarget | null): HTMLElement | null => {
      const el = objetivo as HTMLElement | null;
      return typeof el?.closest === "function" ? el.closest<HTMLElement>("[data-bh-campo]") : null;
    };
    const alPulsar = (ev: MouseEvent) => {
      const objetivo = ev.target as HTMLElement | null;
      // Un enlace de la vista no navega: se edita su texto.
      if (objetivo?.closest?.("a")) ev.preventDefault();
      const bloque = objetivo?.closest?.("[data-bh-bloque]");
      setElegido(bloque ? bloque.getAttribute("data-bh-bloque") : null);
    };
    const alEntrar = (ev: Event) => {
      const el = campoDe(ev.target);
      if (el && !leido.has(el)) leido.set(el, valorDe(el));
    };
    const alEscribir = (ev: Event) => {
      const el = campoDe(ev.target);
      if (el) leer(el);
    };
    const alSalir = (ev: Event) => {
      const el = campoDe(ev.target);
      if (!el) return;
      leer(el);
      // El mismo campo en varias fichas (el botón de una fila): todas iguales.
      const campo = el.getAttribute("data-bh-campo");
      doc.querySelectorAll<HTMLElement>(`[data-bh-campo="${campo}"]`).forEach((otro) => {
        if (otro !== el) otro.textContent = el.textContent;
      });
    };
    const alTeclear = (ev: KeyboardEvent) => {
      const el = campoDe(ev.target);
      if (!el || ev.key !== "Enter") return;
      const campo = el.getAttribute("data-bh-campo") ?? "";
      if (campo.endsWith(":html")) return;               // párrafos: el navegador
      ev.preventDefault();
      // Un botón es una línea; un título o un texto corto admiten salto.
      if (!/:(boton|botones\.\d+\.texto)$/.test(campo) && typeof doc.execCommand === "function") {
        doc.execCommand("insertLineBreak");
      }
    };
    const alPegar = (ev: ClipboardEvent) => {
      // Se pega texto, nunca HTML de fuera.
      if (!campoDe(ev.target)) return;
      ev.preventDefault();
      const texto = ev.clipboardData?.getData("text/plain") ?? "";
      if (typeof doc.execCommand === "function") doc.execCommand("insertText", false, texto);
    };
    doc.addEventListener("click", alPulsar);
    doc.addEventListener("focusin", alEntrar);
    doc.addEventListener("input", alEscribir);
    doc.addEventListener("focusout", alSalir);
    doc.addEventListener("keydown", alTeclear);
    doc.addEventListener("paste", alPegar);
    return () => {
      doc.removeEventListener("click", alPulsar);
      doc.removeEventListener("focusin", alEntrar);
      doc.removeEventListener("input", alEscribir);
      doc.removeEventListener("focusout", alSalir);
      doc.removeEventListener("keydown", alTeclear);
      doc.removeEventListener("paste", alPegar);
    };
  }, [html]);

  // Mientras se envía, nada se edita.
  useEffect(() => {
    const doc = marcoRef.current?.contentDocument;
    doc?.querySelectorAll<HTMLElement>("[data-bh-campo]").forEach((el) => {
      el.setAttribute("contenteditable", bloqueada ? "false" : "true");
    });
  }, [bloqueada, html]);

  // El bloque elegido, marcado en la vista.
  useEffect(() => {
    const doc = marcoRef.current?.contentDocument;
    doc?.querySelectorAll("[data-bh-bloque]").forEach((el) => {
      el.classList.toggle("bh-elegido", el.getAttribute("data-bh-bloque") === elegido);
    });
  }, [elegido, html]);

  const bloques = estructura.bloques;
  const indice = elegido ? bloques.findIndex((b) => b.id === elegido) : -1;
  const actual = indice >= 0 ? bloques[indice] : null;

  // El correo final (el que se envía): con la firma para copiarlo o
  // descargarlo; sin ella para pasarlo a texto (el editor pone la suya).
  async function correoFinal(conFirma: boolean): Promise<CorreoGenerado> {
    return renderCorreoCompuesto({
      estructura: estructuraRef.current, asunto: asuntoRef.current,
      firma_html: conFirma ? firmaHtml : null,
    });
  }

  async function conFinal(
    accion: (c: CorreoGenerado) => Promise<void> | void, listo: string, conFirma = true,
  ) {
    if (ocupado) return;
    setOcupado(true);
    setError(null);
    try {
      await accion(await correoFinal(conFirma));
      setHecho(listo);
      window.setTimeout(() => setHecho(null), 2500);
    } catch (err) {
      setError(extractErrorMessage(err, "No se ha podido generar el correo."));
    } finally {
      setOcupado(false);
    }
  }

  const copiar = () => conFinal((c) => copiarAlPortapapeles(c.html), "HTML copiado");
  const descargar = () => conFinal((c) => {
    const url = URL.createObjectURL(new Blob([c.html], { type: "text/html;charset=utf-8" }));
    const enlace = document.createElement("a");
    enlace.href = url;
    enlace.download = nombreDeFichero(asuntoRef.current);
    document.body.appendChild(enlace);
    enlace.click();
    enlace.remove();
    URL.revokeObjectURL(url);
  }, "Descargado");
  const pasarATexto = () => conFinal((c) => onPasarATexto?.(c.texto), "", false);

  function cambiar(nueva: EstructuraCompuesta, siguiente: string | null = elegido) {
    estructuraRef.current = nueva;
    setElegido(siguiente);
    onCambiar(nueva);
  }

  return (
    <section className="correo-compuesto-vista" aria-label="Vista previa del correo compuesto">
      <div className="correo-compuesto-barra" role="toolbar" aria-label="Vista previa">
        <span className="correo-compuesto-dispositivos" role="group" aria-label="Dispositivo">
          <button
            type="button"
            className={`icon-button${dispositivo === "escritorio" ? " is-activo" : ""}`}
            aria-pressed={dispositivo === "escritorio"}
            title="Cómo se ve en un ordenador (600 px)"
            onClick={() => setDispositivo("escritorio")}
          >
            <Monitor size={14} aria-hidden /> Escritorio
          </button>
          <button
            type="button"
            className={`icon-button${dispositivo === "movil" ? " is-activo" : ""}`}
            aria-pressed={dispositivo === "movil"}
            title="Cómo se ve en un móvil: las fichas se apilan"
            onClick={() => setDispositivo("movil")}
          >
            <Smartphone size={14} aria-hidden /> Móvil
          </button>
        </span>
        <span className="correo-compuesto-bloque" role="group" aria-label="Bloque elegido">
          <select
            aria-label="Bloque"
            value={elegido ?? ""}
            onChange={(e) => setElegido(e.target.value || null)}
            title="Pulsa un bloque en la vista previa, o elígelo aquí"
          >
            <option value="">Pulsa un bloque para moverlo o quitarlo…</option>
            {bloques.map((b, i) => (
              <option key={b.id} value={b.id}>{`${i + 1}. ${etiquetaBloque(b, estructura.idioma)}`}</option>
            ))}
          </select>
          <button
            type="button" className="icon-button" aria-label="Subir el bloque" title="Subir"
            disabled={bloqueada || !actual || indice === 0}
            onClick={() => actual && cambiar(moverBloque(estructura, actual.id, -1))}
          >
            <ArrowUp size={14} aria-hidden />
          </button>
          <button
            type="button" className="icon-button" aria-label="Bajar el bloque" title="Bajar"
            disabled={bloqueada || !actual || indice === bloques.length - 1}
            onClick={() => actual && cambiar(moverBloque(estructura, actual.id, 1))}
          >
            <ArrowDown size={14} aria-hidden />
          </button>
          <button
            type="button" className="icon-button" aria-label="Duplicar el bloque" title="Duplicar"
            disabled={bloqueada || !actual}
            onClick={() => {
              if (!actual) return;
              const { estructura: nueva, id } = duplicarBloque(estructura, actual.id);
              cambiar(nueva, id);
            }}
          >
            <CopyPlus size={14} aria-hidden />
          </button>
          <button
            type="button" className="icon-button" aria-label="Quitar el bloque" title="Quitar"
            disabled={bloqueada || !actual}
            onClick={() => actual && cambiar(quitarBloque(estructura, actual.id), null)}
          >
            <Trash2 size={14} aria-hidden />
          </button>
        </span>
        <span className="correo-compuesto-salidas" role="group" aria-label="El correo">
          <button
            type="button" className="button secondary small" onClick={() => void copiar()}
            disabled={ocupado || bloques.length === 0}
            title="Copia el HTML del correo (el documento entero, autocontenido)"
          >
            <ClipboardCopy size={12} aria-hidden /> Copiar HTML
          </button>
          <button
            type="button" className="button secondary small" onClick={() => void descargar()}
            disabled={ocupado || bloques.length === 0}
            title="Descarga el correo como fichero .html"
          >
            <Download size={12} aria-hidden /> Descargar .html
          </button>
          {onPasarATexto ? (
            <button
              type="button" className="button secondary small" onClick={() => void pasarATexto()}
              disabled={ocupado || bloqueada}
              title="Pasa el correo a texto normal, en el editor (sin fichas, vídeo ni botones)"
            >
              <FileText size={12} aria-hidden /> Pasar a texto
            </button>
          ) : null}
          {hecho ? <span className="muted small" role="status">{hecho}</span> : null}
        </span>
      </div>
      {error ? <p className="form-error correo-compuesto-error" role="alert">{error}</p> : null}
      {avisos.length > 0 ? (
        <p className="correo-compuesto-avisos" role="status">{avisos.join(" ")}</p>
      ) : null}
      {bloques.length === 0 ? (
        <p className="correo-compuesto-avisos" role="status">
          El correo se ha quedado sin bloques: pide otro a la IA o pásalo a texto.
        </p>
      ) : null}
      <div className={`correo-compuesto-marco is-${dispositivo}`}>
        {html === null && !error ? (
          <p className="muted small correo-compuesto-cargando">Generando la vista previa…</p>
        ) : null}
        <iframe
          ref={marcoRef}
          title="Vista previa del correo"
          sandbox="allow-same-origin"
          style={{
            width: `${dispositivo === "movil" ? ANCHO_MOVIL : ANCHO_ESCRITORIO}px`,
            visibility: html === null ? "hidden" : "visible",
          }}
        />
      </div>
      <p className="muted small correo-compuesto-ayuda">
        Pulsa un texto para cambiarlo. Los precios, las fichas y los enlaces salen del catálogo:
        para cambiarlos, pídeselo a la IA o quita el bloque.
      </p>
    </section>
  );
}
