"use client";

/**
 * "🕐 Últimas interacciones" — feed cross-contacto. PR-C mock-up.
 *
 * El feed por-contacto YA existe en `/api/contacts/{id}/activity-
 * events`. Para el dashboard necesitamos una vista agregada con TODAS
 * las interacciones recientes de los contactos del operador. Hasta
 * que aterrice ese endpoint reusamos el de "recent-email-activity"
 * que sí ofrece un timeline agregado (limitado a emails).
 */
import { Mail, Phone, StickyNote } from "lucide-react";
import { useEffect, useState } from "react";
import Link from "next/link";
import { getDashboardRecentEmailActivity } from "../../lib/dashboardApi";
import type { RecentEmailEvent } from "../../lib/dashboardApi";

function relativeFromNow(iso: string): string {
  const then = new Date(iso).getTime();
  const diff = Date.now() - then;
  const sec = Math.floor(diff / 1000);
  if (sec < 60) return `hace ${sec}s`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `hace ${min}min`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `hace ${hr}h`;
  const day = Math.floor(hr / 24);
  return `hace ${day}d`;
}

function iconForEvent(event_type: string) {
  if (event_type.includes("call")) return <Phone size={14} aria-hidden />;
  if (event_type.includes("note")) return <StickyNote size={14} aria-hidden />;
  return <Mail size={14} aria-hidden />;
}

function labelForEvent(event_type: string): string {
  if (event_type === "sent") return "Email enviado";
  if (event_type === "opened") return "Email abierto";
  if (event_type === "clicked") return "Click en email";
  if (event_type === "bounced") return "Email rebotado";
  if (event_type === "unsubscribed") return "Baja de email";
  return event_type.replace("_", " ");
}

export function RecentInteractionsWidget() {
  const [events, setEvents] = useState<RecentEmailEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getDashboardRecentEmailActivity("mine")
      .then((rows) => {
        if (!cancelled) setEvents(rows.slice(0, 6));
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
  }, []);

  return (
    <article className="card widget widget-interactions">
      <header className="section-title">
        <h2>🕐 Últimas interacciones</h2>
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
                {iconForEvent(ev.event_type)}
              </span>
              <div className="widget-row-main">
                <p className="widget-row-title">
                  <Link href={`/contacts/${ev.contact_id}`}>{ev.contact_name}</Link>{" "}
                  <span className="muted small">· {labelForEvent(ev.event_type)}</span>
                </p>
                <p className="widget-row-meta">
                  <span className="muted small">{ev.subject ?? "Sin asunto"}</span>
                  <span className="muted small">{relativeFromNow(ev.occurred_at)}</span>
                </p>
              </div>
            </li>
          ))}
        </ul>
      )}
    </article>
  );
}
