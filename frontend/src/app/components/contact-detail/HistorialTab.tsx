"use client";

/** Sprint Ficha 360 — Bloque 2: pestaña Historial (timeline unificado). */
import { useCallback, useEffect, useState } from "react";
import {
  getContactTimeline,
  type TimelineEvent,
} from "../../lib/callsApi";
import { formatRelative } from "../../lib/dates";
import { extractErrorMessage } from "../../lib/errors";

const TYPE_CHIPS = [
  { key: "call", label: "Llamadas" },
  { key: "email", label: "Emails" },
  { key: "note", label: "Notas" },
  { key: "tag", label: "Tags" },
  { key: "pipeline", label: "Pipeline" },
  { key: "task", label: "Tareas" },
  { key: "brevo", label: "Brevo" },
  { key: "lifecycle", label: "Cambios" },
];

const PAGE = 100;

export function HistorialTab({
  contactId,
  refreshKey = 0,
}: {
  contactId: string;
  refreshKey?: number;
}) {
  const [items, setItems] = useState<TimelineEvent[]>([]);
  const [total, setTotal] = useState(0);
  const [sort, setSort] = useState<"asc" | "desc">("desc");
  const [selected, setSelected] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (offset: number, append: boolean) => {
    setLoading(true);
    setError(null);
    try {
      const page = await getContactTimeline(contactId, {
        limit: PAGE,
        offset,
        sort,
        types: selected.length ? selected : undefined,
      });
      setTotal(page.total);
      setItems((prev) => (append ? [...prev, ...page.items] : page.items));
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo cargar el historial."));
    } finally {
      setLoading(false);
    }
  }, [contactId, sort, selected]);

  useEffect(() => {
    void load(0, false);
  }, [load, refreshKey]);

  const toggleChip = (key: string) =>
    setSelected((prev) =>
      prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key],
    );

  return (
    <section className="historial-tab">
      <header className="historial-header">
        <h3>Historial</h3>
        <button
          type="button"
          className="button small secondary"
          onClick={() => setSort(sort === "desc" ? "asc" : "desc")}
          title="Cambiar orden"
        >
          {sort === "desc" ? "↓ Recientes primero" : "↑ Antiguos primero"}
        </button>
      </header>
      <div className="historial-chips">
        <button
          type="button"
          className={`chip ${selected.length === 0 ? "chip-active" : ""}`}
          onClick={() => setSelected([])}
        >
          Todo
        </button>
        {TYPE_CHIPS.map((c) => (
          <button
            key={c.key}
            type="button"
            className={`chip ${selected.includes(c.key) ? "chip-active" : ""}`}
            onClick={() => toggleChip(c.key)}
          >
            {c.label}
          </button>
        ))}
      </div>
      {error ? <p className="form-error">{error}</p> : null}
      {loading && items.length === 0 ? (
        <p className="muted small">Cargando…</p>
      ) : items.length === 0 ? (
        <p className="muted small">Sin eventos en el historial.</p>
      ) : (
        <ul className="historial-list">
          {items.map((e) => (
            <li key={e.id} className={`historial-item historial-${e.type}`}>
              <span className="historial-icon" aria-hidden>{e.icon}</span>
              <div className="historial-body">
                <p className="muted small">
                  {formatRelative(e.occurred_at)}
                  {e.actor ? ` · ${e.actor.name}` : ""}
                </p>
                <p className="historial-title">{e.title}</p>
                {e.detail ? (
                  <p className="historial-detail">{e.detail}</p>
                ) : null}
              </div>
            </li>
          ))}
        </ul>
      )}
      {items.length < total ? (
        <button
          type="button"
          className="button small secondary"
          disabled={loading}
          onClick={() => load(items.length, true)}
        >
          Cargar más ({items.length}/{total})
        </button>
      ) : null}
    </section>
  );
}
