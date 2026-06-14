"use client";

import {
  AlertTriangle,
  Ban,
  Eye,
  MousePointerClick,
  Send,
} from "lucide-react";
import type { EmailEvent } from "../../lib/emailTrackingApi";

function formatDateTime(value: string): string {
  return new Date(value).toLocaleString("es-ES", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

type BadgeSpec = {
  icon: React.ComponentType<{ size?: number; "aria-hidden"?: boolean }>;
  label: string;
  className: string;
};

/** Per-event-type visual: same icon + accent across the thread page,
 *  the contact emails section and the dashboard widget. */
const TYPE_SPEC: Record<EmailEvent["event_type"], BadgeSpec> = {
  sent: { icon: Send, label: "Enviado", className: "ev-sent" },
  delivered: { icon: Send, label: "Entregado", className: "ev-sent" },
  open: { icon: Eye, label: "Apertura", className: "ev-open" },
  click: {
    icon: MousePointerClick,
    label: "Clic",
    className: "ev-click",
  },
  bounce: { icon: AlertTriangle, label: "Bounce", className: "ev-bounce" },
  complaint: { icon: AlertTriangle, label: "Queja", className: "ev-bounce" },
  unsubscribe: { icon: Ban, label: "Baja", className: "ev-unsub" },
};

type Props = {
  events: EmailEvent[];
  /** When true, only render a one-line summary (counts, no list).
   *  Used by the contact card; the thread page uses the full view. */
  compact?: boolean;
};

export function EmailEventBadges({ events, compact = false }: Props) {
  // Count per type. `sent` was previously stripped from display
  // (assumed implicit), but a message with only a sent event then
  // rendered NOTHING — the operator couldn't tell whether the tracking
  // pipeline was alive at all. Keep the sent pill so the row always
  // shows something; opens / clicks etc. join it as they arrive.
  const counts: Partial<Record<EmailEvent["event_type"], number>> = {};
  const latest: Partial<Record<EmailEvent["event_type"], string>> = {};
  for (const e of events) {
    counts[e.event_type] = (counts[e.event_type] ?? 0) + 1;
    latest[e.event_type] = e.occurred_at;
  }

  const ordered: EmailEvent["event_type"][] = [
    "sent",
    "open",
    "click",
    "bounce",
    "unsubscribe",
    "complaint",
  ];
  const visible = ordered.filter((t) => counts[t]);
  if (visible.length === 0 && !compact) return null;

  return (
    <div
      className={`ev-badges${compact ? " ev-badges-compact" : ""}`}
      aria-label="Eventos del mensaje"
    >
      {visible.map((type) => {
        const spec = TYPE_SPEC[type];
        const Icon = spec.icon;
        const count = counts[type] ?? 0;
        const last = latest[type];
        return (
          <span
            key={type}
            className={`ev-badge ${spec.className}`}
            title={last ? `Último: ${formatDateTime(last)}` : spec.label}
          >
            <Icon size={11} aria-hidden />
            <span>
              {spec.label}
              {count > 1 ? ` ×${count}` : ""}
            </span>
          </span>
        );
      })}
      {compact && visible.length === 0 ? (
        <span className="ev-badge ev-sent" title="Sin eventos">
          <Send size={11} aria-hidden />
          <span>Sin actividad</span>
        </span>
      ) : null}
    </div>
  );
}
