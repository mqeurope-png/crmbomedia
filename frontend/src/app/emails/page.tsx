"use client";

import { Inbox } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { EmailTrackingStatsWidget } from "../components/dashboard/EmailTrackingStatsWidget";

/** Right-pane placeholder shown when no thread is open. The
 *  v2.4 layout keeps sidebar + thread list mounted via
 *  `layout.tsx`; this page only fills the rightmost column.
 *
 *  QoL2 hotfix — el widget de tracking lee `scope` + `team_user_id`
 *  de URL params para reflejar el mismo filtro que la lista de
 *  threads (el toggle vive en `EmailThreadList`). */
export default function EmailsPage() {
  const params = useSearchParams();
  const scope: "mine" | "team" =
    params?.get("scope") === "team" ? "team" : "mine";
  const teamUserId = params?.get("team_user_id") ?? undefined;
  return (
    <div className="email-thread-empty">
      <div className="email-thread-empty-stats">
        <EmailTrackingStatsWidget scope={scope} teamUserId={teamUserId} />
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
