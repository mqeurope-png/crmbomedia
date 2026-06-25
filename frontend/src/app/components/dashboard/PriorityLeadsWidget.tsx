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
 * V3 arregló la race condition + añadió campo `id` al engine.
 * Pero introdujo otro bug: pedía `limit=500` al endpoint backend
 * `GET /api/dashboard/priority-leads` que tenía `le=50`. FastAPI
 * 422-eaba la request → mi `.catch(() => [])` la swallow-eaba →
 * `ids = []` → fallback `buildRulesUrl([""])` con `value:[""]` →
 * /contacts mostraba 20.228 contactos (rule vacío = sin filtro).
 *
 * V4 (este fix):
 *   1. Cap frontend a 50 (alineado con backend después de subirlo
 *      a 200 en el mismo PR para futuro power-user margen).
 *   2. ELIMINAMOS el fallback `[""]`. Si la fetch falla o devuelve
 *      empty, mostramos error en UI Y NO navegamos. El operador
 *      ve el problema en lugar de aterrizar en una lista
 *      desfiltrada.
 *   3. `.catch` log + setError en lugar de retornar `[]` silencioso.
 *      Bart y futuros mantenedores verán el fail real, no la
 *      consecuencia downstream.
 *   4. El botón "Ver todos" se deshabilita si la fetch falló.
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

  // V4: full-set IDs cacheados + ref a la promise para await en
  // click si todavía está pending. Limit alineado al cap del
  // endpoint backend (sube a 200 en este mismo PR; mantenemos 50
  // por defecto que cubre los casos reales sin saturar el JSON
  // de la URL).
  const FULL_SET_LIMIT = 50;
  const [allIds, setAllIds] = useState<string[] | null>(null);
  const [seeAllError, setSeeAllError] = useState<string | null>(null);
  const seeAllPromiseRef = useRef<Promise<string[] | null> | null>(null);
  const [navigating, setNavigating] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setAllIds(null);
    setSeeAllError(null);
    seeAllPromiseRef.current = null;
    // V4 critical: la promesa puede devolver `null` cuando hay
    // error — esto es lo que distingue "no hay leads" (array vacío
    // válido) de "fetch falló" (null). El click handler lo lee y
    // decide entre navegar, hacer nada, o mostrar error.
    const promise = getDashboardPriorityLeads(window_, FULL_SET_LIMIT)
      .then((rows) => rows.map((r) => r.id))
      .catch((err) => {
        // V4: NO swallow silently. Log + surface al state.
        // PR-Fix-Leads-Prioritarios-4a-Vez Bart: el bug del PR #239
        // venía precisamente del `.catch(() => [])` swallow que
        // ocultaba el 422 del backend (cap le=50, frontend pedía
        // 500). Ahora vemos el error en consola Y en UI.
        // eslint-disable-next-line no-console
        console.error("priority-leads full set fetch failed:", err);
        return null;
      });
    seeAllPromiseRef.current = promise;
    promise.then((ids) => {
      if (cancelled) return;
      if (ids === null) {
        setSeeAllError(
          "No se pudo cargar la lista completa de leads prioritarios.",
        );
        setAllIds(null);
      } else {
        setAllIds(ids);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [window_]);

  async function onSeeAll(e: React.MouseEvent) {
    e.preventDefault();
    setNavigating(true);
    setSeeAllError(null);
    try {
      let ids: string[] | null;
      if (allIds != null) {
        ids = allIds;
      } else {
        // Fetch todavía pending → await la promise cacheada
        // (no disparar otra).
        ids =
          (await (seeAllPromiseRef.current ??
            getDashboardPriorityLeads(window_, FULL_SET_LIMIT).then((rows) =>
              rows.map((r) => r.id),
            ))) ?? null;
      }
      // V4 critical: si la fetch falló (null) O devolvió empty,
      // NO navegamos con un rule roto. Mostramos error y dejamos
      // al operador en el dashboard. Esto evita aterrizar en
      // /contacts con `value:[""]` o navegar a una vista default
      // que no tiene nada que ver con prioritarios.
      if (ids === null) {
        setSeeAllError(
          "No se pudo cargar la lista completa de leads prioritarios. Vuelve a intentar.",
        );
        return;
      }
      if (!ids.length) {
        // Caso legítimo "0 prioritarios". El widget no debería
        // siquiera renderizar "Ver todos" en este caso (gate
        // `leads.length > 0`), pero si llega aquí no rompemos:
        // mensaje claro al operador.
        setSeeAllError(
          "No tienes leads prioritarios en este período — nada que ver.",
        );
        return;
      }
      // Validación defensiva: filtra ids vacíos / null por si la
      // API algún día devuelve basura. Si todo es basura, falla
      // loud.
      const cleanIds = ids.filter((id) => typeof id === "string" && id.length > 0);
      if (!cleanIds.length) {
        setSeeAllError(
          "La respuesta del servidor no traía IDs válidos. Recarga la página y vuelve a intentar.",
        );
        return;
      }
      router.push(buildRulesUrl(cleanIds));
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
          {/* V3: button + onClick async — el navigate ocurre DESPUÉS
           * de que tenemos los IDs, así nunca llegamos a /contacts
           * con URL vacía (que disparaba el localStorage fallback y
           * la default view con view_id=cc9baa84...).
           *
           * V4: si seeAllError está set, mostramos el mensaje encima
           * del botón en lugar de navegar silenciosamente a una URL
           * con rule vacío. */}
          {seeAllError ? (
            <p
              className="form-error"
              style={{ marginBottom: 6, fontSize: 12 }}
            >
              {seeAllError}
            </p>
          ) : null}
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
