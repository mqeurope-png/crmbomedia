"use client";

import { useEffect, useState } from "react";
import {
  getContactActivityEvents,
  type ActivityEvent,
} from "../lib/api";

const EMAIL_EVENT_META: Record<string, { icon: string; label: string }> = {
  "email.queued": { icon: "⏳", label: "En cola" },
  "email.sent": { icon: "📤", label: "Enviado" },
  "email.delivered": { icon: "📬", label: "Entregado" },
  "email.opened": { icon: "📩", label: "Abierto" },
  "email.clicked": { icon: "🔗", label: "Click" },
  "email.bounced_hard": { icon: "⚠️", label: "Rebote duro" },
  "email.bounced_soft": { icon: "⚠️", label: "Rebote blando" },
  "email.unsubscribed": { icon: "🚫", label: "Baja" },
  "email.spam_complaint": { icon: "🛑", label: "Spam" },
};

/**
 * Webhook-fed email activity (Brevo) for one contact, newest first.
 * Renders nothing while loading and a friendly empty state when the
 * contact has no email events yet.
 */
export function ContactEmailActivity({ contactId }: { contactId: string }) {
  const [events, setEvents] = useState<ActivityEvent[] | null>(null);

  useEffect(() => {
    getContactActivityEvents(contactId, { limit: 200 })
      .then((page) =>
        setEvents(
          page.items
            .filter((event) => event.event_type.startsWith("email."))
            .sort(
              (a, b) =>
                new Date(b.occurred_at).getTime() -
                new Date(a.occurred_at).getTime(),
            )
            .slice(0, 50),
        ),
      )
      .catch(() => setEvents([]));
  }, [contactId]);

  return (
    <article className="card">
      <h2>Actividad email</h2>
      {events === null ? (
        <p className="muted">Cargando…</p>
      ) : events.length === 0 ? (
        <p className="muted">
          Sin eventos de email todavía. Llegan automáticamente vía webhook de
          Brevo (entregas, aperturas, clicks, rebotes, bajas).
        </p>
      ) : (
        <ul className="email-activity-list">
          {events.map((event) => {
            const meta = EMAIL_EVENT_META[event.event_type] ?? {
              icon: "✉️",
              label: event.event_type,
            };
            return (
              <li key={event.id}>
                <span className="email-activity-icon" aria-hidden>
                  {meta.icon}
                </span>
                <div className="email-activity-body">
                  <strong>{meta.label}</strong>
                  {event.subject ? (
                    <span className="muted small"> · {event.subject}</span>
                  ) : null}
                  {event.event_type === "email.clicked" && event.body ? (
                    <span className="muted small"> · {event.body}</span>
                  ) : null}
                  <span className="muted small email-activity-date">
                    {new Date(event.occurred_at).toLocaleString("es-ES")}
                  </span>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </article>
  );
}
