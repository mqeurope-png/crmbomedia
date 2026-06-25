"use client";

/**
 * "👥 Leads prioritarios" — PR-E2, selector temporal ampliado en
 * PR-E3 ([3d][1sem][15d][30d][Custom]) + persistencia localStorage.
 * Lista contactos asignados al user con razón recent/assigned/active.
 *
 * PR-Fix-Leads-Prioritarios-3a-Vez. La V1 (#237) hizo
 * `?preset=priority_leads`; /contacts no reconocía el preset → vacío.
 * La V2 (#238) pre-fetcheaba IDs async y montaba `?rules=base64` —
 * pero el href arrancaba en `/contacts` plano y Bart, al clickar
 * antes del resolve del fetch, navegaba a /contacts SIN query →
 * /contacts hidrataba el `view_state` de localStorage (su última
 * vista guardada, `view_id=cc9baa84...`) y la URL final resultaba
 * con `view_id` y sin `rules`. Race-condition documentada por Bart
 * con screenshot de la URL real.
 *
 * V3 (este fix): convertir el `<Link>` en `<button>` con
 * onClick async. Pase lo que pase, el navigate ocurre DESPUÉS de
 * que tenemos los IDs:
 *
 *   1. Si los IDs ya están cacheados en estado → push inmediato.
 *   2. Si la fetch sigue pending → await la promesa cacheada en
 *      `seeAllPromiseRef` (sin disparar otra) y push con su
 *      resultado.
 *   3. Si la fetch falló o devolvió 0 → navegar a `/contacts`
 *      vacío (degradado correcto, no rompe el flow).
 *
 * El href nunca contiene `view_id`. Se usa `router.push()` con
 * rules en query string — el parser de /contacts ya entra por la
 * rama `if (urlState.rules)` (línea 254 de contacts/page.tsx) y
 * descarta la default view. Verificado leyendo readUrlState +
 * el effect que re-serializa la URL en /contacts.
 */
import { Users } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
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

function buildRulesUrl(ids: string[]): string {
  // Formato del rules tree del repo: ver
  // `frontend/src/app/lib/entitySchema.ts` (RuleNode).
  const rules = {
    operator: "AND",
    children: [
      {
        type: "rule",
        field: "id",
        comparator: "in",
        value: ids,
      },
    ],
  };
  const encoded = btoa(encodeURIComponent(JSON.stringify(rules)));
  return `/contacts?rules=${encoded}`;
}

export function PriorityLeadsWidget() {
  const router = useRouter();
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

  // V3: full-set IDs cacheados + ref a la promise para que el click
  // pueda await si todavía está pending. Pedimos hasta 500 — cap
  // razonable para /contacts; si hay más, Bart afina con filtros
  // adicionales encima.
  const [allIds, setAllIds] = useState<string[] | null>(null);
  const seeAllPromiseRef = useRef<Promise<string[]> | null>(null);
  const [navigating, setNavigating] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setAllIds(null);
    seeAllPromiseRef.current = null;
    const promise = getDashboardPriorityLeads(window_, 500)
      .then((rows) => rows.map((r) => r.id))
      .catch(() => [] as string[]);
    seeAllPromiseRef.current = promise;
    promise.then((ids) => {
      if (!cancelled) setAllIds(ids);
    });
    return () => {
      cancelled = true;
    };
  }, [window_]);

  async function onSeeAll(e: React.MouseEvent) {
    e.preventDefault();
    setNavigating(true);
    try {
      let ids: string[];
      if (allIds != null) {
        ids = allIds;
      } else {
        // Fetch todavía pending → esperar la promise CACHEADA
        // (no disparar otra). Si nunca arrancó por algún bug,
        // disparar fresco.
        ids =
          (await (seeAllPromiseRef.current ??
            getDashboardPriorityLeads(window_, 500).then((rows) =>
              rows.map((r) => r.id),
            ))) ?? [];
      }
      if (!ids.length) {
        // No hay leads prioritarios → llevamos a /contacts vacío
        // con rules que matchean 0 (id IN [""]) para que la página
        // muestre 0 resultados en lugar de la default view del user.
        router.push(buildRulesUrl([""]));
        return;
      }
      router.push(buildRulesUrl(ids));
    } finally {
      setNavigating(false);
    }
  }

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
          {/* PR-Fix-Leads-Prioritarios-3a-Vez. button + onClick
           * async — el navigate ocurre DESPUÉS de que tenemos los
           * IDs, así nunca llegamos a /contacts con URL vacía (que
           * disparaba el localStorage fallback y la default view
           * con view_id=cc9baa84...). */}
          <button
            type="button"
            className="widget-see-all"
            onClick={onSeeAll}
            disabled={navigating}
            aria-busy={navigating}
          >
            {navigating ? "Cargando…" : "Ver todos →"}
          </button>
        </footer>
      ) : null}
    </article>
  );
}
