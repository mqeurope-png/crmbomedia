"use client";

/**
 * Pestaña "Resumen" de la ficha contacto BoHub (PR-D). 4 cards:
 *
 *   - Actividad reciente: timeline último 5 con icono color por tipo.
 *   - Engagement por email (30 días): aperturas / clics / respuestas.
 *     Datos sacados de los `activity_events` del contacto en tipo
 *     `email.*`. Sin endpoint dedicado todavía, el card es híbrido.
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
import type { ActivityEvent } from "../../lib/api";

type Props = {
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

function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString("es-ES", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function countEvents(events: ActivityEvent[], types: string[]): number {
  return events.filter((e) => types.includes(e.event_type)).length;
}

export function ContactSummaryTab({ events, onSeeAllActivity }: Props) {
  const recent = events.slice(0, 5);
  const opens = countEvents(events, ["EMAIL_OPENED"]);
  const clicks = countEvents(events, ["EMAIL_CLICKED"]);
  const replies = countEvents(events, ["email.reply_received"]);

  return (
    <div className="contact-summary">
      <article className="card contact-summary-card">
        <header className="contact-summary-card-header">
          <h3>Actividad reciente</h3>
        </header>
        {recent.length === 0 ? (
          <p className="muted small">Sin actividad reciente.</p>
        ) : (
          <ul className="contact-summary-timeline">
            {recent.map((e) => {
              const b = bucketFor(e.event_type);
              return (
                <li key={e.id} className="contact-summary-timeline-item">
                  <span
                    className={`contact-summary-timeline-icon is-${b.tone}`}
                    aria-hidden
                  >
                    {b.icon}
                  </span>
                  <div className="contact-summary-timeline-body">
                    <p className="contact-summary-timeline-title">
                      <strong>{e.subject || b.label}</strong>
                    </p>
                    {e.body ? (
                      <p className="muted small contact-summary-timeline-desc">
                        {e.body}
                      </p>
                    ) : null}
                    <p className="muted small">{formatDateTime(e.occurred_at)}</p>
                  </div>
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
    </div>
  );
}
