"use client";

/**
 * Card de Resumen ficha contacto — "Notas recientes": las 3 notas más
 * recientes + link "Ver todas" → tab Notas. PR-Db.
 *
 * PR-Hotfix-Notas-Widget. CAUSA RAÍZ del "Sin notas todavía" al primer
 * mount: las notas de AgileCRM se importan ON-DEMAND al abrir la ficha
 * (auto-refresh de page.tsx cuando external_data_freshness=outdated).
 * En un contacto importado nunca abierto, el fetch del widget corre
 * ANTES de que el refresh inserte las notas → [] legítimo — y el widget
 * no volvía a fetchear. La pestaña Notas "funcionaba" solo porque el
 * usuario la abre DESPUÉS de que el refresh terminó.
 *
 * Fix: mecanismo de carga IDÉNTICO al de ContactNotesSection (mismo
 * endpoint, mismo patrón load/useEffect, sin retries ni timers) + un
 * `refreshKey` que la página sube al COMPLETARSE el refresh externo →
 * el widget re-fetchea y ve las filas recién importadas, exactamente
 * como las vería la pestaña al abrirse. Mientras el refresh está en
 * vuelo (`refreshing`), mostramos "Cargando…" en lugar de un "Sin
 * notas todavía" transitorio.
 */
import { ArrowUpRight, StickyNote } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { listContactNotes, type ContactNote } from "../../lib/contactNotesApi";
import { formatRelative, parseBackendDate } from "../../lib/dates";
import { extractErrorMessage } from "../../lib/errors";

type Props = {
  contactId: string;
  /** Sube cuando un refresh externo (AgileCRM) termina — re-fetch. */
  refreshKey?: number;
  /** True mientras el refresh externo está en vuelo — spinner. */
  refreshing?: boolean;
  onSeeAll?: () => void;
};

/** Cuántas notas caben en el card. */
const PREVIEW_LIMIT = 3;

// PR-Timezone-Fix. Delegado en la util compartida.
const relative = (value: string) => formatRelative(value);

function preview(content: string): string {
  const flat = content.replace(/\s+/g, " ").trim();
  return flat.length > 140 ? `${flat.slice(0, 140)}…` : flat;
}

// Fecha efectiva: para importadas, `external_created_at` es la fecha
// REAL (p.ej. 2020); `created_at` es solo el instante de importación.
function effectiveDate(n: ContactNote): string {
  return n.external_created_at ?? n.created_at;
}

// Etiqueta legible del sistema de origen para el badge.
function originLabel(system: string | null): string {
  if (!system) return "";
  const map: Record<string, string> = { agilecrm: "AgileCRM", brevo: "Brevo" };
  return map[system.toLowerCase()] ?? system;
}

export function ContactNotesPreviewCard({
  contactId,
  refreshKey = 0,
  refreshing = false,
  onSeeAll,
}: Props) {
  const [items, setItems] = useState<ContactNote[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Mismo mecanismo que ContactNotesSection (la pestaña Notas): un
  // `load` useCallback → GET /api/contacts/{id}/notes → estado local.
  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
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
  }, [load, refreshKey]);

  // Orden desc por fecha efectiva + slice a lo que cabe en el card. Sin
  // ningún filtro adicional (ni autor, ni origen).
  const notes = [...items]
    .sort(
      (a, b) =>
        parseBackendDate(effectiveDate(b)).getTime() -
        parseBackendDate(effectiveDate(a)).getTime(),
    )
    .slice(0, PREVIEW_LIMIT);

  return (
    <article className="card contact-summary-card">
      <header className="contact-summary-card-header">
        <h3>
          <StickyNote size={14} aria-hidden /> Notas recientes
        </h3>
      </header>
      {loading || refreshing ? (
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
