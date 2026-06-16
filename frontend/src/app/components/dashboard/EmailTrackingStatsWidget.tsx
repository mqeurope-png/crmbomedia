"use client";

import {
  AlertTriangle,
  Ban,
  Eye,
  MousePointerClick,
  Send,
} from "lucide-react";
import { useEffect, useState } from "react";
import {
  getEmailStats,
  type EmailStats,
} from "../../lib/emailTrackingApi";
import { extractErrorMessage } from "../../lib/errors";

type Props = {
  /** QoL hotfix — refleja el toggle de la lista de threads para que
   *  el widget arriba muestre los mismos counters que la lista de
   *  abajo. Default `mine`. */
  scope?: "mine" | "team";
  teamUserId?: string;
};

/** Aggregated tracking counters. Pre-QoL2, el widget ignoraba el
 *  toggle Mías/Equipo y siempre llamaba a `/api/emails/stats` sin
 *  scope — manager+ veía contadores globales fijos en `/emails`
 *  aunque la lista de threads de abajo estuviera filtrada a las
 *  suyas. Ahora el caller propaga `scope` + `teamUserId` y el widget
 *  refetch al cambiar. */
export function EmailTrackingStatsWidget({ scope, teamUserId }: Props = {}) {
  const [stats, setStats] = useState<EmailStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    getEmailStats(30, { scope, teamUserId })
      .then(setStats)
      .catch((err) =>
        setError(
          extractErrorMessage(err, "No se pudieron cargar las estadísticas."),
        ),
      )
      .finally(() => setLoading(false));
  }, [scope, teamUserId]);

  function rate(numer: number | undefined, denom: number | undefined) {
    if (!denom || denom === 0 || numer === undefined) return null;
    const pct = Math.round((numer / denom) * 100);
    return `${pct}%`;
  }

  return (
    <article className="card widget widget-emailstats">
      <header className="section-title">
        <h2>
          <Send size={14} aria-hidden /> Tracking de email (30 días)
        </h2>
      </header>
      {error ? <p className="form-error">{error}</p> : null}
      {loading || !stats ? (
        <p className="muted small">Cargando…</p>
      ) : (
        <ul className="email-stats-grid">
          <li className="email-stats-cell email-stats-sent">
            <Send size={14} aria-hidden />
            <span className="email-stats-val">{stats.sent}</span>
            <span className="muted small">enviados</span>
          </li>
          <li className="email-stats-cell email-stats-open">
            <Eye size={14} aria-hidden />
            <span className="email-stats-val">{stats.opened}</span>
            <span className="muted small">
              abiertos
              {rate(stats.opened, stats.sent)
                ? ` · ${rate(stats.opened, stats.sent)}`
                : ""}
            </span>
          </li>
          <li className="email-stats-cell email-stats-click">
            <MousePointerClick size={14} aria-hidden />
            <span className="email-stats-val">{stats.clicked}</span>
            <span className="muted small">
              clics
              {rate(stats.clicked, stats.sent)
                ? ` · ${rate(stats.clicked, stats.sent)}`
                : ""}
            </span>
          </li>
          <li className="email-stats-cell email-stats-unsub">
            <Ban size={14} aria-hidden />
            <span className="email-stats-val">{stats.unsubscribed}</span>
            <span className="muted small">bajas</span>
          </li>
          {stats.bounced > 0 ? (
            <li className="email-stats-cell email-stats-bounce">
              <AlertTriangle size={14} aria-hidden />
              <span className="email-stats-val">{stats.bounced}</span>
              <span className="muted small">bounces</span>
            </li>
          ) : null}
        </ul>
      )}
    </article>
  );
}
