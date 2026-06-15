"use client";

import { PenLine, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { EmailComposerModal } from "../../components/EmailComposerModal";
import { formatBackendDateTime } from "../../lib/dates";
import {
  type EmailDraft,
  deleteEmailDraft,
  listEmailDrafts,
} from "../../lib/emailsApi";
import { extractErrorMessage } from "../../lib/errors";

/** Right-pane view for `/emails/drafts`. Lists every draft owned
 *  by the current operator. Each row offers Continue (re-opens
 *  the composer with the draft hydrated) and Descartar
 *  (confirm + delete). Multi-select + bulk delete via the row
 *  checkboxes. */
export default function DraftsPage() {
  const [items, setItems] = useState<EmailDraft[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [openDraft, setOpenDraft] = useState<EmailDraft | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setItems(await listEmailDrafts());
      setSelected(new Set());
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudieron cargar los borradores."));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const toggle = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const onDiscard = async (id: string) => {
    if (!confirm("¿Descartar este borrador?")) return;
    setBusy(true);
    try {
      await deleteEmailDraft(id);
      await load();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo descartar."));
    } finally {
      setBusy(false);
    }
  };

  const onBulkDelete = async () => {
    const ids = Array.from(selected);
    if (ids.length === 0) return;
    if (!confirm(`¿Descartar ${ids.length} borrador${ids.length > 1 ? "es" : ""}?`))
      return;
    setBusy(true);
    try {
      await Promise.all(ids.map((id) => deleteEmailDraft(id)));
      await load();
    } catch (err) {
      setError(extractErrorMessage(err, "Alguno no se pudo descartar."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="email-thread-view">
      <header className="email-thread-actions">
        <div className="email-thread-actions-title">
          <h2>
            <PenLine size={18} aria-hidden /> Borradores
          </h2>
          <p className="muted small">
            Emails que has empezado a redactar y no has enviado todavía.
            Cada compose se autoguarda cada 5 segundos.
          </p>
        </div>
        {selected.size > 0 ? (
          <button
            type="button"
            className="btn"
            onClick={onBulkDelete}
            disabled={busy}
          >
            <Trash2 size={12} aria-hidden /> Descartar {selected.size}
          </button>
        ) : null}
      </header>

      {error ? <p className="form-error">{error}</p> : null}

      {loading ? (
        <p className="muted">Cargando…</p>
      ) : items.length === 0 ? (
        <p className="muted">No tienes ningún borrador.</p>
      ) : (
        <ul className="email-drafts-list">
          {items.map((d) => {
            const subject = d.subject || "(sin asunto)";
            const to = d.to_emails[0] ?? "(sin destinatario)";
            const snippet = d.body_text
              ? d.body_text.slice(0, 160)
              : d.body_html
                ? d.body_html.replace(/<[^>]+>/g, " ").slice(0, 160)
                : "";
            return (
              <li key={d.id} className="email-drafts-item">
                <input
                  type="checkbox"
                  checked={selected.has(d.id)}
                  onChange={() => toggle(d.id)}
                  aria-label={`Seleccionar ${subject}`}
                />
                <button
                  type="button"
                  className="email-drafts-row"
                  onClick={() => setOpenDraft(d)}
                >
                  <span className="email-drafts-subject">{subject}</span>
                  <span className="email-drafts-meta muted small">
                    Para: {to}
                  </span>
                  {snippet ? (
                    <span className="email-drafts-snippet muted small">
                      {snippet}
                    </span>
                  ) : null}
                  <span className="email-drafts-updated muted small">
                    Actualizado {formatBackendDateTime(d.updated_at)}
                  </span>
                </button>
                <div className="email-drafts-actions">
                  <button
                    type="button"
                    className="btn small"
                    onClick={() => setOpenDraft(d)}
                    disabled={busy}
                  >
                    Continuar
                  </button>
                  <button
                    type="button"
                    className="btn small"
                    onClick={() => onDiscard(d.id)}
                    disabled={busy}
                  >
                    <Trash2 size={11} aria-hidden /> Descartar
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      )}

      {openDraft ? (
        <div
          className="email-compose-overlay"
          role="presentation"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) setOpenDraft(null);
          }}
        >
          <EmailComposerModal
            initialDraft={openDraft}
            contactId={openDraft.contact_id}
            onClose={() => setOpenDraft(null)}
            onSent={() => {
              setOpenDraft(null);
              void load();
            }}
          />
        </div>
      ) : null}
    </div>
  );
}
