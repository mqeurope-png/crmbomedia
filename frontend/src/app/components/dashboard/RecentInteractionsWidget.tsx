"use client";

/**
 * "🕐 Últimas interacciones" — PR-E2 timeline mixto, PR-E3 añade
 * selector temporal + persistencia. Toggle scope Mías/Equipo +
 * período [3d][1sem][15d][30d][Custom].
 */
import { CheckSquare, Mail, MessageCircle, Phone, StickyNote } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { formatRelative } from "../../lib/dates";
import {
  getDashboardRecentInteractions,
  type DashboardWindow,
  type RecentInteraction,
} from "../../lib/dashboardApi";
import { usePersistentState } from "../../lib/usePersistentState";
import { PeriodSelector } from "./PeriodSelector";

type Scope = "mine" | "team";

// PR-Timezone-Fix. Sustituye el `new Date(value)` directo por la util
// que tolera ISO sin offset (compat con endpoints que aún no pasen
// por el SQLAlchemy event listener de `app/models/crm.py`).
const relative = (value: string) => formatRelative(value);

function iconFor(eventType: string): React.ReactNode {
  const t = eventType.toLowerCase();
  if (t.includes("call")) return <Phone size={12} aria-hidden />;
  if (t.includes("note")) return <StickyNote size={12} aria-hidden />;
  if (t.includes("task")) return <CheckSquare size={12} aria-hidden />;
  if (t.includes("email")) return <Mail size={12} aria-hidden />;
  return <MessageCircle size={12} aria-hidden />;
}

function labelFor(eventType: string): string {
  const t = eventType.toLowerCase();
  if (t.includes("sent")) return "Email enviado";
  if (t.includes("opened")) return "Email abierto";
  if (t.includes("clicked")) return "Click en email";
  if (t.includes("call")) return "Llamada registrada";
  if (t.includes("note")) return "Nota añadida";
  if (t.includes("task.completed")) return "Tarea completada";
  if (t.includes("task.created")) return "Tarea creada";
  return eventType.replace(/[_.]/g, " ");
}

export function RecentInteractionsWidget() {
  const [scope, setScope] = usePersistentState<Scope>(
    "crmbomedia_dash:recent_interactions:scope",
    "mine",
  );
  const [window_, setWindow] = usePersistentState<DashboardWindow>(
    "crmbomedia_dash:recent_interactions:period",
    { period: "7d" },
  );
  const [events, setEvents] = useState<RecentInteraction[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getDashboardRecentInteractions(scope, window_, 20)
      .then((rows) => {
        if (!cancelled) setEvents(rows);
      })
      .catch(() => {
        if (!cancelled)
          setError("No se pudieron cargar las interacciones recientes.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [scope, window_]);

  return (
    <article className="card widget widget-interactions">
      <header className="section-title section-title-stack">
        <h2>🕐 Últimas interacciones</h2>
        <div className="widget-header-controls">
          <div className="widget-segment" role="radiogroup" aria-label="Scope">
            <button
              type="button"
              role="radio"
              aria-checked={scope === "mine"}
              className={`widget-segment-item${
                scope === "mine" ? " is-active" : ""
              }`}
              onClick={() => setScope("mine")}
            >
              Mías
            </button>
            <button
              type="button"
              role="radio"
              aria-checked={scope === "team"}
              className={`widget-segment-item${
                scope === "team" ? " is-active" : ""
              }`}
              onClick={() => setScope("team")}
            >
              Equipo
            </button>
          </div>
          <PeriodSelector value={window_} onChange={setWindow} />
        </div>
      </header>
      <div className="widget-scroll">
        {loading ? (
          <p className="muted small">Cargando…</p>
        ) : error ? (
          <p className="form-error">{error}</p>
        ) : events.length === 0 ? (
          <div className="widget-empty">
            <p className="muted small">Sin interacciones recientes.</p>
          </div>
        ) : (
          <ul className="widget-list">
            {events.map((ev) => (
              <li key={ev.id} className="widget-row">
                <span className="widget-row-icon" aria-hidden>
                  {iconFor(ev.event_type)}
                </span>
                <div className="widget-row-main">
                  <p className="widget-row-title">
                    <Link href={`/contacts/${ev.contact_id}`}>
                      {ev.contact_name}
                    </Link>{" "}
                    <span className="muted small">
                      · {labelFor(ev.event_type)}
                    </span>
                  </p>
                  <p className="widget-row-meta">
                    <span className="muted small">
                      {ev.subject ?? "Sin asunto"}
                    </span>
                    <span className="muted small">
                      {relative(ev.occurred_at)}
                    </span>
                  </p>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </article>
  );
}
