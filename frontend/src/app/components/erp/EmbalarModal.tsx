"use client";

import { useState } from "react";
import { extractErrorMessage } from "../../lib/errors";
import { setPackages, transitionPacked, type PackageInput } from "../../lib/erpApi";

type Row = { weight_kg: string; height_cm: string; width_cm: string; depth_cm: string };
const EMPTY: Row = { weight_kg: "", height_cm: "", width_cm: "", depth_cm: "" };

function num(v: string): number | null {
  const n = Number(v);
  return v.trim() !== "" && Number.isFinite(n) ? n : null;
}

/** Modal «Embalado» (Fase D · D-1): multi-bulto obligatorio. Guarda los bultos
 *  y solicita la transición a `packed` (que exige ≥1 bulto medido). */
export function EmbalarModal({
  orderId,
  onDone,
  onCancel,
}: {
  orderId: string;
  onDone: () => void;
  onCancel: () => void;
}) {
  const [rows, setRows] = useState<Row[]>([{ ...EMPTY }]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function update(i: number, key: keyof Row, val: string) {
    setRows((rs) => rs.map((r, j) => (j === i ? { ...r, [key]: val } : r)));
  }
  function addRow() {
    setRows((rs) => [...rs, { ...EMPTY }]);
  }
  function removeRow(i: number) {
    setRows((rs) => rs.filter((_, j) => j !== i));
  }

  const valid =
    rows.length > 0 &&
    rows.every((r) =>
      [r.weight_kg, r.height_cm, r.width_cm, r.depth_cm].every((v) => {
        const n = num(v);
        return n !== null && n > 0;
      }),
    );

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const packages: PackageInput[] = rows.map((r) => ({
        weight_kg: num(r.weight_kg),
        height_cm: num(r.height_cm),
        width_cm: num(r.width_cm),
        depth_cm: num(r.depth_cm),
      }));
      await setPackages(orderId, packages);
      await transitionPacked(orderId);
      onDone();
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo embalar."));
      setBusy(false);
    }
  }

  return (
    <div className="modal-overlay" role="dialog" aria-modal="true"
         aria-label="Embalar pedido (bultos)">
      <div className="modal-dialog">
        <h2>Embalado — bultos</h2>
        <p className="muted small">
          Indica peso y medidas de cada bulto. Todos los valores deben ser &gt; 0.
        </p>

        {rows.map((r, i) => (
          <div key={i} className="erp-bulto-row" aria-label={`Bulto ${i + 1}`}>
            <div className="erp-bulto-head">
              <strong>Bulto {i + 1}</strong>
              {i > 0 ? (
                <button type="button" className="button small secondary"
                        onClick={() => removeRow(i)}>
                  Eliminar
                </button>
              ) : null}
            </div>
            <div className="erp-bulto-fields">
              <label className="field">
                <span>Peso (kg)</span>
                <input type="number" step="0.01" min="0" value={r.weight_kg}
                       aria-label={`Peso bulto ${i + 1}`}
                       onChange={(e) => update(i, "weight_kg", e.target.value)} />
              </label>
              <label className="field">
                <span>Alto (cm)</span>
                <input type="number" min="0" value={r.height_cm}
                       aria-label={`Alto bulto ${i + 1}`}
                       onChange={(e) => update(i, "height_cm", e.target.value)} />
              </label>
              <label className="field">
                <span>Ancho (cm)</span>
                <input type="number" min="0" value={r.width_cm}
                       aria-label={`Ancho bulto ${i + 1}`}
                       onChange={(e) => update(i, "width_cm", e.target.value)} />
              </label>
              <label className="field">
                <span>Fondo (cm)</span>
                <input type="number" min="0" value={r.depth_cm}
                       aria-label={`Fondo bulto ${i + 1}`}
                       onChange={(e) => update(i, "depth_cm", e.target.value)} />
              </label>
            </div>
          </div>
        ))}

        <button type="button" className="button small secondary" onClick={addRow}>
          + Añadir bulto
        </button>

        {error ? <p className="form-error">{error}</p> : null}

        <div className="modal-actions">
          <button type="button" className="button secondary"
                  onClick={onCancel} disabled={busy}>
            Cancelar
          </button>
          <button type="button" className="button"
                  onClick={save} disabled={busy || !valid}>
            {busy ? "Guardando…" : "Guardar y embalar"}
          </button>
        </div>
      </div>
    </div>
  );
}
