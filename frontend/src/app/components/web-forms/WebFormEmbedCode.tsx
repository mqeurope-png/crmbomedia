"use client";

import { Check, Copy } from "lucide-react";
import { useState } from "react";
import type { EmbedCode } from "../../lib/formsApi";

/** Muestra los 2 snippets de embed (script JS + iframe) con botón copiar. */
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
    </div>
  );
}

function Snippet({
  title,
  description,
  code,
}: {
  title: string;
  description: string;
  code: string;
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
    </div>
  );
}
