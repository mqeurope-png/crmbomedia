"use client";

import { Check, Copy } from "lucide-react";
import { useState, type ReactNode } from "react";
import type { EmbedCode } from "../../lib/formsApi";

/** Muestra los 3 snippets de embed (script JS + iframe + HTML puro) con
 *  botón copiar. El HTML puro incluye además una preview aislada. */
export function WebFormEmbedCode({ embed }: { embed: EmbedCode }) {
  return (
    <div className="wf-embed">
      <Snippet
        title="Script JS (recomendado)"
        description="Hereda el diseño de tu web. Pega esto donde quieras el formulario."
        code={embed.script_snippet}
      />
      <Snippet
        title="iframe (aislado)"
        description="Diseño propio BoHub, aislado de la web. Útil si no puedes tocar el CSS."
        code={embed.iframe_snippet}
      />
      <Snippet
        title="HTML puro"
        description={
          "Pega este HTML directamente en tu web. Requiere el snippet reCAPTCHA " +
          "incluido. Estila libremente con tu CSS usando las clases .bh-form, " +
          ".bh-field, .bh-label, .bh-input, .bh-button."
        }
        code={embed.html_snippet}
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
}: {
  title: string;
  description: string;
  code: string;
  children?: ReactNode;
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
      <pre className="wf-embed-code">
        <code>{code}</code>
      </pre>
      {children}
    </div>
  );
}
