"use client";

import { useEffect, useState } from "react";

export type ContactViewDraft = {
  name: string;
  description: string;
  isShared: boolean;
};

type Props = {
  open: boolean;
  /** When editing, `initial` carries the row's current values so the
   * form preloads them. When creating, leave undefined. */
  initial?: ContactViewDraft;
  title: string;
  submitLabel: string;
  /** Optional extra controls — `<SavePresetModal>` uses this slot to
   * render a "Save in current view / Save as new" radio. */
  extraControls?: React.ReactNode;
  onSubmit: (draft: ContactViewDraft) => Promise<void> | void;
  onClose: () => void;
};

const EMPTY: ContactViewDraft = { name: "", description: "", isShared: false };

export function ContactViewEditorModal({
  open,
  initial,
  title,
  submitLabel,
  extraControls,
  onSubmit,
  onClose,
}: Props) {
  const [draft, setDraft] = useState<ContactViewDraft>(initial ?? EMPTY);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setDraft(initial ?? EMPTY);
      setError(null);
    }
  }, [open, initial]);

  if (!open) return null;

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!draft.name.trim()) {
      setError("El nombre es obligatorio.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await onSubmit(draft);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "No se pudo guardar la vista.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="modal-overlay" role="dialog" aria-modal>
      <form onSubmit={handleSubmit} className="modal-card">
        <h2>{title}</h2>
        <label>
          <span>Nombre</span>
          <input
            type="text"
            required
            maxLength={100}
            value={draft.name}
            onChange={(event) =>
              setDraft((current) => ({ ...current, name: event.target.value }))
            }
          />
        </label>
        <label>
          <span>Descripción</span>
          <textarea
            rows={3}
            maxLength={2000}
            value={draft.description}
            onChange={(event) =>
              setDraft((current) => ({
                ...current,
                description: event.target.value,
              }))
            }
          />
        </label>
        <label className="checkbox">
          <input
            type="checkbox"
            checked={draft.isShared}
            onChange={(event) =>
              setDraft((current) => ({
                ...current,
                isShared: event.target.checked,
              }))
            }
          />
          <span>Compartir con otros usuarios (read-only)</span>
        </label>
        {extraControls}
        {error ? <p className="danger-text">{error}</p> : null}
        <div className="form-actions">
          <button type="submit" className="button" disabled={submitting}>
            {submitting ? "Guardando…" : submitLabel}
          </button>
          <button
            type="button"
            className="button secondary"
            onClick={onClose}
            disabled={submitting}
          >
            Cancelar
          </button>
        </div>
      </form>
    </div>
  );
}
