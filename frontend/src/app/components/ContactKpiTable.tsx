"use client";

/**
 * PR-Bugs-4-5amp-7-9. Tabla compartida usada por las páginas KPI:
 *
 *   - `/dashboard/leads-prioritarios` (bug 5 PR #241 — original)
 *   - `/dashboard/mis-stats/{kpi}` (bug 4)
 *   - `/marketing/campaigns/{id}/{kpi}` (bug 5 ampliación)
 *
 * 6 columnas: Nombre (link a ficha) · Email · Última actividad ·
 * Etiquetas (chips, hasta 3 + "+N") · Lead score · Propietario.
 *
 * Datos vienen del backend en shape `PriorityLead` (ver
 * `lib/dashboardApi.ts`). El callsite indica el label de la columna
 * "Última actividad" — para las KPIs de campaña tiene más sentido
 * "Último evento", para mis-stats "Última interacción", etc. Pero
 * mantenemos un default razonable.
 */
import Link from "next/link";
import type { PriorityLead, PriorityLeadTag } from "../lib/dashboardApi";
import { parseBackendDate } from "../lib/dates";

const REASON_LABEL: Record<string, { label: string; tone: string }> = {
  recent: { label: "Recién creado", tone: "is-info" },
  assigned: { label: "Recién asignado", tone: "is-success" },
  active: { label: "Activo", tone: "is-warning" },
};

function relative(value: string | null | undefined): string {
  if (!value) return "—";
  const target = parseBackendDate(value);
  if (Number.isNaN(target.getTime())) return "—";
  const diff = Date.now() - target.getTime();
  const day = Math.floor(diff / 86_400_000);
  if (day === 0) return "hoy";
  if (day === 1) return "ayer";
  if (day < 7) return `hace ${day}d`;
  return target.toLocaleDateString("es-ES", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}

type Props = {
  rows: PriorityLead[];
  /** Texto de la 3ª columna. Default "Última actividad". */
  signalLabel?: string;
};

export function ContactKpiTable({
  rows,
  signalLabel = "Última actividad",
}: Props) {
  return (
    <section className="table-wrap">
      <table className="data-table">
        <thead>
          <tr>
            <th>Nombre</th>
            <th>Email</th>
            <th>{signalLabel}</th>
            <th>Etiquetas</th>
            <th>Lead score</th>
            <th>Propietario</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const name =
              [row.first_name, row.last_name].filter(Boolean).join(" ") ||
              row.email;
            const tags: PriorityLeadTag[] = row.tags ?? [];
            const visibleTags = tags.slice(0, 3);
            const extraTagsCount = tags.length - visibleTags.length;
            const reason = row.reason
              ? REASON_LABEL[row.reason] ?? {
                  label: row.reason,
                  tone: "is-muted",
                }
              : null;
            return (
              <tr key={row.id}>
                <td>
                  <Link href={`/contacts/${row.id}`}>{name}</Link>
                  {reason ? (
                    <div className="muted" style={{ fontSize: 11 }}>
                      <span className={`chip ${reason.tone}`}>
                        {reason.label}
                      </span>
                    </div>
                  ) : null}
                </td>
                <td className="muted">{row.email}</td>
                <td>{relative(row.signal_at)}</td>
                <td>
                  {visibleTags.length === 0 ? (
                    <span className="muted small">—</span>
                  ) : (
                    <div
                      style={{
                        display: "flex",
                        gap: 4,
                        flexWrap: "wrap",
                      }}
                    >
                      {visibleTags.map((t) => (
                        <span
                          key={t.id}
                          className="chip"
                          style={{
                            background: t.color ?? undefined,
                            fontSize: 11,
                          }}
                        >
                          {t.name}
                        </span>
                      ))}
                      {extraTagsCount > 0 ? (
                        <span className="muted small">
                          +{extraTagsCount}
                        </span>
                      ) : null}
                    </div>
                  )}
                </td>
                <td>
                  {row.lead_score != null ? (
                    row.lead_score
                  ) : (
                    <span className="muted small">—</span>
                  )}
                </td>
                <td>
                  {row.owner_name ? (
                    row.owner_name
                  ) : (
                    <span className="muted small">Sin propietario</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </section>
  );
}
