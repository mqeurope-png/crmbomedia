"use client";

import { useState } from "react";
import { linkCompanyFactusol } from "../../lib/erpApi";
import { extractErrorMessage } from "../../lib/errors";

const MATCH_LABEL: Record<string, string> = {
  already_linked: "ya estaba vinculada",
  existing_cif: "vinculada por CIF a un cliente existente",
  created_new: "cliente creado en FACTUSOL",
};

/** Fase C · C-2 — «Vincular a FACTUSOL» en la ficha de empresa. Si ya está
 *  vinculada muestra el badge con su CODCLI; si no, el botón + confirmación. */
export function LinkFactusolButton({
  companyId,
  factusolCompanyId,
  onLinked,
}: {
  companyId: string;
  factusolCompanyId: string | null;
  onLinked?: (codcli: string) => void;
}) {
  const [codcli, setCodcli] = useState<string | null>(factusolCompanyId);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (codcli || factusolCompanyId) {
    return (
      <span className="badge ok" aria-label="Vinculada a FACTUSOL">
        Vinculada a FACTUSOL · CODCLI {codcli || factusolCompanyId}
      </span>
    );
  }

  async function doLink() {
    setBusy(true);
    setError(null);
    try {
      const r = await linkCompanyFactusol(companyId);
      setCodcli(r.factusol_codcli);
      setConfirming(false);
      setMessage(`CODCLI ${r.factusol_codcli} — ${MATCH_LABEL[r.matched_by] ?? r.matched_by}.`);
      onLinked?.(r.factusol_codcli);
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo vincular a FACTUSOL."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <button type="button" className="button small secondary"
              onClick={() => setConfirming(true)}>
        Vincular a FACTUSOL
      </button>
      {message ? <span className="muted small">{message}</span> : null}
      {error ? <span className="form-error">{error}</span> : null}
      {confirming ? (
        <div className="modal-overlay" role="dialog" aria-modal="true"
             aria-label="Vincular empresa a FACTUSOL">
          <div className="modal-dialog">
            <h2>Vincular a FACTUSOL</h2>
            <p className="muted small">
              Buscará esta empresa por CIF en FACTUSOL; si existe la vincula, si
              no la crea como cliente nuevo.
            </p>
            <div className="modal-actions">
              <button type="button" className="button secondary"
                      onClick={() => setConfirming(false)} disabled={busy}>
                Cancelar
              </button>
              <button type="button" className="button" onClick={doLink} disabled={busy}>
                {busy ? "Vinculando…" : "Vincular"}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}
