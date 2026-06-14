"use client";

import { useEffect, useState } from "react";
import { Modal } from "../Modal";
import {
  type EmailLabel,
  createEmailLabel,
  updateEmailLabel,
} from "../../lib/emailsApi";
import { extractErrorMessage } from "../../lib/errors";

type Props = {
  open: boolean;
  label: EmailLabel | null;
  onClose: () => void;
  onSaved: () => void;
};

const PRESET_COLORS = [
  "#3366ff",
  "#06b6d4",
  "#22c55e",
  "#facc15",
  "#f97316",
  "#ef4444",
  "#a855f7",
  "#64748b",
];

export function EmailLabelDialog({ open, label, onClose, onSaved }: Props) {
  const [name, setName] = useState("");
  const [color, setColor] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setName(label?.name ?? "");
      setColor(label?.color ?? null);
      setError(null);
    }
  }, [open, label]);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const payload = { name: name.trim(), color };
      if (label) await updateEmailLabel(label.id, payload);
      else await createEmailLabel(payload);
      onSaved();
      onClose();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo guardar la etiqueta."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={label ? "Editar etiqueta" : "Nueva etiqueta"}
      size="small"
    >
      <form onSubmit={onSubmit} className="form-stack">
        <label>
          Nombre
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
            maxLength={80}
            autoFocus
          />
        </label>
        <fieldset className="color-picker">
          <legend>Color</legend>
          <div className="color-swatches">
            <button
              type="button"
              className={`color-swatch${color === null ? " is-active" : ""}`}
              style={{ backgroundColor: "transparent", borderStyle: "dashed" }}
              onClick={() => setColor(null)}
              aria-label="Sin color"
            />
            {PRESET_COLORS.map((c) => (
              <button
                key={c}
                type="button"
                className={`color-swatch${color === c ? " is-active" : ""}`}
                style={{ backgroundColor: c }}
                onClick={() => setColor(c)}
                aria-label={`Color ${c}`}
              />
            ))}
          </div>
        </fieldset>
        {error ? <p className="form-error">{error}</p> : null}
        <div className="form-actions">
          <button type="button" className="btn" onClick={onClose} disabled={busy}>
            Cancelar
          </button>
          <button type="submit" className="btn btn-primary" disabled={busy}>
            {busy ? "Guardando…" : "Guardar"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
