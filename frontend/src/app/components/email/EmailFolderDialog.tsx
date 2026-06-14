"use client";

import { useEffect, useState } from "react";
import { Modal } from "../Modal";
import {
  type EmailFolder,
  createEmailFolder,
  updateEmailFolder,
} from "../../lib/emailsApi";
import { extractErrorMessage } from "../../lib/errors";

type Props = {
  open: boolean;
  folder: EmailFolder | null;
  folders: EmailFolder[];
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

export function EmailFolderDialog({
  open,
  folder,
  folders,
  onClose,
  onSaved,
}: Props) {
  const [name, setName] = useState("");
  const [color, setColor] = useState<string | null>(null);
  const [parentId, setParentId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setName(folder?.name ?? "");
      setColor(folder?.color ?? null);
      setParentId(folder?.parent_id ?? null);
      setError(null);
    }
  }, [open, folder]);

  const parentOptions = folders.filter(
    (f) => f.id !== folder?.id && !f.is_system,
  );

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const payload = {
        name: name.trim(),
        parent_id: parentId,
        color,
      };
      if (folder) await updateEmailFolder(folder.id, payload);
      else await createEmailFolder(payload);
      onSaved();
      onClose();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo guardar la carpeta."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={folder ? "Editar carpeta" : "Nueva carpeta"}
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
            maxLength={120}
            autoFocus
          />
        </label>
        <label>
          Carpeta padre
          <select
            value={parentId ?? ""}
            onChange={(e) => setParentId(e.target.value || null)}
          >
            <option value="">— Ninguna —</option>
            {parentOptions.map((f) => (
              <option key={f.id} value={f.id}>
                {f.name}
              </option>
            ))}
          </select>
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
