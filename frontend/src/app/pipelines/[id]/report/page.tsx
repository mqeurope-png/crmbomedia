"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { PageHeader } from "../../../components/PageHeader";
import { ErrorState } from "../../../components/ErrorState";
import { PipelineReportChart } from "../../../components/PipelineReportChart";
import {
  pipelineReport,
  pipelineStalledContacts,
  type PipelineReport,
  type StalledContactRow,
} from "../../../lib/api";
import { extractErrorMessage } from "../../../lib/errors";

function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  if (seconds < 60) return `${Math.round(seconds)} s`;
  const minutes = seconds / 60;
  if (minutes < 60) return `${Math.round(minutes)} min`;
  const hours = minutes / 60;
  if (hours < 48) return `${hours.toFixed(1)} h`;
  return `${(hours / 24).toFixed(1)} d`;
}

export default function PipelineReportPage() {
  const params = useParams<{ id: string }>();
  const [report, setReport] = useState<PipelineReport | null>(null);
  const [stalled, setStalled] = useState<StalledContactRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const refresh = useCallback(async () => {
    setIsLoading(true);
    try {
      const [reportResponse, stalledResponse] = await Promise.all([
        pipelineReport(params.id),
        pipelineStalledContacts(params.id),
      ]);
      setReport(reportResponse);
      setStalled(stalledResponse);
      setError(null);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo cargar el reporte."));
    } finally {
      setIsLoading(false);
    }
  }, [params.id]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const totalsRow = useMemo(() => {
    if (!report) return null;
    const open = report.total_contacts - report.won_count - report.lost_count;
    return { open, won: report.won_count, lost: report.lost_count };
  }, [report]);

  if (isLoading && !report) {
    return (
      <main className="shell shell-wide">
        <p className="muted">Cargando reporte…</p>
      </main>
    );
  }
  if (error || !report) {
    return (
      <main className="shell narrow">
        <PageHeader
          title="Reporte"
          crumbs={[{ label: "Pipelines", href: "/pipelines" }]}
        />
        <ErrorState
          title="No se pudo cargar el reporte"
          message={error ?? "Reporte no disponible"}
        />
      </main>
    );
  }

  return (
    <main className="shell shell-wide">
      <PageHeader
        title={report.pipeline_name}
        eyebrow="Reporte"
        description="Vista agregada de los contactos del pipeline + métricas de tiempo y conversión por etapa."
        crumbs={[
          { label: "Pipelines", href: "/pipelines" },
          { label: report.pipeline_name, href: `/pipelines/${report.pipeline_id}` },
          { label: "Reporte" },
        ]}
      />

      <section className="stats-grid" aria-label="Resumen">
        <article className="stat-card">
          <span>{report.total_contacts}</span>
          <p>Contactos en pipeline</p>
        </article>
        <article className="stat-card">
          <span>{totalsRow?.open ?? 0}</span>
          <p>Activos</p>
        </article>
        <article className="stat-card">
          <span className="success-text">{report.won_count}</span>
          <p>Ganados</p>
        </article>
        <article className="stat-card">
          <span className="danger-text">{report.lost_count}</span>
          <p>Perdidos</p>
        </article>
      </section>

      <section className="panel">
        <h2>Distribución por etapa</h2>
        <PipelineReportChart metrics={report.metrics} />
      </section>

      <section className="panel">
        <h2>Métricas por etapa</h2>
        <div className="table-wrapper">
          <table className="data-table">
            <thead>
              <tr>
                <th>Etapa</th>
                <th>Contactos</th>
                <th>Tiempo medio</th>
                <th>Conversión a siguiente</th>
                <th>Estancados</th>
              </tr>
            </thead>
            <tbody>
              {report.metrics.map((metric) => (
                <tr key={metric.stage_id}>
                  <td>{metric.stage_name}</td>
                  <td>{metric.contact_count}</td>
                  <td>{formatDuration(metric.avg_seconds_in_stage)}</td>
                  <td>
                    {metric.conversion_to_next != null
                      ? `${Math.round(metric.conversion_to_next * 100)} %`
                      : "—"}
                  </td>
                  <td>
                    {metric.stalled_count > 0 ? (
                      <span className="danger-text">{metric.stalled_count}</span>
                    ) : (
                      "—"
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel">
        <h2>Contactos estancados</h2>
        {stalled.length === 0 ? (
          <p className="muted">
            Sin contactos por encima del SLA. ✨
          </p>
        ) : (
          <div className="table-wrapper">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Contacto</th>
                  <th>Email</th>
                  <th>Etapa</th>
                  <th>Días en etapa</th>
                  <th>SLA</th>
                  <th>Días de retraso</th>
                </tr>
              </thead>
              <tbody>
                {stalled.map((row) => {
                  const name =
                    [row.first_name, row.last_name].filter(Boolean).join(" ") ||
                    "(Sin nombre)";
                  return (
                    <tr key={row.assignment_id}>
                      <td>
                        <Link href={`/contacts/${row.contact_id}`}>{name}</Link>
                      </td>
                      <td>{row.email}</td>
                      <td>{row.stage_name}</td>
                      <td>{row.days_in_stage}</td>
                      <td>{row.target_days}</td>
                      <td>
                        <span className="danger-text">+{row.overdue_days}</span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </main>
  );
}
