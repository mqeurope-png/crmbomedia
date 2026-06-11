"use client";

import { Kanban } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { extractErrorMessage } from "../../lib/errors";
import {
  getDashboardPipelineSummary,
  type PipelineSummary,
} from "../../lib/dashboardApi";

export function PipelineSummaryWidget() {
  const [data, setData] = useState<PipelineSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getDashboardPipelineSummary()
      .then(setData)
      .catch((err) =>
        setError(extractErrorMessage(err, "No se pudo cargar el pipeline.")),
      )
      .finally(() => setLoading(false));
  }, []);

  return (
    <article className="card widget widget-pipeline">
      <header className="section-title">
        <h2>
          <Kanban size={14} aria-hidden /> Mi pipeline
        </h2>
        <Link href="/pipelines" className="small muted">
          Ver pipelines
        </Link>
      </header>
      {loading ? (
        <p className="muted small">Cargando…</p>
      ) : error ? (
        <p className="form-error">{error}</p>
      ) : data.length === 0 ? (
        <p className="muted small">No hay pipelines activos.</p>
      ) : (
        <div className="pipeline-summary-list">
          {data.map((p) => {
            const max = Math.max(1, ...p.stages.map((s) => s.count));
            return (
              <section key={p.pipeline_id} className="pipeline-summary-item">
                <h3 className="muted small">{p.pipeline_name}</h3>
                <ul className="pipeline-stage-bars">
                  {p.stages.map((stage) => (
                    <li key={stage.id} className="pipeline-stage-row">
                      <span className="pipeline-stage-label">{stage.name}</span>
                      <span
                        className="pipeline-stage-bar"
                        style={{
                          width: `${(stage.count / max) * 100}%`,
                          background: stage.color ?? "#6b86c0",
                        }}
                      />
                      <span className="pipeline-stage-count">{stage.count}</span>
                    </li>
                  ))}
                </ul>
              </section>
            );
          })}
        </div>
      )}
    </article>
  );
}
