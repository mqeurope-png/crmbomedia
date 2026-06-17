"use client";

/**
 * "📊 Estadísticas campañas por user" — PR-E2 reemplaza al placeholder
 * "Oportunidades calientes". Ranking del equipo por leads engaged en
 * campañas Brevo. Tira de `/api/dashboard/user-campaign-stats`.
 */
import { Trophy } from "lucide-react";
import { useEffect, useState } from "react";
import {
  getDashboardUserCampaignStats,
  type DashboardPeriod,
  type UserCampaignStat,
} from "../../lib/dashboardApi";

const PERIOD_OPTIONS: ReadonlyArray<[DashboardPeriod, string]> = [
  ["7d", "7 días"],
  ["14d", "14 días"],
  ["30d", "30 días"],
];

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
  const [period, setPeriod] = useState<DashboardPeriod>("30d");
  const [rows, setRows] = useState<UserCampaignStat[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getDashboardUserCampaignStats(period, 5)
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
  }, [period]);

  return (
    <article className="card widget widget-user-campaign-stats">
      <header className="section-title">
        <h2>
          <Trophy size={14} aria-hidden /> Stats campañas por user
        </h2>
        <div
          className="widget-segment"
          role="radiogroup"
          aria-label="Rango temporal"
        >
          {PERIOD_OPTIONS.map(([value, label]) => (
            <button
              key={value}
              type="button"
              role="radio"
              aria-checked={period === value}
              className={`widget-segment-item${
                period === value ? " is-active" : ""
              }`}
              onClick={() => setPeriod(value)}
            >
              {label}
            </button>
          ))}
        </div>
      </header>
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
              <th>Leads</th>
              <th>CTR</th>
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
                <td>
                  <strong>{row.leads}</strong>
                </td>
                <td>
                  <span className="muted small">{row.conversion_pct}%</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </article>
  );
}
