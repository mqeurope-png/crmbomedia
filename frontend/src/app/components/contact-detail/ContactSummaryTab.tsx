"use client";

/**
 * Pestaña "Resumen" de la ficha contacto BoHub (PR-D). 4 cards:
 *
 *   - Actividad reciente: timeline último 5 con icono color por tipo.
 *   - Engagement por email (30 días): aperturas / clics / respuestas.
 *     PR-Fix-Widget-Engagement-Email: fuente de datos cambió de
 *     `activity_events` (donde se quedaba en 0) a
 *     `GET /api/contacts/{id}/engagement-stats`, que lee de
 *     `email_message_events` igual que la lista global `/emails`.
 *   - Oportunidades vinculadas: mini-tabla. Placeholder hasta que
 *     ContactPipelinesSection se "headless-ifique".
 *   - Incidencias recientes: placeholder hasta integración Freshdesk.
 */
import {
  ArrowUpRight,
  ChartLine,
  CheckCircle,
  Mail,
  MessageSquare,
  MousePointerClick,
  Phone,
  StickyNote,
} from "lucide-react";
import { useEffect, useState } from "react";
import {
  getContactEngagementStats,
  type ActivityEvent,
  type EngagementStats,
} from "../../lib/api";
import { formatBackendDateTime, formatRelative } from "../../lib/dates";

type Props = {
  contactId: string;
  events: ActivityEvent[];
  onSeeAllActivity?: () => void;
};

type Bucket = {
  icon: React.ReactNode;
  tone: "green" | "blue" | "purple" | "amber";
  label: string;
};

const EVENT_BUCKETS: Record<string, Bucket> = {
  "email.sent_from_crm": {
    icon: <Mail size={14} aria-hidden />,
    tone: "blue",
    label: "Correo enviado",
  },
  "email.reply_received": {
    icon: <Mail size={14} aria-hidden />,
    tone: "green",
    label: "Correo recibido",
  },
  EMAIL_SENT: {
    icon: <Mail size={14} aria-hidden />,
    tone: "blue",
    label: "Correo enviado",
  },
  EMAIL_OPENED: {
    icon: <Mail size={14} aria-hidden />,
    tone: "blue",
    label: "Correo abierto",
  },
  CALL_LOG: {
    icon: <Phone size={14} aria-hidden />,
    tone: "green",
    label: "Llamada registrada",
  },
  NOTE: {
    icon: <StickyNote size={14} aria-hidden />,
    tone: "amber",
    label: "Nota añadida",
  },
  TASK_COMPLETED: {
    icon: <CheckCircle size={14} aria-hidden />,
    tone: "green",
    label: "Tarea completada",
  },
  "task.completed": {
    icon: <CheckCircle size={14} aria-hidden />,
    tone: "green",
    label: "Tarea completada",
  },
  "task.created": {
    icon: <CheckCircle size={14} aria-hidden />,
    tone: "amber",
    label: "Tarea creada",
  },
};

function bucketFor(eventType: string): Bucket {
  return (
    EVENT_BUCKETS[eventType] ?? {
      icon: <ChartLine size={14} aria-hidden />,
      tone: "purple",
      label: eventType,
    }
  );
}

// PR-Timezone-Fix. Delegamos en `lib/dates.ts` para que el parsing
// trate timestamps sin offset como UTC en lugar de hora local.
const formatDateTime = (value: string | null | undefined) =>
  formatBackendDateTime(value);
const relativeTime = (value: string | null | undefined) =>
  value ? formatRelative(value) : "—";

export function ContactSummaryTab({
  contactId,
  events,
  onSeeAllActivity,
}: Props) {
  const recent = events.slice(0, 5);
  // PR-Fix-Widget-Engagement-Email. Stats reales desde el endpoint
  // que lee `email_message_events` (no del prop `events`, que cuenta
  // sobre `activity_events` y siempre quedaba en 0 para emails con
  // tracking de aperturas — caso confirmado por Bart con TESTT 2121).
  const [stats, setStats] = useState<EngagementStats>({
    opens: 0,
    clicks: 0,
    replies: 0,
  });
  useEffect(() => {
    let cancelled = false;
    getContactEngagementStats(contactId, 30)
      .then((data) => {
        if (!cancelled) setStats(data);
      })
      .catch(() => {
        // Soft-fail: mantenemos los ceros y no rompemos la pestaña
        // si el endpoint cae.
      });
    return () => {
      cancelled = true;
    };
  }, [contactId]);
  const { opens, clicks, replies } = stats;

  return (
    <div className="contact-summary">
      <article className="card contact-summary-card">
        <header className="contact-summary-card-header">
          <h3>Actividad reciente</h3>
        </header>
        {recent.length === 0 ? (
          <p className="muted small">Sin actividad reciente.</p>
        ) : (
          <ul className="contact-summary-timeline contact-summary-timeline-dense">
            {recent.map((e) => {
              const b = bucketFor(e.event_type);
              const title = e.subject || b.label;
              return (
                <li
                  key={e.id}
                  className="contact-summary-timeline-item"
                  title={
                    e.body
                      ? `${title} — ${e.body} (${formatDateTime(e.occurred_at)})`
                      : `${title} (${formatDateTime(e.occurred_at)})`
                  }
                >
                  <span
                    className={`contact-summary-timeline-icon is-${b.tone}`}
                    aria-hidden
                  >
                    {b.icon}
                  </span>
                  <span className="contact-summary-timeline-title">
                    <strong>{title}</strong>
                  </span>
                  <span className="muted small contact-summary-timeline-time">
                    {relativeTime(e.occurred_at)}
                  </span>
                </li>
              );
            })}
          </ul>
        )}
        {onSeeAllActivity ? (
          <button
            type="button"
            className="contact-summary-link"
            onClick={onSeeAllActivity}
          >
            Ver toda la actividad <ArrowUpRight size={12} aria-hidden />
          </button>
        ) : null}
      </article>

      <article className="card contact-summary-card">
        <header className="contact-summary-card-header">
          <h3>Engagement por email (últimos 30 días)</h3>
        </header>
        <div className="contact-summary-engagement">
          <div className="contact-summary-engagement-item">
            <span className="contact-summary-engagement-label">Aperturas</span>
            <span className="contact-summary-engagement-value">{opens}</span>
            <span className="contact-summary-engagement-icon" aria-hidden>
              <Mail size={14} />
            </span>
          </div>
          <div className="contact-summary-engagement-item">
            <span className="contact-summary-engagement-label">Clics</span>
            <span className="contact-summary-engagement-value">{clicks}</span>
            <span className="contact-summary-engagement-icon" aria-hidden>
              <MousePointerClick size={14} />
            </span>
          </div>
          <div className="contact-summary-engagement-item">
            <span className="contact-summary-engagement-label">Respuestas</span>
            <span className="contact-summary-engagement-value">{replies}</span>
            <span className="contact-summary-engagement-icon" aria-hidden>
              <MessageSquare size={14} />
            </span>
          </div>
        </div>
      </article>

      {/* PR-Ficha-Cleanup: Oportunidades vinculadas + Incidencias se
          mueven a un componente independiente que el page renderiza al
          FINAL del grid, después de los cards con datos reales. */}
    </div>
  );
}

/** PR-Ficha-Cleanup. Cards placeholder que vivían inline en la pestaña
 *  Resumen. Bart pidió moverlas al final del grid porque ocupaban
 *  posición prime sin aportar datos. La página las monta tras los
 *  cards "Tags", "Notas recientes", etc. */
export function ContactSummaryPlaceholderCards() {
  return (
    <>
      <article className="card contact-summary-card">
        <header className="contact-summary-card-header">
          <h3>Oportunidades vinculadas</h3>
        </header>
        <p className="muted small">
          Próximamente — vista resumida del pipeline. Consulta la pestaña{" "}
          <em>Oportunidades</em> para ver el detalle.
        </p>
      </article>

      <article className="card contact-summary-card">
        <header className="contact-summary-card-header">
          <h3>Incidencias recientes</h3>
        </header>
        <p className="muted small">
          Sin incidencias. Integración Freshdesk pendiente (módulo Soporte
          en desarrollo).
        </p>
      </article>
    </>
  );
}
