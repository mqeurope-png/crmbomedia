"use client";

import { CalendarClock, ExternalLink } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { extractErrorMessage } from "../../lib/errors";
import {
  getDashboardGoogleEvents,
  type GoogleCalendarEventsResponse,
} from "../../lib/dashboardApi";

function formatStart(value: string | null, allDay: boolean): string {
  if (!value) return "—";
  const d = new Date(value);
  if (allDay) {
    return d.toLocaleDateString("es-ES", {
      day: "2-digit",
      month: "short",
    });
  }
  return d.toLocaleString("es-ES", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function GoogleEventsWidget() {
  const [data, setData] = useState<GoogleCalendarEventsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getDashboardGoogleEvents()
      .then(setData)
      .catch((err) =>
        setError(extractErrorMessage(err, "No se pudieron cargar los eventos.")),
      )
      .finally(() => setLoading(false));
  }, []);

  return (
    <article className="card widget widget-gcal">
      <header className="section-title">
        <h2>
          <CalendarClock size={14} aria-hidden /> Próximos eventos
        </h2>
        {data?.calendar_summary ? (
          <span className="muted small">{data.calendar_summary}</span>
        ) : null}
      </header>
      {loading ? (
        <p className="muted small">Cargando…</p>
      ) : error ? (
        <p className="form-error">{error}</p>
      ) : !data?.connected ? (
        <div className="widget-empty">
          <p className="muted small">
            Conecta Google Calendar para ver aquí tus próximos eventos.
          </p>
          <Link href="/account" className="button small">
            Conectar Google
          </Link>
        </div>
      ) : data.events.length === 0 ? (
        <p className="muted small">Sin eventos en los próximos 14 días.</p>
      ) : (
        <ul className="widget-list">
          {data.events.map((evt) => (
            <li key={evt.id ?? evt.summary} className="widget-row">
              <div className="widget-row-main">
                <p className="widget-row-title">
                  {evt.summary || "(sin título)"}
                  {evt.html_link ? (
                    <a
                      href={evt.html_link}
                      target="_blank"
                      rel="noreferrer"
                      title="Abrir en Google Calendar"
                    >
                      <ExternalLink size={11} aria-hidden />
                    </a>
                  ) : null}
                </p>
                <p className="widget-row-meta muted small">
                  {formatStart(evt.start, evt.all_day)}
                </p>
              </div>
            </li>
          ))}
        </ul>
      )}
    </article>
  );
}
