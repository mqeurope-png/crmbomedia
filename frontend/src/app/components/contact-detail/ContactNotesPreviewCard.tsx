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
 * Bug (Bart): "Notas recientes vacío al primer mount, sí carga tras
 * visitar la pestaña Notas". El backend es determinista (devuelve
 * siempre TODAS las notas del contacto, sin filtro per-user ni sync
 * perezoso al leer), así que un [] en el primer fetch solo puede ser un
 * race transitorio (lag de lectura / import en vuelo) que se corrige al
 * remontar. Fix: un ÚNICO reintento silencioso cuando el primer fetch
 * resuelve vacío — inofensivo para contactos realmente sin notas (una
 * request extra) y suficiente para el caso de Bart. El botón "⟳" queda
 * como escape hatch manual.
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

// PR-Hotfix-Notas-Widget-Importadas. Fecha efectiva de la nota: para las
// importadas, `external_created_at` es la fecha REAL (p.ej. 2020); el
// `created_at` es solo el instante de importación (reciente y engañoso).
function effectiveDate(n: ContactNote): string {
  return n.external_created_at ?? n.created_at;
}

// Etiqueta legible del sistema de origen para el badge.
function originLabel(system: string | null): string {
  if (!system) return "";
  const map: Record<string, string> = { agilecrm: "AgileCRM", brevo: "Brevo" };
  return map[system.toLowerCase()] ?? system;
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
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    // Un único reintento automático por carga si el primer fetch llega
    // vacío (ver cabecera). Local al effect → se reinicia en cada
    // contactId / reload manual.
    let retried = false;
    setLoading(true);
    setError(null);

    const run = () => {
      listContactNotes(contactId)
        .then((rows) => {
          if (cancelled) return;
          if (rows.length === 0 && !retried) {
            retried = true;
            retryTimer = setTimeout(run, 700);
            return; // mantenemos "Cargando…" durante el reintento
          }
          // Ordenamos por la fecha EFECTIVA desc (external_created_at si
          // existe, sino created_at) para mostrar las 3 más recientes; el
          // endpoint puede devolverlas en cualquier orden tras la
          // unificación 0049. Sin esto, las notas importadas (con fecha
          // real antigua pero created_at reciente) se colaban al principio.
          const sorted = [...rows].sort(
            (a, b) =>
              parseBackendDate(effectiveDate(b)).getTime() -
              parseBackendDate(effectiveDate(a)).getTime(),
          );
          setNotes(sorted.slice(0, 3));
          setLoading(false);
        })
        .catch(() => {
          if (!cancelled) {
            setError("No se pudieron cargar las notas.");
            setLoading(false);
          }
        });
    };
    run();

    return () => {
      cancelled = true;
      if (retryTimer) clearTimeout(retryTimer);
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
                {/* PR-Hotfix-Notas-Widget-Importadas. Autor con fallback:
                    nota importada → nombre externo + badge de origen. */}
                {n.external_author_name ? (
                  <>
                    <span>{n.external_author_name}</span>
                    {n.external_system ? (
                      <span className="note-origin-badge">
                        {originLabel(n.external_system)}
                      </span>
                    ) : null}
                    {" · "}
                  </>
                ) : null}
                {relative(effectiveDate(n))}
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
