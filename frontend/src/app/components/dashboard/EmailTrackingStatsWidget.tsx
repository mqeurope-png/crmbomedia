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

/** Aggregated tracking counters for the dashboard, mirroring the
 *  scope rules of `GET /api/emails/stats`: regular users see only
 *  events tied to their own sends; admins + managers see all. */
export function EmailTrackingStatsWidget() {
  const [stats, setStats] = useState<EmailStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    getEmailStats(30)
      .then(setStats)
      .catch((err) =>
        setError(
          extractErrorMessage(err, "No se pudieron cargar las estadísticas."),
        ),
      )
      .finally(() => setLoading(false));
  }, []);

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
