"use client";

/**
 * "📊 Estadísticas campañas por user" — PR-E2, métrica corregida en
 * PR-E3. Por cada user del equipo: cuántos de SUS contactos primary
 * recibieron / abrieron / clickearon campañas Brevo enviadas en el
 * período. Columnas: User / Recibieron / Abrieron / Clickearon / OR% /
 * CTR%.
 */
import { Trophy } from "lucide-react";
import { useEffect, useState } from "react";
import {
  getDashboardUserCampaignStats,
  type DashboardWindow,
  type UserCampaignStat,
} from "../../lib/dashboardApi";
import { usePersistentState } from "../../lib/usePersistentState";
import { PeriodSelector } from "./PeriodSelector";

function initials(full: string): string {
  return full
    .split(" ")
    .map((p) => p[0])
    .filter(Boolean)
    .slice(0, 2)
    .join("")
    .toUpperCase();
}

export function UserCampaignStatsWidget() {
  const [window_, setWindow] = usePersistentState<DashboardWindow>(
    "crmbomedia_dash:user_campaign_stats:period",
    { period: "30d" },
  );
  const [rows, setRows] = useState<UserCampaignStat[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getDashboardUserCampaignStats(window_, 5)
      .then((res) => {
        if (!cancelled) setRows(res);
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
    <article className="card widget widget-user-campaign-stats">
      <header className="section-title">
        <h2>
          <Trophy size={14} aria-hidden /> Stats campañas por user
        </h2>
        <PeriodSelector value={window_} onChange={setWindow} />
      </header>
      <div className="widget-scroll">
        {loading ? (
          <p className="muted small">Cargando…</p>
        ) : error ? (
          <p className="form-error">{error}</p>
        ) : rows.length === 0 ? (
          <div className="widget-empty">
            <p className="muted small">
              Sin engagement en campañas en este período.
            </p>
          </div>
        ) : (
          <table className="widget-table widget-leaderboard">
            <thead>
              <tr>
                <th>User</th>
                <th>Recib.</th>
                <th>Abrier.</th>
                <th>Click.</th>
                <th>OR%</th>
                <th>CTR%</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, idx) => (
                <tr key={row.user_id}>
                  <td>
                    <span className="widget-rank">{idx + 1}</span>
                    <span
                      className="widget-avatar"
                      aria-hidden
                      title={row.full_name}
                    >
                      {initials(row.full_name)}
                    </span>
                    <span className="widget-user-name">{row.full_name}</span>
                  </td>
                  <td>{row.received}</td>
                  <td>{row.opened}</td>
                  <td>
                    <strong>{row.clicked}</strong>
                  </td>
                  <td>
                    <span className="muted small">{row.open_rate}%</span>
                  </td>
                  <td>
                    <span className="muted small">{row.click_rate}%</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </article>
  );
}
