"use client";

import { useState } from "react";
import { extractErrorMessage } from "../../lib/errors";
import {
  downloadOrderFactusolAlbaranPdf,
  saveBlob,
  type FactusolPdfLang,
} from "../../lib/erpApi";

/** «PDF del albarán (FACTUSOL)» — descarga el albarán que BoHub creó en
 *  FACTUSOL para el pedido (`factusol_albaran_number`). Mismo motor y mismo
 *  selector de idioma que «PDF del pedido (FACTUSOL)». Solo tiene sentido
 *  cuando hay nº de albarán: el padre decide si lo muestra. El error (albarán
 *  ya no existe en FACTUSOL, DELSOL caído) se enseña sin romper la ficha. */
export function FactusolAlbaranPdfButton({
  orderId,
  numero,
  lang = "es",
  className = "button small secondary",
  onError,
}: {
  orderId: string;
  numero: string;
  lang?: FactusolPdfLang;
  className?: string;
  /** Si el padre lo pasa, el error va a su línea de error; si no, inline. */
  onError?: (msg: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function download() {
    setBusy(true);
    setError(null);
    try {
      const blob = await downloadOrderFactusolAlbaranPdf(orderId, lang);
      saveBlob(blob, `Albaran_${numero}.pdf`);
    } catch (e) {
      const msg = extractErrorMessage(e, "No se pudo generar el PDF del albarán de FACTUSOL.");
      if (onError) onError(msg);
      else setError(msg);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <button
        type="button"
        className={className}
        disabled={busy}
        title={`Descarga el albarán ${numero} de FACTUSOL en PDF (lo compone BoHub, solo lectura)`}
        onClick={() => void download()}
      >
        {busy ? "Generando…" : "PDF del albarán (FACTUSOL)"}
      </button>
      {error ? <p className="form-error" role="alert">{error}</p> : null}
    </>
  );
}
