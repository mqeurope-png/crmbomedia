"use client";

/**
 * "👥 Leads prioritarios" — PR-E2, selector temporal ampliado en
 * PR-E3 ([3d][1sem][15d][30d][Custom]) + persistencia localStorage.
 * Lista contactos asignados al user con razón recent/assigned/active.
 */
import { Users } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import {
  getDashboardPriorityLeads,
  type DashboardWindow,
  type PriorityLead,
} from "../../lib/dashboardApi";
import { parseBackendDate } from "../../lib/dates";
import { usePersistentState } from "../../lib/usePersistentState";
import { PeriodSelector } from "./PeriodSelector";

const REASON_LABEL: Record<string, { label: string; tone: string }> = {
  recent: { label: "Recién creado", tone: "is-info" },
  assigned: { label: "Recién asignado", tone: "is-success" },
  active: { label: "Activo", tone: "is-warning" },
};

// PR-Timezone-Fix. Esta función tiene granularidad de día — "hoy",
// "ayer", "hace Nd" — no de horas, así que un offset de 2 h no debería
// notarse en pantalla. Aún así migrar el parsing por coherencia y para
// que el `toLocaleDateString` final use la fecha local correcta.
function relative(value: string): string {
  const target = parseBackendDate(value);
  if (Number.isNaN(target.getTime())) return "—";
  const diff = Date.now() - target.getTime();
  const day = Math.floor(diff / 86_400_000);
  if (day === 0) return "hoy";
  if (day === 1) return "ayer";
  if (day < 30) return `hace ${day}d`;
  return target.toLocaleDateString("es-ES", {
    day: "2-digit",
    month: "short",
  });
}

export function PriorityLeadsWidget() {
  const [window_, setWindow] = usePersistentState<DashboardWindow>(
    "crmbomedia_dash:priority_leads:period",
    { period: "7d" },
  );
  const [leads, setLeads] = useState<PriorityLead[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getDashboardPriorityLeads(window_, 10)
      .then((rows) => {
        if (!cancelled) setLeads(rows);
      })
      .catch(() => {
        if (!cancelled) setError("No se pudieron cargar los leads.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [window_]);

  // PR-Fix-Regresiones-PR237 Bug 3. La V1 del PR #237 linkaba a
  // `?preset=priority_leads` pero /contacts no reconocía ese preset
  // → abría sin filtros. Fix: pre-fetch IDs del endpoint del widget
  // y encodearlos como `rules` IN base64 — el mismo formato que ya
  // usa /contacts cuando aplica filtros del builder.
  //
  // Resultado: /contacts muestra EXACTAMENTE los mismos contactos
  // que el widget (no más, no menos), con todas las acciones masivas
  // disponibles (Bart pidió "asignar owner, añadir tag, push Brevo,
  // etc").
  const [seeAllHref, setSeeAllHref] = useState<string>("/contacts");
  useEffect(() => {
    let cancelled = false;
    // Pedimos hasta 500 (cap razonable para /contacts) — si hay más,
    // Bart puede afinar con filtros adicionales encima.
    getDashboardPriorityLeads(window_, 500)
      .then((rows) => {
        if (cancelled) return;
        if (!rows.length) {
          setSeeAllHref("/contacts");
          return;
        }
        // Formato del rules tree del repo: ver
        // `frontend/src/app/lib/entitySchema.ts` (RuleNode).
        const rules = {
          operator: "AND",
          children: [
            {
              type: "rule",
              field: "id",
              comparator: "in",
              value: rows.map((r) => r.id),
            },
          ],
        };
        const encoded = btoa(
          encodeURIComponent(JSON.stringify(rules)),
        );
        setSeeAllHref(`/contacts?rules=${encoded}`);
      })
      .catch(() => {
        if (!cancelled) setSeeAllHref("/contacts");
      });
    return () => {
      cancelled = true;
    };
  }, [window_]);

  return (
    <article className="card widget widget-priority-leads">
      <header className="section-title">
        <h2>
          <Users size={14} aria-hidden /> Leads prioritarios
        </h2>
        <PeriodSelector value={window_} onChange={setWindow} />
      </header>
      <div className="widget-scroll">
        {loading ? (
          <p className="muted small">Cargando…</p>
        ) : error ? (
          <p className="form-error">{error}</p>
        ) : leads.length === 0 ? (
          <div className="widget-empty">
            <p className="muted small">
              No tienes leads prioritarios en este período.
            </p>
          </div>
        ) : (
          <ul className="widget-list">
            {leads.map((lead) => {
              const reason = REASON_LABEL[lead.reason] ?? {
                label: lead.reason,
                tone: "is-muted",
              };
              const name =
                [lead.first_name, lead.last_name].filter(Boolean).join(" ") ||
                lead.email;
              return (
                <li key={lead.id} className="widget-row">
                  <div className="widget-row-main">
                    <p className="widget-row-title">
                      <Link href={`/contacts/${lead.id}`}>{name}</Link>
                    </p>
                    <p className="widget-row-meta">
                      <span className="muted small">{lead.email}</span>
                      <span className="muted small">
                        · {relative(lead.signal_at)}
                      </span>
                    </p>
                  </div>
                  <span className={`chip ${reason.tone}`}>{reason.label}</span>
                </li>
              );
            })}
          </ul>
        )}
      </div>
      {leads.length > 0 ? (
        <footer className="widget-footer">
          <Link href={seeAllHref} className="widget-see-all">
            Ver todos →
          </Link>
        </footer>
      ) : null}
    </article>
  );
}
