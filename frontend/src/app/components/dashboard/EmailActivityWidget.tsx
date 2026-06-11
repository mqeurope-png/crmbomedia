"use client";

import { Mail } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import {
  getDashboardRecentEmailActivity,
  type RecentEmailEvent,
} from "../../lib/dashboardApi";
import { extractErrorMessage } from "../../lib/errors";

const EVENT_LABEL: Record<string, string> = {
  email_sent: "Enviado",
  EMAIL_SENT: "Enviado",
  email_opened: "Abierto",
  EMAIL_OPENED: "Abierto",
  email_clicked: "Click",
  EMAIL_CLICKED: "Click",
  email_bounced: "Rebotado",
  email_unsubscribed: "Baja",
};

function formatTime(value: string): string {
  const d = new Date(value);
  return d.toLocaleString("es-ES", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function EmailActivityWidget() {
  const [scope, setScope] = useState<"mine" | "all">("all");
  const [events, setEvents] = useState<RecentEmailEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    getDashboardRecentEmailActivity(scope)
      .then(setEvents)
      .catch((err) =>
        setError(extractErrorMessage(err, "No se pudieron cargar los eventos.")),
      )
      .finally(() => setLoading(false));
  }, [scope]);

  return (
    <article className="card widget widget-email">
      <header className="section-title">
        <h2>
          <Mail size={14} aria-hidden /> Actividad email
        </h2>
        <div className="widget-toolbar">
          <button
            type="button"
            className={`pill-toggle ${scope === "all" ? "is-active" : ""}`}
            onClick={() => setScope("all")}
          >
            Todo
          </button>
          <button
            type="button"
            className={`pill-toggle ${scope === "mine" ? "is-active" : ""}`}
            onClick={() => setScope("mine")}
          >
            Míos
          </button>
        </div>
      </header>
      {loading ? (
        <p className="muted small">Cargando…</p>
      ) : error ? (
        <p className="form-error">{error}</p>
      ) : events.length === 0 ? (
        <p className="muted small">Sin actividad reciente.</p>
      ) : (
        <ul className="widget-list">
          {events.map((evt) => (
            <li key={evt.id} className="widget-row">
              <div className="widget-row-main">
                <p className="widget-row-title">
                  <span className={`email-event-tag email-event-${evt.event_type.toLowerCase()}`}>
                    {EVENT_LABEL[evt.event_type] ?? evt.event_type}
                  </span>{" "}
                  <Link href={`/contacts/${evt.contact_id}`}>
                    {evt.contact_name}
                  </Link>
                </p>
                <p className="widget-row-meta muted small">
                  {evt.subject ? `${evt.subject} · ` : null}
                  {formatTime(evt.occurred_at)}
                </p>
              </div>
            </li>
          ))}
        </ul>
      )}
    </article>
  );
}
