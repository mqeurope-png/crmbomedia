"use client";

import { Check, Copy } from "lucide-react";
import { useState, type ReactNode } from "react";
import type { EmbedCode } from "../../lib/formsApi";

/** Muestra los 3 snippets de embed (script JS + iframe + HTML puro) con
 *  botón copiar. El HTML puro incluye además una preview aislada. */
export function WebFormEmbedCode({ embed }: { embed: EmbedCode }) {
  const inactivo = embed.is_active === false;
  return (
    <div className="wf-embed">
      {inactivo ? (
        <p className="form-warning" role="alert">
          Este formulario está <strong>desactivado</strong>: puedes copiar el código, pero no
          se verá en la web hasta activarlo (Editar formulario → «Activo»).
        </p>
      ) : null}
      {embed.brand_snippet ? (
        <Snippet
          title={`Un solo código para toda la web (${embed.brand})`}
          description={
            "Recomendado en webs con varios idiomas: el mismo código vale en todas " +
            "las traducciones. Mira el idioma de la página (el «lang» del <html> o el " +
            "prefijo de la URL) y sirve el formulario de esta marca en ese idioma; si no " +
            "hay uno, el de respaldo. Se puede poner en la plantilla, no página por página. " +
            "Pega LAS DOS LÍNEAS: sin el <div> el script carga y no pinta nada."
          }
          code={embed.brand_snippet}
          inactivo={inactivo}
        />
      ) : null}
      <Snippet
        title="Script JS de este formulario"
        description={
          "Solo este formulario (" + "este idioma)." +
          " Hereda el diseño de tu web. Pega LAS DOS LÍNEAS donde quieras el formulario: " +
          "el <script> y el <div data-bohub-form>; sin el <div> el script carga y no pinta nada."
        }
        code={embed.script_snippet}
        inactivo={inactivo}
      />
      <Snippet
        title="iframe (aislado)"
        description={
          "Diseño propio BoHub, aislado de la web. Útil si no puedes tocar el CSS. " +
          "Crece solo con el contenido (incluye un pequeño script)."
        }
        code={embed.iframe_snippet}
        inactivo={inactivo}
      />
      <Snippet
        title="HTML puro"
        description={
          "Pega este HTML directamente en tu web. Requiere el snippet reCAPTCHA " +
          "incluido. Estila libremente con tu CSS usando las clases .bh-form, " +
          ".bh-field, .bh-label, .bh-input, .bh-button."
        }
        code={embed.html_snippet}
        inactivo={inactivo}
      >
        <div className="wf-embed-preview">
          <span className="muted small">Vista previa (sin estilar):</span>
          <iframe
            className="wf-embed-preview-frame"
            title="Vista previa del HTML puro"
            srcDoc={embed.html_snippet}
            sandbox=""
          />
        </div>
      </Snippet>
    </div>
  );
}

function Snippet({
  title,
  description,
  code,
  children,
  inactivo = false,
}: {
  title: string;
  description: string;
  code: string;
  children?: ReactNode;
  inactivo?: boolean;
}) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard bloqueado — el user puede seleccionar y copiar a mano */
    }
  }

  return (
    <div className="wf-embed-snippet">
      <header className="wf-embed-head">
        <div>
          <h3>{title}</h3>
          <p className="muted small">{description}</p>
        </div>
        <button
          type="button"
          className="button small secondary"
          onClick={copy}
          aria-label={`Copiar ${title}`}
        >
          {copied ? <Check size={13} aria-hidden /> : <Copy size={13} aria-hidden />}
          {copied ? "Copiado" : "Copiar"}
        </button>
      </header>
      {copied && inactivo ? (
        <p className="form-warning small" role="status">
          Copiado, pero no se verá en la web hasta activar el formulario.
        </p>
      ) : null}
      <pre className="wf-embed-code">
        <code>{code}</code>
      </pre>
      {children}
    </div>
  );
}
