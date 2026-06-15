"use client";

import { CheckCircle2, Mail, Plus, Star, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import {
  type ContactEmail,
  createContactEmail,
  deleteContactEmail,
  listContactEmails,
  setPrimaryEmail,
  updateContactEmail,
} from "../lib/contactChannelsApi";
import { extractErrorMessage } from "../lib/errors";

type Props = { contactId: string };

/** Sprint Empresas — sub-PR 3/4. List of every email address the
 *  contact owns. Distinct from the existing v2.4
 *  `ContactEmailsSection` (which renders threaded conversations);
 *  the rename to `Secondary` keeps both imports unambiguous. */
export function ContactSecondaryEmailsSection({ contactId }: Props) {
  const [items, setItems] = useState<ContactEmail[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState({ label: "", email: "" });

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setItems(await listContactEmails(contactId));
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudieron cargar los emails."));
    } finally {
      setLoading(false);
    }
  }, [contactId]);

  useEffect(() => {
    void load();
  }, [load]);

  const onAdd = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!draft.email.trim()) return;
    try {
      await createContactEmail(contactId, {
        label: draft.label.trim() || null,
        email: draft.email.trim(),
      });
      setAdding(false);
      setDraft({ label: "", email: "" });
      await load();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo añadir."));
    }
  };

  const onPrimary = async (id: string) => {
    try {
      await setPrimaryEmail(contactId, id);
      await load();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo marcar primario."));
    }
  };

  const onDelete = async (id: string) => {
    if (!confirm("¿Borrar este email?")) return;
    try {
      await deleteContactEmail(contactId, id);
      await load();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo borrar."));
    }
  };

  const onToggleVerified = async (row: ContactEmail) => {
    try {
      await updateContactEmail(contactId, row.id, {
        label: row.label,
        email: row.email,
        is_primary: row.is_primary,
        is_verified: !row.is_verified,
        source: row.source,
      });
      await load();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo cambiar verificado."));
    }
  };

  return (
    <section className="contact-card">
      <h4>
        <Mail size={12} aria-hidden /> Direcciones de email
      </h4>
      {error ? <p className="form-error">{error}</p> : null}
      {loading ? (
        <p className="muted">Cargando…</p>
      ) : items.length === 0 && !adding ? (
        <p className="muted small">Sin direcciones adicionales.</p>
      ) : (
        <ul className="contact-channel-list">
          {items.map((row) => (
            <li key={row.id} className="contact-channel-row">
              <button
                type="button"
                className={`contact-channel-primary${row.is_primary ? " is-on" : ""}`}
                onClick={() => onPrimary(row.id)}
                title={row.is_primary ? "Primario" : "Marcar primario"}
              >
                <Star
                  size={12}
                  aria-hidden
                  fill={row.is_primary ? "#facc15" : "none"}
                  color={row.is_primary ? "#facc15" : "#cbd5e1"}
                />
              </button>
              <a href={`mailto:${row.email}`}>{row.email}</a>
              {row.label ? (
                <span className="muted small">{row.label}</span>
              ) : null}
              <button
                type="button"
                className={`contact-channel-verified${row.is_verified ? " is-on" : ""}`}
                onClick={() => onToggleVerified(row)}
                title={row.is_verified ? "Verificado" : "Marcar verificado"}
              >
                <CheckCircle2
                  size={12}
                  aria-hidden
                  color={row.is_verified ? "#10b981" : "#cbd5e1"}
                />
              </button>
              <button
                type="button"
                className="btn small"
                onClick={() => onDelete(row.id)}
              >
                <Trash2 size={11} aria-hidden />
              </button>
            </li>
          ))}
        </ul>
      )}
      {adding ? (
        <form onSubmit={onAdd} className="contact-channel-add">
          <input
            type="text"
            placeholder="etiqueta (personal, trabajo…)"
            value={draft.label}
            onChange={(e) => setDraft({ ...draft, label: e.target.value })}
          />
          <input
            type="email"
            placeholder="email@ejemplo.com"
            value={draft.email}
            onChange={(e) => setDraft({ ...draft, email: e.target.value })}
            required
            autoFocus
          />
          <button type="submit" className="btn btn-primary small">
            Añadir
          </button>
          <button
            type="button"
            className="btn small"
            onClick={() => {
              setAdding(false);
              setDraft({ label: "", email: "" });
            }}
          >
            Cancelar
          </button>
        </form>
      ) : (
        <button
          type="button"
          className="btn small"
          onClick={() => setAdding(true)}
        >
          <Plus size={11} aria-hidden /> Añadir email
        </button>
      )}
    </section>
  );
}
