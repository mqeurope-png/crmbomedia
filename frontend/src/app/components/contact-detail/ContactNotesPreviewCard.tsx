"use client";

/**
 * Card de Resumen ficha contacto — "Notas recientes" con 3 notas
 * más recientes + link "Ver todas" → tab Notas. PR-Db.
 *
 * PR-Bugs-4-5amp-7-9 — auditoría bug 9. Este componente YA dispara
 * su propio fetch al mount (`useEffect` con `listContactNotes`). El
 * mismo patrón está en ContactTasksPendingCard, ContactBrevoEngagement
 * Card, ContactUnsubscribeStatusCard y en el sub-card de Engagement de
 * ContactSummaryTab. Las únicas excepciones son las cards Actividad y
 * Tags, que vienen del `contact` que el padre ya ha cargado para
 * pintar la cabecera (evitando un round-trip redundante). Por eso
 * todos los widgets del Resumen son auto-suficientes desde el primer
 * mount.
 *
 * El bug reportado por Bart ("Notas recientes vacío al primer mount,
 * sí carga tras visitar la pestaña Notas") no se reproduce con este
 * código. Mantenemos el botón "Reintentar" como escape hatch por si
 * un fallo transitorio (401 durante refresh de token, race con la
 * cookie de sesión, etc.) deja el primer fetch en empty/error.
 */
import { ArrowUpRight, StickyNote } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { listContactNotes, type ContactNote } from "../../lib/contactNotesApi";
import { formatRelative, parseBackendDate } from "../../lib/dates";

type Props = {
  contactId: string;
  onSeeAll?: () => void;
};

// PR-Timezone-Fix. Delegado en la util compartida.
const relative = (value: string) => formatRelative(value);

function preview(content: string): string {
  const flat = content.replace(/\s+/g, " ").trim();
  return flat.length > 140 ? `${flat.slice(0, 140)}…` : flat;
}

export function ContactNotesPreviewCard({ contactId, onSeeAll }: Props) {
  const [notes, setNotes] = useState<ContactNote[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  const reload = useCallback(() => {
    setReloadKey((k) => k + 1);
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    listContactNotes(contactId)
      .then((rows) => {
        if (cancelled) return;
        // Ordenamos por created_at desc para mostrar las 3 más recientes;
        // el endpoint puede devolverlas en cualquier orden tras la
        // unificación 0049.
        const sorted = [...rows].sort(
          (a, b) =>
            parseBackendDate(b.created_at).getTime() -
            parseBackendDate(a.created_at).getTime(),
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
  }, [contactId, reloadKey]);

  return (
    <article className="card contact-summary-card">
      <header className="contact-summary-card-header">
        <h3>
          <StickyNote size={14} aria-hidden /> Notas recientes
        </h3>
        {/* PR-Bugs-4-5amp-7-9. Escape hatch para el "Sin notas todavía"
         * que Bart reportó al primer mount. Si un fallo transitorio
         * de auth dejó la lista vacía, este botón fuerza un re-fetch
         * sin tener que ir y volver de la pestaña Notas. */}
        {!loading && !error ? (
          <button
            type="button"
            className="contact-summary-link contact-summary-link-icon"
            onClick={reload}
            title="Recargar"
            aria-label="Recargar notas"
          >
            ⟳
          </button>
        ) : null}
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
