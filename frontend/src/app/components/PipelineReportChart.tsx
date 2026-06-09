"use client";

import type { PipelineStageMetric } from "../lib/api";

type Props = {
  metrics: PipelineStageMetric[];
};

/**
 * Tiny inline SVG bar chart for the pipeline report. Renders one bar
 * per stage scaled to the max `contact_count` on the dataset; labels
 * sit below each bar and the count + conversion% appear above.
 *
 * We hand-rolled this instead of pulling in Recharts / Chart.js
 * because the chart is 100% static per render and there are at most
 * ~15 bars per pipeline. The bundle gain (>40 kB gz) wasn't worth it
 * for an admin-only screen.
 */
export function PipelineReportChart({ metrics }: Props) {
  if (metrics.length === 0) {
    return <p className="muted">Sin métricas: el pipeline no tiene etapas.</p>;
  }
  const max = Math.max(1, ...metrics.map((m) => m.contact_count));
  const barWidth = 56;
  const gap = 18;
  const chartHeight = 200;
  const labelHeight = 56;
  const padding = 32;
  const innerWidth = metrics.length * barWidth + (metrics.length - 1) * gap;
  const width = innerWidth + padding * 2;
  const height = chartHeight + labelHeight + padding;

  return (
    <div className="pipeline-chart-wrapper">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label="Distribución de contactos por etapa"
        className="pipeline-chart"
      >
        {metrics.map((metric, index) => {
          const x = padding + index * (barWidth + gap);
          const ratio = metric.contact_count / max;
          const barHeight = Math.max(2, ratio * chartHeight);
          const y = padding + (chartHeight - barHeight);
          return (
            <g key={metric.stage_id}>
              <text
                x={x + barWidth / 2}
                y={y - 6}
                textAnchor="middle"
                className="pipeline-chart-count"
              >
                {metric.contact_count}
              </text>
              <rect
                x={x}
                y={y}
                width={barWidth}
                height={barHeight}
                rx={6}
                className={`pipeline-chart-bar${
                  metric.stalled_count > 0 ? " is-stale" : ""
                }`}
              />
              {metric.conversion_to_next != null ? (
                <text
                  x={x + barWidth / 2}
                  y={padding + chartHeight + 18}
                  textAnchor="middle"
                  className="pipeline-chart-conversion"
                >
                  ↪ {Math.round(metric.conversion_to_next * 100)}%
                </text>
              ) : null}
              <text
                x={x + barWidth / 2}
                y={padding + chartHeight + 38}
                textAnchor="middle"
                className="pipeline-chart-label"
              >
                {metric.stage_name}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}
