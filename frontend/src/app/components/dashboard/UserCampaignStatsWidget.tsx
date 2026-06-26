"use client";

/**
 * "📊 Mis stats de campañas" — PR-E4. Reemplaza el leaderboard de
 * PR-E2/E3 por las métricas del current user (Bart quiere ver sus
 * números, no el ranking del equipo).
 *
 * Formato espejo del widget "Tracking de email": 5 mini-stats con
 * número grande + label. Selector de período mantenido + persistido.
 */
import {
  MousePointerClick,
  Send,
  Sparkles,
  Trophy,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import {
  getDashboardMyCampaignStats,
  type DashboardWindow,
  type MyCampaignStats,
} from "../../lib/dashboardApi";
import { usePersistentState } from "../../lib/usePersistentState";
import { PeriodSelector } from "./PeriodSelector";

export function UserCampaignStatsWidget() {
  const [window_, setWindow] = usePersistentState<DashboardWindow>(
    "crmbomedia_dash:my_campaign_stats:period",
    { period: "30d" },
  );
  const [stats, setStats] = useState<MyCampaignStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getDashboardMyCampaignStats(window_)
      .then((res) => {
        if (!cancelled) setStats(res);
      })
      .catch(() => {
        if (!cancelled)
          setError("No se pudieron cargar las stats de campañas.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [window_]);

  return (
    <article className="card widget widget-emailstats widget-my-campaign-stats">
      <header className="section-title">
        <h2>
          <Trophy size={14} aria-hidden /> Mis stats de campañas
        </h2>
        <PeriodSelector value={window_} onChange={setWindow} />
      </header>
      <div className="widget-scroll">
        {error ? <p className="form-error">{error}</p> : null}
        {loading || !stats ? (
          <p className="muted small">Cargando…</p>
        ) : (
          <ul className="email-stats-grid">
            {/* PR-Bugs-4-5amp-7-9. Las 3 cajas con KPI clicable
             * (recibieron/abrieron/clickearon) son Links a la página
             * dedicada `/dashboard/mis-stats/{kpi}` con la ventana
             * actual del widget propagada por query param. Las cajas
             * OR/CTR no llevan a una lista — son derivadas. */}
            <li className="email-stats-cell email-stats-sent">
              <Link
                href={`/dashboard/mis-stats/received?window=${encodeURIComponent(window_.period)}`}
                className="email-stats-link"
              >
                <Send size={14} aria-hidden />
                <span className="email-stats-val">{stats.received}</span>
                <span className="muted small">recibieron</span>
              </Link>
            </li>
            <li className="email-stats-cell email-stats-open">
              <Link
                href={`/dashboard/mis-stats/opened?window=${encodeURIComponent(window_.period)}`}
                className="email-stats-link"
              >
                <Sparkles size={14} aria-hidden />
                <span className="email-stats-val">{stats.opened}</span>
                <span className="muted small">abrieron</span>
              </Link>
            </li>
            <li className="email-stats-cell email-stats-click">
              <Link
                href={`/dashboard/mis-stats/clicked?window=${encodeURIComponent(window_.period)}`}
                className="email-stats-link"
              >
                <MousePointerClick size={14} aria-hidden />
                <span className="email-stats-val">{stats.clicked}</span>
                <span className="muted small">clickearon</span>
              </Link>
            </li>
            <li className="email-stats-cell email-stats-or">
              <span className="email-stats-val">{stats.open_rate}%</span>
              <span className="muted small">OR (abrieron/recibieron)</span>
            </li>
            <li className="email-stats-cell email-stats-ctr">
              <span className="email-stats-val">{stats.click_rate}%</span>
              <span className="muted small">CTR (clickearon/abrieron)</span>
            </li>
          </ul>
        )}
      </div>
    </article>
  );
}
