"use client";

/**
 * PR-Leads-Prioritarios-Página-Dedicada. Página dedicada a la lista
 * expandida del widget "Leads prioritarios" del dashboard.
 *
 * Decisión congelada por Bart tras 4 PRs intentando reusar /contacts
 * (#237/#238/#239/#240): la complejidad de serializar los criterios
 * del widget como rules del engine de segments + URL state + parsing
 * en /contacts fue frágil. Esta página es una tabla simple,
 * autosuficiente, sin paginación inicial, sin filtros, sin acciones
 * masivas. 200 filas como mucho — alineado con el cap del endpoint.
 *
 * Reusa `GET /api/dashboard/priority-leads` (mismo endpoint del
 * widget) con `limit=200`. El widget devuelve 10 para el preview;
 * la página devuelve hasta 200 con campos extra (lead_score, tags,
 * owner_name) que el widget no necesita.
 *
 * Estado vacío: "No hay leads prioritarios en este periodo".
 * Estado de error: banner + botón "Reintentar".
 */
import { Users } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { PageHeader } from "../../components/PageHeader";
import { PeriodSelector } from "../../components/dashboard/PeriodSelector";
import {
  getDashboardPriorityLeads,
  type DashboardWindow,
  type PriorityLead,
} from "../../lib/dashboardApi";
import { parseBackendDate } from "../../lib/dates";

const FULL_SET_LIMIT = 200;

function relative(value: string): string {
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

const REASON_LABEL: Record<string, { label: string; tone: string }> = {
  recent: { label: "Recién creado", tone: "is-info" },
  assigned: { label: "Recién asignado", tone: "is-success" },
  active: { label: "Activo", tone: "is-warning" },
};

function parseWindow(raw: string | null): DashboardWindow {
  if (!raw) return { period: "7d" };
  // El widget pasa `?window=3d` / `7d` / etc. — formato corto.
  if (["3d", "7d", "14d", "15d", "30d"].includes(raw)) {
    return { period: raw as DashboardWindow["period"] };
  }
  return { period: "7d" };
}

export default function LeadsPrioritariosPage() {
  const searchParams = useSearchParams();
  const initialWindow = useMemo(
    () => parseWindow(searchParams.get("window")),
    [searchParams],
  );
  const [window_, setWindow] = useState<DashboardWindow>(initialWindow);
  const [leads, setLeads] = useState<PriorityLead[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const rows = await getDashboardPriorityLeads(window_, FULL_SET_LIMIT);
      setLeads(rows);
    } catch (err) {
      // eslint-disable-next-line no-console
      console.error("leads-prioritarios load failed:", err);
      setError(
        "No se han podido cargar los leads prioritarios. Reintenta más tarde.",
      );
      setLeads(null);
    } finally {
      setLoading(false);
    }
  }, [window_]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <main className="shell">
      <PageHeader
        title="Leads prioritarios"
        eyebrow="Dashboard"
        description="Contactos asignados a ti que han tenido señal reciente (recién creados, recién asignados o con actividad) dentro del periodo seleccionado."
        actions={<PeriodSelector value={window_} onChange={setWindow} />}
        crumbs={[
          { label: "Dashboard", href: "/" },
          { label: "Leads prioritarios" },
        ]}
      />

      {loading ? (
        <p className="muted">Cargando…</p>
      ) : error ? (
        <div className="error-state">
          <p>{error}</p>
          <button
            type="button"
            className="button small secondary"
            onClick={() => void load()}
          >
            Reintentar
          </button>
        </div>
      ) : !leads || leads.length === 0 ? (
        <div className="empty-state">
          <Users size={32} aria-hidden />
          <p className="muted">
            No hay leads prioritarios en este periodo.
          </p>
        </div>
      ) : (
        <section className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Nombre</th>
                <th>Email</th>
                <th>Última actividad</th>
                <th>Etiquetas</th>
                <th>Lead score</th>
                <th>Propietario</th>
              </tr>
            </thead>
            <tbody>
              {leads.map((lead) => {
                const name =
                  [lead.first_name, lead.last_name].filter(Boolean).join(" ") ||
                  lead.email;
                const reason = REASON_LABEL[lead.reason] ?? {
                  label: lead.reason,
                  tone: "is-muted",
                };
                const tags = lead.tags ?? [];
                const visibleTags = tags.slice(0, 3);
                const extraTagsCount = tags.length - visibleTags.length;
                return (
                  <tr key={lead.id}>
                    <td>
                      <Link href={`/contacts/${lead.id}`}>{name}</Link>
                      <div className="muted" style={{ fontSize: 11 }}>
                        <span className={`chip ${reason.tone}`}>
                          {reason.label}
                        </span>
                      </div>
                    </td>
                    <td className="muted">{lead.email}</td>
                    <td>{relative(lead.signal_at)}</td>
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
                      {lead.lead_score != null ? (
                        lead.lead_score
                      ) : (
                        <span className="muted small">—</span>
                      )}
                    </td>
                    <td>
                      {lead.owner_name ? (
                        lead.owner_name
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
      )}
    </main>
  );
}
