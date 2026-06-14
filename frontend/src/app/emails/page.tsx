"use client";

import { Inbox } from "lucide-react";
import { EmailTrackingStatsWidget } from "../components/dashboard/EmailTrackingStatsWidget";

/** Right-pane placeholder shown when no thread is open. The
 *  v2.4 layout keeps sidebar + thread list mounted via
 *  `layout.tsx`; this page only fills the rightmost column. */
export default function EmailsPage() {
  return (
    <div className="email-thread-empty">
      <div className="email-thread-empty-stats">
        <EmailTrackingStatsWidget />
      </div>
      <div className="email-thread-empty-cta">
        <Inbox size={28} aria-hidden />
        <h2>Selecciona un hilo</h2>
        <p className="muted">
          Pulsa cualquier conversación de la lista para verla aquí.
        </p>
      </div>
    </div>
  );
}
