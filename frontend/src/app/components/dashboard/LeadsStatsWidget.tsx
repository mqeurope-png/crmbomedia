"use client";

import { TrendingUp } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  getDashboardLeadsStats,
  type LeadsStats,
  type LeadsStatsBucket,
  type LeadsStatsRange,
} from "../../lib/dashboardApi";
import { extractErrorMessage } from "../../lib/errors";

const RANGE_OPTIONS: Array<[LeadsStatsRange, string]> = [
  ["7d", "7 días"],
  ["30d", "30 días"],
  ["90d", "90 días"],
];

const BUCKET_OPTIONS: Array<[LeadsStatsBucket, string]> = [
  ["day", "Día"],
  ["week", "Semana"],
  ["month", "Mes"],
];

export function LeadsStatsWidget() {
  const [range, setRange] = useState<LeadsStatsRange>("30d");
  const [bucket, setBucket] = useState<LeadsStatsBucket>("day");
  const [stats, setStats] = useState<LeadsStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    getDashboardLeadsStats(range, bucket)
      .then(setStats)
      .catch((err) =>
        setError(extractErrorMessage(err, "No se pudieron cargar las estadísticas.")),
      )
      .finally(() => setLoading(false));
  }, [range, bucket]);

  const chartData = useMemo(
    () => stats?.series.map((s) => ({ name: s.bucket, leads: s.count })) ?? [],
    [stats],
  );

  return (
    <article className="card widget widget-stats">
      <header className="section-title">
        <h2>
          <TrendingUp size={14} aria-hidden /> Estadísticas de leads
        </h2>
        <div className="widget-toolbar">
          <select
            value={range}
            onChange={(e) => setRange(e.target.value as LeadsStatsRange)}
            aria-label="Rango"
          >
            {RANGE_OPTIONS.map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
          <select
            value={bucket}
            onChange={(e) => setBucket(e.target.value as LeadsStatsBucket)}
            aria-label="Agregado"
          >
            {BUCKET_OPTIONS.map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
        </div>
      </header>
      {error ? <p className="form-error">{error}</p> : null}
      {loading || !stats ? (
        <p className="muted small">Cargando…</p>
      ) : (
        <>
          <ul className="widget-kpis">
            <li>
              <strong>{stats.totals.leads_current}</strong>
              <span className="muted small">Leads en este período</span>
            </li>
            <li>
              <strong>
                {stats.totals.delta_pct === null
                  ? "—"
                  : `${stats.totals.delta_pct > 0 ? "+" : ""}${stats.totals.delta_pct}%`}
              </strong>
              <span className="muted small">vs período anterior</span>
            </li>
            <li>
              <strong>{stats.totals.qualified_pct}%</strong>
              <span className="muted small">Qualified</span>
            </li>
            <li>
              <strong>{stats.totals.closed_won_pct}%</strong>
              <span className="muted small">Cerrados ganados</span>
            </li>
          </ul>
          <div className="widget-chart">
            <ResponsiveContainer width="100%" height={180}>
              <BarChart data={chartData}>
                <CartesianGrid strokeDasharray="3 3" stroke="#eef0f5" />
                <XAxis dataKey="name" tick={{ fontSize: 11 }} />
                <YAxis allowDecimals={false} tick={{ fontSize: 11 }} width={28} />
                <Tooltip
                  contentStyle={{ fontSize: 12 }}
                  cursor={{ fill: "#f1f4fb" }}
                />
                <Bar dataKey="leads" fill="#3357ab" radius={[3, 3, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </>
      )}
    </article>
  );
}
