"use client";

import { ArrowUpRight, Layers } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { listContactPipelines, type ContactPipelineSummary } from "../../lib/api";
import { extractErrorMessage } from "../../lib/errors";

type Props = {
  contactId: string;
  /** Lleva a la pestaña «Pipelines» (cambiar de etapa, añadir, sacar). */
  onSeeAll?: () => void;
};

function dias(n: number): string {
  return n === 1 ? "1 día" : `${n} días`;
}

/** ¿Se ha pasado del plazo de la etapa? El servidor lo manda (`is_overdue`);
 *  si no viene, se calcula con el plazo y los días en la etapa. */
export function fueraDePlazo(row: ContactPipelineSummary): boolean {
  if (typeof row.is_overdue === "boolean") return row.is_overdue;
  return typeof row.target_days === "number" && row.target_days > 0
    && row.days_in_stage > row.target_days;
}

/** Recuadro «Pipelines vinculados» del Resumen, con datos de verdad: el
 *  pipeline, la etapa actual, los días que lleva en ella, el plazo de la
 *  etapa y si se ha pasado. Sustituye al placeholder «Próximamente». */
export function ContactPipelinesSummaryCard({ contactId, onSeeAll }: Props) {
  const [rows, setRows] = useState<ContactPipelineSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let vivo = true;
    listContactPipelines(contactId)
      .then((r) => {
        if (vivo) setRows(r);
      })
      .catch((e) => {
        if (vivo) setError(extractErrorMessage(e, "No se pudieron cargar los pipelines."));
      })
      .finally(() => {
        if (vivo) setLoading(false);
      });
    return () => {
      vivo = false;
    };
  }, [contactId]);

  return (
    <article className="card contact-summary-card">
      <header className="contact-summary-card-header">
        <h3>
          <Layers size={14} aria-hidden /> Pipelines vinculados
        </h3>
      </header>
      {loading ? (
        <p className="muted small">Cargando…</p>
      ) : error ? (
        <p className="form-error">{error}</p>
      ) : rows.length === 0 ? (
        <p className="muted small">El contacto no está en ningún pipeline.</p>
      ) : (
        <ul className="contact-pipelines-summary">
          {rows.map((row) => {
            const pasado = !row.is_won && !row.is_lost && fueraDePlazo(row);
            return (
              <li
                key={row.assignment_id}
                className={`contact-pipelines-summary-row${pasado ? " is-overdue" : ""}`}
              >
                <div className="contact-pipelines-summary-main">
                  <Link href={`/pipelines/${row.pipeline_id}`}>
                    <strong>{row.pipeline_name}</strong>
                  </Link>
                  <span className="contact-pipelines-summary-stage">{row.stage_name}</span>
                  {row.is_won ? (
                    <span className="badge active">Ganado</span>
                  ) : row.is_lost ? (
                    <span className="badge muted">Perdido</span>
                  ) : pasado ? (
                    <span className="badge bad">Fuera de plazo</span>
                  ) : null}
                </div>
                <p className="muted small">
                  {dias(row.days_in_stage)} en la etapa
                  {typeof row.target_days === "number" && row.target_days > 0
                    ? ` · plazo ${dias(row.target_days)}`
                    : " · sin plazo"}
                </p>
              </li>
            );
          })}
        </ul>
      )}
      {onSeeAll ? (
        <button type="button" className="contact-summary-link" onClick={onSeeAll}>
          Ver pipelines <ArrowUpRight size={12} aria-hidden />
        </button>
      ) : null}
    </article>
  );
}
