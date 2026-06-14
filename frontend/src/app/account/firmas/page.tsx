"use client";

import {
  Pencil,
  Plus,
  Star,
  StarOff,
  Trash2,
} from "lucide-react";
import dynamic from "next/dynamic";
import { useCallback, useEffect, useState } from "react";
import { ErrorState } from "../../components/ErrorState";
import { PageHeader } from "../../components/PageHeader";
import {
  createEmailSignature,
  deleteEmailSignature,
  listEmailSignatures,
  setDefaultEmailSignature,
  updateEmailSignature,
  type EmailSignature,
} from "../../lib/emailSignaturesApi";
import { extractErrorMessage } from "../../lib/errors";

// TinyMCE touches `window` at import — keep it in a client-only chunk.
const RichEditor = dynamic(
  () =>
    import("../../components/email/RichEditor").then((m) => m.RichEditor),
  {
    ssr: false,
    loading: () => <div className="re-loading">Cargando editor…</div>,
  },
);

type DraftState = {
  id: string | null;
  name: string;
  html: string;
  isDefault: boolean;
};

const EMPTY_DRAFT: DraftState = {
  id: null,
  name: "",
  html: "",
  isDefault: false,
};

export default function FirmasPage() {
  const [items, setItems] = useState<EmailSignature[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState<DraftState | null>(null);
  const [saving, setSaving] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const rows = await listEmailSignatures();
      setItems(rows);
      setError(null);
    } catch (err) {
      setError(
        extractErrorMessage(err, "No se pudieron cargar las firmas."),
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  function openCreate() {
    setDraft({ ...EMPTY_DRAFT });
  }

  function openEdit(s: EmailSignature) {
    setDraft({
      id: s.id,
      name: s.name,
      html: s.html_content,
      isDefault: s.is_default,
    });
  }

  async function handleSave(event: React.FormEvent) {
    event.preventDefault();
    if (!draft) return;
    if (!draft.name.trim()) {
      setError("El nombre es obligatorio.");
      return;
    }
    if (!draft.html.trim()) {
      setError("El cuerpo de la firma está vacío.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const payload = {
        name: draft.name.trim(),
        html_content: draft.html,
        is_default: draft.isDefault,
        sort_order: 0,
      };
      if (draft.id) {
        await updateEmailSignature(draft.id, payload);
      } else {
        await createEmailSignature(payload);
      }
      setDraft(null);
      await refresh();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo guardar la firma."));
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(s: EmailSignature) {
    if (!window.confirm(`¿Borrar la firma "${s.name}"?`)) return;
    try {
      await deleteEmailSignature(s.id);
      await refresh();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo borrar la firma."));
    }
  }

  async function handleSetDefault(s: EmailSignature) {
    try {
      await setDefaultEmailSignature(s.id);
      await refresh();
    } catch (err) {
      setError(
        extractErrorMessage(err, "No se pudo cambiar la firma por defecto."),
      );
    }
  }

  return (
    <main className="shell narrow">
      <PageHeader
        title="Firmas de email"
        eyebrow="Cuenta"
        description="Crea varias firmas y marca una como predeterminada — se añadirá automáticamente al redactar un email."
      />
      {error ? <ErrorState title="Error" message={error} /> : null}

      <section className="card sig-card">
        <header className="sig-card-header">
          <h2>Tus firmas</h2>
          <button type="button" className="button small" onClick={openCreate}>
            <Plus size={12} aria-hidden /> Nueva firma
          </button>
        </header>

        {loading ? (
          <p className="muted">Cargando…</p>
        ) : items.length === 0 ? (
          <p className="muted">
            Aún no tienes firmas. Crea la primera con el botón de arriba.
          </p>
        ) : (
          <ul className="sig-list">
            {items.map((s) => (
              <li
                key={s.id}
                className={`sig-row${s.is_default ? " is-default" : ""}`}
              >
                <button
                  type="button"
                  className="sig-default-toggle"
                  title={
                    s.is_default
                      ? "Predeterminada — no puede desmarcarse"
                      : "Marcar como predeterminada"
                  }
                  onClick={() => handleSetDefault(s)}
                  disabled={s.is_default}
                >
                  {s.is_default ? (
                    <Star size={14} aria-hidden />
                  ) : (
                    <StarOff size={14} aria-hidden />
                  )}
                </button>
                <div className="sig-row-body">
                  <div className="sig-row-name">
                    <strong>{s.name}</strong>
                    {s.is_default ? (
                      <span className="sig-row-pill">predeterminada</span>
                    ) : null}
                  </div>
                  <div
                    className="sig-row-preview"
                    dangerouslySetInnerHTML={{ __html: s.html_content }}
                  />
                </div>
                <div className="sig-row-actions">
                  <button
                    type="button"
                    className="button secondary small"
                    onClick={() => openEdit(s)}
                  >
                    <Pencil size={12} aria-hidden /> Editar
                  </button>
                  <button
                    type="button"
                    className="button secondary small"
                    onClick={() => handleDelete(s)}
                  >
                    <Trash2 size={12} aria-hidden /> Borrar
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      {draft ? (
        <div
          className="modal-overlay"
          role="dialog"
          aria-modal="true"
          onClick={(e) => {
            if (e.target === e.currentTarget) setDraft(null);
          }}
        >
          <div className="modal-dialog sig-editor-dialog">
            <div className="modal-header">
              <h2>{draft.id ? "Editar firma" : "Nueva firma"}</h2>
              <button
                type="button"
                className="modal-close"
                onClick={() => setDraft(null)}
                aria-label="Cerrar"
              >
                ×
              </button>
            </div>
            <div className="modal-body">
              <form className="modal-form" onSubmit={handleSave}>
                <label>
                  <span>Nombre</span>
                  <input
                    type="text"
                    required
                    maxLength={120}
                    value={draft.name}
                    onChange={(e) =>
                      setDraft({ ...draft, name: e.target.value })
                    }
                  />
                </label>
                <label className="sig-default-check">
                  <input
                    type="checkbox"
                    checked={draft.isDefault}
                    onChange={(e) =>
                      setDraft({ ...draft, isDefault: e.target.checked })
                    }
                  />
                  <span>Usar como firma predeterminada</span>
                </label>
                <label>
                  <span>Contenido</span>
                  <RichEditor
                    value={draft.html}
                    onChange={(html) => setDraft({ ...draft, html })}
                    placeholder="Saludos cordiales, tu nombre. Logo arriba si quieres."
                    minHeight={300}
                    draftKey={`signature-${draft.id ?? "new"}`}
                  />
                </label>
                <div className="modal-footer">
                  <button
                    type="button"
                    className="button secondary"
                    onClick={() => setDraft(null)}
                  >
                    Cancelar
                  </button>
                  <button
                    type="submit"
                    className="button"
                    disabled={saving}
                  >
                    {saving ? "Guardando…" : "Guardar firma"}
                  </button>
                </div>
              </form>
            </div>
          </div>
        </div>
      ) : null}
    </main>
  );
}
