"use client";

/**
 * "🕐 Últimas interacciones" — PR-E2 reescrito para usar el endpoint
 * `/api/dashboard/recent-interactions` que mezcla email + call + note
 * + task events ordenados por `occurred_at` desc.
 *
 * Toggle scope `mine` / `team` para que el operador vea su perímetro
 * o el del equipo. Default `mine`.
 */
import { CheckSquare, Mail, MessageCircle, Phone, StickyNote } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import {
  getDashboardRecentInteractions,
  type RecentInteraction,
} from "../../lib/dashboardApi";

type Scope = "mine" | "team";

function relative(value: string): string {
  const then = new Date(value).getTime();
  if (Number.isNaN(then)) return "—";
  const diff = Date.now() - then;
  const min = Math.floor(diff / 60_000);
  if (min < 1) return "ahora";
  if (min < 60) return `hace ${min}m`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `hace ${hr}h`;
  const day = Math.floor(hr / 24);
  return `hace ${day}d`;
}

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
  const [scope, setScope] = useState<Scope>("mine");
  const [events, setEvents] = useState<RecentInteraction[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getDashboardRecentInteractions(scope, 15)
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
  }, [scope]);

  return (
    <article className="card widget widget-interactions">
      <header className="section-title">
        <h2>🕐 Últimas interacciones</h2>
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
      </header>
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
                  <span className="muted small">· {labelFor(ev.event_type)}</span>
                </p>
                <p className="widget-row-meta">
                  <span className="muted small">
                    {ev.subject ?? "Sin asunto"}
                  </span>
                  <span className="muted small">{relative(ev.occurred_at)}</span>
                </p>
              </div>
            </li>
          ))}
        </ul>
      )}
    </article>
  );
}
