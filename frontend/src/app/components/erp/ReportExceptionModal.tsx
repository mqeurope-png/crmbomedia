"use client";

import { useState } from "react";
import { EXCEPTION_CATALOG } from "../../lib/erpApi";

/** Modal «Reportar problema»: selector de tipo + subtipo (si aplica) +
 *  descripción libre. Usa .modal-dialog (caja centrada del CRM). */
export function ReportExceptionModal({
  onSubmit,
  onClose,
  busy,
}: {
  onSubmit: (data: { type: string; subtype?: string; description: string }) => void;
  onClose: () => void;
  busy?: boolean;
}) {
  const [type, setType] = useState(EXCEPTION_CATALOG[0].type);
  const [subtype, setSubtype] = useState("");
  const [description, setDescription] = useState("");

  const selected = EXCEPTION_CATALOG.find((c) => c.type === type);
  const subtypes = selected?.subtypes ?? [];

  function submit() {
    onSubmit({
      type,
      subtype: subtypes.length > 0 ? subtype || subtypes[0].value : undefined,
      description: description.trim(),
    });
  }

  return (
    <div className="modal-overlay" role="dialog" aria-modal="true" aria-label="Reportar problema">
      <div className="modal-dialog">
        <h2>Reportar problema</h2>
        <label className="field">
          <span>Tipo de incidencia</span>
          <select
            value={type}
            aria-label="Tipo de incidencia"
            onChange={(e) => { setType(e.target.value); setSubtype(""); }}
          >
            {EXCEPTION_CATALOG.map((c) => (
              <option key={c.type} value={c.type}>{c.label}</option>
            ))}
          </select>
        </label>
        {subtypes.length > 0 ? (
          <label className="field">
            <span>Detalle</span>
            <select
              value={subtype || subtypes[0].value}
              aria-label="Subtipo"
              onChange={(e) => setSubtype(e.target.value)}
            >
              {subtypes.map((s) => (
                <option key={s.value} value={s.value}>{s.label}</option>
              ))}
            </select>
          </label>
        ) : null}
        <label className="field">
          <span>Descripción</span>
          <textarea
            value={description}
            aria-label="Descripción del problema"
            rows={3}
            placeholder="Qué ha pasado…"
            onChange={(e) => setDescription(e.target.value)}
          />
        </label>
        <div className="modal-actions">
          <button type="button" className="button secondary" onClick={onClose} disabled={busy}>
            Cancelar
          </button>
          <button type="button" className="button danger" onClick={submit} disabled={busy}>
            Reportar y bloquear
          </button>
        </div>
      </div>
    </div>
  );
}
