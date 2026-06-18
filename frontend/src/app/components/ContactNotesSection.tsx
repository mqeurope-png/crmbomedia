"use client";

import { Pencil, Pin, PinOff, Plus, StickyNote, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import {
  type ContactNote,
  createContactNote,
  deleteContactNote,
  listContactNotes,
  pinContactNote,
  unpinContactNote,
  updateContactNote,
} from "../lib/contactNotesApi";
import { formatBackendDateTime } from "../lib/dates";
import { extractErrorMessage } from "../lib/errors";

type Props = { contactId: string };

// PR-Timezone-Fix.
const formatDate = (iso: string) => formatBackendDateTime(iso);

const sourceLabel = (source: string) => {
  if (source === "manual") return "manual";
  if (source === "agile:timeline") return "Agile timeline";
  if (source.startsWith("agile:")) return source.replace("agile:", "Agile ");
  return source;
};

/** Pinta el bloque de meta de una nota: autor + fecha + source. Las
 *  notas Agile timeline traen autor remoto; el resto van por
 *  source. */
const renderMeta = (row: ContactNote): string => {
  const author = row.external_author_name || row.external_author_email;
  const date = row.external_created_at ?? row.created_at;
  const dt = formatDate(date);
  if (author) return `${author} · ${sourceLabel(row.source)} · ${dt}`;
  return `${sourceLabel(row.source)} · ${dt}`;
};

export function ContactNotesSection({ contactId }: Props) {
  const [items, setItems] = useState<ContactNote[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingContent, setEditingContent] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setItems(await listContactNotes(contactId));
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudieron cargar las notas."));
    } finally {
      setLoading(false);
    }
  }, [contactId]);

  useEffect(() => {
    void load();
  }, [load]);

  const onAdd = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!draft.trim()) return;
    try {
      await createContactNote(contactId, { content: draft.trim() });
      setAdding(false);
      setDraft("");
      await load();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo añadir la nota."));
    }
  };

  const onSaveEdit = async (row: ContactNote) => {
    if (!editingContent.trim()) {
      setEditingId(null);
      return;
    }
    try {
      await updateContactNote(contactId, row.id, {
        content: editingContent.trim(),
        pinned: row.pinned,
      });
      setEditingId(null);
      setEditingContent("");
      await load();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo guardar la nota."));
    }
  };

  const onTogglePin = async (row: ContactNote) => {
    try {
      if (row.pinned) {
        await unpinContactNote(contactId, row.id);
      } else {
        await pinContactNote(contactId, row.id);
      }
      await load();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo cambiar el pin."));
    }
  };

  const onDelete = async (id: string) => {
    if (!confirm("¿Borrar esta nota?")) return;
    try {
      await deleteContactNote(contactId, id);
      await load();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo borrar la nota."));
    }
  };

  return (
    <section className="contact-card">
      <h4>
        <StickyNote size={12} aria-hidden /> Notas
      </h4>
      {error ? <p className="form-error">{error}</p> : null}
      {loading ? (
        <p className="muted">Cargando…</p>
      ) : items.length === 0 && !adding ? (
        <p className="muted small">Sin notas.</p>
      ) : (
        <ul className="contact-notes-list">
          {items.map((row) => (
            <li
              key={row.id}
              className={`contact-note-row${row.pinned ? " is-pinned" : ""}`}
            >
              <div className="contact-note-header">
                <button
                  type="button"
                  className={`contact-note-pin${row.pinned ? " is-on" : ""}`}
                  onClick={() => onTogglePin(row)}
                  title={row.pinned ? "Despinear" : "Pinear arriba"}
                >
                  {row.pinned ? (
                    <Pin size={12} aria-hidden fill="#facc15" color="#facc15" />
                  ) : (
                    <PinOff size={12} aria-hidden color="#cbd5e1" />
                  )}
                </button>
                <span
                  className="contact-note-meta small muted"
                  title={row.external_author_email ?? undefined}
                >
                  {renderMeta(row)}
                </span>
                <div className="contact-note-actions">
                  {editingId === row.id ? null : (
                    <button
                      type="button"
                      className="button secondary small"
                      onClick={() => {
                        setEditingId(row.id);
                        setEditingContent(row.content);
                      }}
                      title="Editar"
                    >
                      <Pencil size={11} aria-hidden />
                    </button>
                  )}
                  <button
                    type="button"
                    className="button secondary small"
                    onClick={() => onDelete(row.id)}
                    title="Borrar"
                  >
                    <Trash2 size={11} aria-hidden />
                  </button>
                </div>
              </div>
              {editingId === row.id ? (
                <div className="contact-note-edit">
                  <textarea
                    value={editingContent}
                    onChange={(e) => setEditingContent(e.target.value)}
                    rows={3}
                    autoFocus
                  />
                  <div className="contact-note-edit-actions">
                    <button
                      type="button"
                      className="button small"
                      onClick={() => onSaveEdit(row)}
                    >
                      Guardar
                    </button>
                    <button
                      type="button"
                      className="button secondary small"
                      onClick={() => {
                        setEditingId(null);
                        setEditingContent("");
                      }}
                    >
                      Cancelar
                    </button>
                  </div>
                </div>
              ) : (
                <p className="contact-note-content">{row.content}</p>
              )}
            </li>
          ))}
        </ul>
      )}
      {adding ? (
        <form onSubmit={onAdd} className="contact-note-add">
          <textarea
            placeholder="Escribe una nota…"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            rows={3}
            autoFocus
            required
          />
          <div className="contact-note-edit-actions">
            <button type="submit" className="button small">
              Añadir
            </button>
            <button
              type="button"
              className="button secondary small"
              onClick={() => {
                setAdding(false);
                setDraft("");
              }}
            >
              Cancelar
            </button>
          </div>
        </form>
      ) : (
        <button
          type="button"
          className="button secondary small"
          onClick={() => setAdding(true)}
        >
          <Plus size={11} aria-hidden /> Nueva nota
        </button>
      )}
    </section>
  );
}
