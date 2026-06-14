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
  /** Full event list — used by the thread page / contact timeline,
   *  which fetch per-message events and want the "last occurred"
   *  tooltip. */
  events?: EmailEvent[];
  /** Pre-aggregated counts — used by the inbox list, which gets a
   *  per-thread `{open: 2, click: 1}` map from the threads endpoint
   *  and never has the individual events. Exactly one of `events` /
   *  `counts` should be passed. */
  counts?: Record<string, number>;
  compact?: boolean;
};

// `sent` / `delivered` are intentionally absent — every message has a
// send, so the badge was pure noise (Bart asked for it gone
// everywhere). We only surface what happened AFTER delivery.
const ORDERED: EmailEvent["event_type"][] = [
  "open",
  "click",
  "bounce",
  "unsubscribe",
  "complaint",
];

export function EmailEventBadges({ events, counts, compact = false }: Props) {
  const tally: Partial<Record<EmailEvent["event_type"], number>> = {};
  const latest: Partial<Record<EmailEvent["event_type"], string>> = {};
  if (events) {
    for (const e of events) {
      tally[e.event_type] = (tally[e.event_type] ?? 0) + 1;
      latest[e.event_type] = e.occurred_at;
    }
  } else if (counts) {
    for (const [type, n] of Object.entries(counts)) {
      tally[type as EmailEvent["event_type"]] = n;
    }
  }

  const visible = ORDERED.filter((t) => tally[t]);
  if (visible.length === 0) return null;

  return (
    <div
      className={`ev-badges${compact ? " ev-badges-compact" : ""}`}
      aria-label="Eventos del email"
    >
      {visible.map((type) => {
        const spec = TYPE_SPEC[type];
        const Icon = spec.icon;
        const count = tally[type] ?? 0;
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
    </div>
  );
}
