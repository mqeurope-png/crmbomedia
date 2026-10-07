"use client";

import { useEffect, useState } from "react";
import { previewForm, type FormAppearance, type FormField } from "../../lib/formsApi";
import { WebFormPreview } from "./WebFormPreview";

type Props = {
  name: string;
  language: string;
  fields: FormField[];
  appearance: FormAppearance | null | undefined;
};

const ANCHO_MOVIL = 375;

/** Vista previa del formulario tal y como saldrá (la pinta el servidor con
 *  el mismo código que la web), con lo que hay en el editor y sin guardar.
 *  Se actualiza al cambiar campos, ancho, alineación o estilo. Si el
 *  servidor no responde, queda la vista simple de siempre. */
export function WebFormLivePreview({ name, language, fields, appearance }: Props) {
  const [html, setHtml] = useState<string | null>(null);
  const [movil, setMovil] = useState(false);
  const clave = JSON.stringify({ name, language, fields, appearance });

  useEffect(() => {
    let vivo = true;
    const t = setTimeout(() => {
      Promise.resolve()
        .then(() => previewForm({ name, language, fields, appearance }))
        .then((r) => { if (vivo) setHtml(typeof r?.html === "string" ? r.html : null); })
        .catch(() => { if (vivo) setHtml(null); });
    }, 400);
    return () => { vivo = false; clearTimeout(t); };
    // `clave` resume todo lo que pinta la vista previa.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clave]);

  return (
    <div className="wf-live-preview">
      <div className="wf-live-preview-head">
        <h2>Vista previa</h2>
        <div className="wf-live-preview-toggle" role="group" aria-label="Tamaño de pantalla">
          <button type="button" className={`button small ${movil ? "secondary" : ""}`}
            aria-pressed={!movil} onClick={() => setMovil(false)}>Escritorio</button>
          <button type="button" className={`button small ${movil ? "" : "secondary"}`}
            aria-pressed={movil} onClick={() => setMovil(true)}>Móvil</button>
        </div>
      </div>
      {html ? (
        <iframe
          className="wf-live-preview-frame"
          title="Vista previa del formulario"
          srcDoc={html}
          sandbox=""
          style={movil ? { width: ANCHO_MOVIL } : undefined}
        />
      ) : (
        <WebFormPreview name={name} fields={fields} />
      )}
    </div>
  );
}
