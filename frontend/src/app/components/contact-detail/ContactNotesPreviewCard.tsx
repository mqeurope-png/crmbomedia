"use client";

/**
 * Card de Resumen ficha contacto — "Notas recientes" con 3 notas
 * más recientes + link "Ver todas" → tab Notas. PR-Db.
 */
import { ArrowUpRight, StickyNote } from "lucide-react";
import { useEffect, useState } from "react";
import { listContactNotes, type ContactNote } from "../../lib/contactNotesApi";

type Props = {
  contactId: string;
  onSeeAll?: () => void;
};

function relative(value: string): string {
  const then = new Date(value).getTime();
  if (Number.isNaN(then)) return "—";
  const diff = Date.now() - then;
  const min = Math.floor(diff / 60000);
  if (min < 1) return "ahora";
  if (min < 60) return `hace ${min}min`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `hace ${hr}h`;
  const day = Math.floor(hr / 24);
  return `hace ${day}d`;
}

function preview(content: string): string {
  const flat = content.replace(/\s+/g, " ").trim();
  return flat.length > 140 ? `${flat.slice(0, 140)}…` : flat;
}

export function ContactNotesPreviewCard({ contactId, onSeeAll }: Props) {
  const [notes, setNotes] = useState<ContactNote[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    listContactNotes(contactId)
      .then((rows) => {
        if (cancelled) return;
        // Ordenamos por created_at desc para mostrar las 3 más recientes;
        // el endpoint puede devolverlas en cualquier orden tras la
        // unificación 0049.
        const sorted = [...rows].sort(
          (a, b) =>
            new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
        );
        setNotes(sorted.slice(0, 3));
      })
      .catch(() => {
        if (!cancelled) setError("No se pudieron cargar las notas.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [contactId]);

  return (
    <article className="card contact-summary-card">
      <header className="contact-summary-card-header">
        <h3>
          <StickyNote size={14} aria-hidden /> Notas recientes
        </h3>
      </header>
      {loading ? (
        <p className="muted small">Cargando…</p>
      ) : error ? (
        <p className="form-error">{error}</p>
      ) : notes.length === 0 ? (
        <p className="muted small">Sin notas todavía.</p>
      ) : (
        <ul className="contact-notes-preview-list">
          {notes.map((n) => (
            <li key={n.id} className="contact-notes-preview-item">
              <p className="contact-notes-preview-text">{preview(n.content)}</p>
              <p className="muted small">
                {relative(n.created_at)}
                {n.pinned ? " · 📌 pinned" : ""}
              </p>
            </li>
          ))}
        </ul>
      )}
      {onSeeAll ? (
        <button
          type="button"
          className="contact-summary-link"
          onClick={onSeeAll}
        >
          Ver todas <ArrowUpRight size={12} aria-hidden />
        </button>
      ) : null}
    </article>
  );
}
