"use client";

/**
 * "👥 Leads prioritarios" — PR-E2, selector temporal ampliado en
 * PR-E3 ([3d][1sem][15d][30d][Custom]) + persistencia localStorage.
 * Lista contactos asignados al user con razón recent/assigned/active.
 *
 * Postmortem "Ver todos" — 4 PRs antes de simplificar:
 *
 *   #237: link a `/contacts?preset=priority_leads` → /contacts no
 *         reconoce el preset.
 *   #238: pre-fetch IDs async + Link con href construido en estado.
 *         Race: si Bart clickaba antes del resolve, navegaba a
 *         /contacts vacío → hidratador caía en localStorage view_id.
 *   #239: button + onClick async + campo `id` añadido al engine de
 *         segments. Backend cap `le=50` reventaba el fetch de 500
 *         con 422; mi `.catch(() => [])` lo swallow-eaba.
 *   #240: cap backend → 200, mejor manejo de errores. Pero seguía
 *         dependiendo de la sincronía rules ↔ engine ↔ /contacts.
 *
 * PR-Leads-Prioritarios-Página-Dedicada: dejamos de intentarlo.
 * "Ver todos" navega a una página dedicada
 * `/dashboard/leads-prioritarios?window=X` que muestra la lista
 * expandida en una tabla simple. Cero rules, cero engine, cero
 * URL state. Si en el futuro hace falta filtrar / acciones masivas,
 * se añade ahí — pero ahora mismo Bart solo necesita ver la lista
 * y eso cabe en 200 líneas autocontenidas.
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

  // "Ver todos" pasa la ventana del widget como query param para que
  // la página dedicada arranque alineada. Solo periodos presets en
  // el href — `custom` no se navega porque la página tiene su propio
  // selector que el operador puede ajustar.
  const seeAllHref =
    window_.period === "custom"
      ? "/dashboard/leads-prioritarios"
      : `/dashboard/leads-prioritarios?window=${encodeURIComponent(window_.period)}`;

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
          {/* PR-Leads-Prioritarios-Página-Dedicada. Link plano a la
           * página dedicada. Sin async, sin rules, sin URL state
           * frágil. Si la página falla, el operador ve el error en
           * la página, no aterriza en una vista desfiltrada. */}
          <Link href={seeAllHref} className="widget-see-all">
            Ver todos →
          </Link>
        </footer>
      ) : null}
    </article>
  );
}
