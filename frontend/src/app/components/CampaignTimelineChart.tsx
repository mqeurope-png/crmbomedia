"use client";

/**
 * Hand-rolled SVG line/bar chart for opens + clicks per day. Same
 * no-dependency rationale as `PipelineReportChart`: an admin screen
 * with ≤60 data points doesn't justify 40 kB of charting library.
 */
type Point = { day: string; opened: number; clicked: number };

const WIDTH = 720;
const HEIGHT = 200;
const PADDING = 28;

export function CampaignTimelineChart({ points }: { points: Point[] }) {
  if (points.length === 0) return null;
  const max = Math.max(
    1,
    ...points.map((point) => Math.max(point.opened, point.clicked)),
  );
  const innerWidth = WIDTH - PADDING * 2;
  const innerHeight = HEIGHT - PADDING * 2;
  const step = innerWidth / Math.max(points.length, 1);
  const barWidth = Math.min(18, step * 0.35);

  function y(value: number): number {
    return PADDING + innerHeight - (value / max) * innerHeight;
  }

  return (
    <div className="campaign-chart-wrapper">
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        role="img"
        aria-label="Aperturas y clicks por día"
        className="campaign-chart"
      >
        {points.map((point, index) => {
          const xBase = PADDING + index * step + step / 2;
          return (
            <g key={point.day}>
              <rect
                x={xBase - barWidth - 1}
                y={y(point.opened)}
                width={barWidth}
                height={PADDING + innerHeight - y(point.opened)}
                className="campaign-chart-open"
              >
                <title>{`${point.day}: ${point.opened} aperturas`}</title>
              </rect>
              <rect
                x={xBase + 1}
                y={y(point.clicked)}
                width={barWidth}
                height={PADDING + innerHeight - y(point.clicked)}
                className="campaign-chart-click"
              >
                <title>{`${point.day}: ${point.clicked} clicks`}</title>
              </rect>
              {points.length <= 14 || index % 2 === 0 ? (
                <text
                  x={xBase}
                  y={HEIGHT - 8}
                  textAnchor="middle"
                  className="campaign-chart-label"
                >
                  {point.day.slice(5)}
                </text>
              ) : null}
            </g>
          );
        })}
        <text x={PADDING} y={14} className="campaign-chart-legend">
          ■ Aperturas
        </text>
        <text x={PADDING + 90} y={14} className="campaign-chart-legend-click">
          ■ Clicks
        </text>
      </svg>
    </div>
  );
}
