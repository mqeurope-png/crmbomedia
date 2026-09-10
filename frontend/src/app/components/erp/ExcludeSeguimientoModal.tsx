"use client";

import { useEffect, useState } from "react";
import {
  EXCLUSION_REASON_CODES,
  previewExcludeSeguimiento,
  type ExcludePreviewItem,
  type ExclusionReasonCode,
} from "../../lib/erpApi";

const REASON_LABELS: Record<ExclusionReasonCode, string> = {
  cancelado: "Cancelado",
  duplicado: "Duplicado",
  prueba: "Prueba",
  reembolsado: "Reembolsado",
  otro: "Otro",
};

export type ExcludeTargetRow = {
  id: string;
  order_number: string;
  cliente: string | null;
};

/** Control manual — «Quitar del seguimiento» para una fila o una selección.
 *  Pide un motivo (rápidos + texto libre), consulta qué tiene cada pedido
 *  aguas abajo (factura, cobro, albarán, SAT, Drive…) y AVISA; nunca bloquea:
 *  Bart decide. Reversible con «Reincluir». No borra nada. */
export function ExcludeSeguimientoModal({
  rows,
  onConfirm,
  onCancel,
  busy,
}: {
  rows: ExcludeTargetRow[];
  onConfirm: (reason: string, reasonCode?: ExclusionReasonCode) => void;
  onCancel: () => void;
  busy?: boolean;
}) {
  const [code, setCode] = useState<ExclusionReasonCode | null>(null);
  const [text, setText] = useState("");
  const [preview, setPreview] = useState<ExcludePreviewItem[] | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  // Clave estable: el padre puede crear un array nuevo en cada render.
  const ids = rows.map((r) => r.id).join(",");

  useEffect(() => {
    let alive = true;
    setPreview(null);
    setPreviewError(null);
    previewExcludeSeguimiento(ids.split(",").filter(Boolean))
      .then((p) => { if (alive) setPreview(p.items); })
      .catch(() => {
        if (alive) {
          setPreviewError("No se pudieron comprobar los avisos; puedes quitar igualmente.");
        }
      });
    return () => { alive = false; };
  }, [ids]);

  const title = rows.length === 1
    ? `Quitar ${rows[0].order_number} del seguimiento`
    : `Quitar ${rows.length} pedidos del seguimiento`;
  const warned = (preview ?? []).filter((it) => it.avisos.length > 0);
  const alreadyOut = (preview ?? []).filter((it) => it.excluido).length;
  const loading = preview === null && previewError === null;

  return (
    <div className="modal-overlay" role="dialog" aria-modal="true" aria-label={title}>
      <div className="modal-dialog">
        <h2>{title}</h2>
        <p className="muted small">
          Deja de listarse y de escribirse en la hoja de Drive. No se borra ni
          se cambia nada del pedido, ni en FACTUSOL. Se deshace cuando quieras
          con «Reincluir» (en «Ver excluidos»).
        </p>
        {rows.length > 1 ? (
          <ul className="item-list small">
            {rows.slice(0, 12).map((r) => (
              <li key={r.id}>
                <strong>{r.order_number}</strong>{r.cliente ? ` · ${r.cliente}` : ""}
              </li>
            ))}
            {rows.length > 12 ? <li className="muted">… y {rows.length - 12} más</li> : null}
          </ul>
        ) : null}

        {loading ? <p className="muted small" role="status">Comprobando avisos…</p> : null}
        {previewError ? <p className="muted small" role="status">{previewError}</p> : null}
        {warned.length > 0 ? (
          <div className="form-error" role="alert">
            <strong>Aviso:</strong>{" "}
            {warned.length === 1 && rows.length === 1
              ? "este pedido tiene"
              : `${warned.length} pedido(s) tienen`}{" "}
            algo aguas abajo. Se quita{warned.length === 1 && rows.length === 1 ? "" : "n"}{" "}
            igualmente si confirmas; no se toca nada de eso.
            <ul className="item-list small">
              {warned.map((it) => (
                <li key={it.order_id}>
                  <strong>{it.order_number}</strong>: {it.avisos.join(", ")}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        {alreadyOut > 0 ? (
          <p className="muted small">
            {alreadyOut} ya estaba(n) fuera del seguimiento: se dejan como están.
          </p>
        ) : null}

        <fieldset className="field">
          <legend>Motivo (opcional)</legend>
          <div className="erp-reason-chips" role="group" aria-label="Motivo rápido">
            {EXCLUSION_REASON_CODES.map((c) => (
              <button
                key={c}
                type="button"
                className={`button small ${code === c ? "" : "secondary"}`}
                aria-pressed={code === c}
                disabled={busy}
                onClick={() => setCode(code === c ? null : c)}
              >
                {REASON_LABELS[c]}
              </button>
            ))}
          </div>
          <textarea
            value={text}
            rows={2}
            aria-label="Motivo"
            placeholder="Detalle (opcional): p. ej. «era el 5782», «lo pidió el cliente»…"
            disabled={busy}
            onChange={(e) => setText(e.target.value)}
          />
        </fieldset>

        <div className="modal-actions">
          <button type="button" className="button secondary" onClick={onCancel} disabled={busy}>
            Cancelar
          </button>
          <button
            type="button"
            className="button danger"
            disabled={busy || loading}
            onClick={() => onConfirm(text.trim(), code ?? undefined)}
          >
            {busy
              ? "Quitando…"
              : warned.length > 0
                ? `Quitar igualmente (${rows.length})`
                : `Quitar del seguimiento (${rows.length})`}
          </button>
        </div>
      </div>
    </div>
  );
}
