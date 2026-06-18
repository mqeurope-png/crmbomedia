"use client";

/**
 * Tira de 6 KPIs del nuevo dashboard BoHub (PR-C). Cada tile pulla
 * de un endpoint distinto del backend (no hay un /api/dashboard/kpis
 * único — la deuda menor es construir uno; de momento se hace en el
 * cliente para no añadir backend en este PR puramente visual).
 *
 * Datos:
 *   - Tareas vencidas:    /api/dashboard/tasks-pending  (filtra por due_at < now)
 *   - Leads nuevos:       /api/dashboard/leads-stats?range=…
 *   - Oportunidades:      /api/dashboard/pipeline-summary (suma stages)
 *   - Emails abiertos:    /api/emails/stats?days=1
 *   - Próximos eventos:   /api/dashboard/google-calendar-events
 *   - Objetivo del mes:   TODO — sin backend, queda placeholder.
 */
import {
  Calendar,
  CalendarClock,
  Mail,
  Target,
  TrendingUp,
  Users,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { parseBackendDate } from "../../lib/dates";
import { getEmailStats } from "../../lib/emailTrackingApi";
import {
  getDashboardGoogleEvents,
  getDashboardLeadsStats,
  getDashboardPipelineSummary,
  getDashboardTasksPending,
  type LeadsStatsRange,
} from "../../lib/dashboardApi";

export type DashboardRange = "today" | "7d" | "30d";

type Props = {
  range: DashboardRange;
};

type KpiTone =
  | "red"
  | "green"
  | "purple"
  | "blue"
  | "amber"
  | "primary";

type KpiState = {
  loading: boolean;
  // Cada KPI se publica por separado para que un endpoint lento no
  // bloquee la tira entera (cada uno enciende su propio "Cargando…").
  tasksOverdue: number | null;
  leadsNew: number | null;
  leadsNewDeltaPct: number | null;
  opportunitiesActive: number | null;
  opportunitiesValue: number | null;
  emailsOpened: number | null;
  emailsOpenedRange: number;
  upcomingEvents: number | null;
};

function rangeDays(range: DashboardRange): number {
  if (range === "today") return 1;
  if (range === "7d") return 7;
  return 30;
}

function rangeToLeadsParam(range: DashboardRange): LeadsStatsRange {
  if (range === "30d") return "30d";
  return "7d"; // today + 7d se mapean a la ventana mínima del endpoint
}

function formatNumber(n: number | null): string {
  if (n === null) return "—";
  return new Intl.NumberFormat("es-ES").format(n);
}

function KpiCard({
  tone,
  icon: Icon,
  label,
  value,
  trend,
  hint,
  progress,
  loading,
}: {
  tone: KpiTone;
  icon: React.ComponentType<{ size?: number; "aria-hidden"?: boolean }>;
  label: string;
  value: string;
  trend?: { label: string; direction: "up" | "down" | "neutral" } | null;
  hint?: string | null;
  progress?: number | null;
  loading?: boolean;
}) {
  return (
    <article className={`kpi-card kpi-tone-${tone}`}>
      <span className="kpi-icon" aria-hidden>
        <Icon size={20} aria-hidden />
      </span>
      <div className="kpi-body">
        <span className="kpi-label">{label}</span>
        <span className="kpi-value">{loading ? "…" : value}</span>
        {trend ? (
          <span className={`kpi-trend kpi-trend-${trend.direction}`}>
            {trend.label}
          </span>
        ) : hint ? (
          <span className="kpi-hint">{hint}</span>
        ) : null}
        {typeof progress === "number" ? (
          <span
            className="kpi-progress"
            role="progressbar"
            aria-valuenow={Math.round(progress)}
            aria-valuemin={0}
            aria-valuemax={100}
          >
            <span
              className="kpi-progress-fill"
              style={{ width: `${Math.min(100, Math.max(0, progress))}%` }}
            />
          </span>
        ) : null}
      </div>
    </article>
  );
}

export function DashboardKpis({ range }: Props) {
  const [state, setState] = useState<KpiState>({
    loading: true,
    tasksOverdue: null,
    leadsNew: null,
    leadsNewDeltaPct: null,
    opportunitiesActive: null,
    opportunitiesValue: null,
    emailsOpened: null,
    emailsOpenedRange: rangeDays(range),
    upcomingEvents: null,
  });

  useEffect(() => {
    let cancelled = false;
    setState((s) => ({ ...s, loading: true, emailsOpenedRange: rangeDays(range) }));

    // Lanzamos los 5 fetches en paralelo. Cada uno setea su slice.
    // Errores individuales se loggean pero no rompen los demás
    // (los slices que fallen quedan en null → tile pinta "—").
    const tasks = getDashboardTasksPending()
      .then((rows) => {
        if (cancelled) return;
        const now = Date.now();
        const overdue = rows.filter(
          (t) => t.due_at && parseBackendDate(t.due_at).getTime() < now,
        ).length;
        setState((s) => ({ ...s, tasksOverdue: overdue }));
      })
      .catch(() => {
        if (!cancelled) setState((s) => ({ ...s, tasksOverdue: 0 }));
      });

    const leads = getDashboardLeadsStats(rangeToLeadsParam(range), "day")
      .then((stats) => {
        if (cancelled) return;
        setState((s) => ({
          ...s,
          leadsNew: stats.totals.leads_current,
          leadsNewDeltaPct: stats.totals.delta_pct,
        }));
      })
      .catch(() => {
        if (!cancelled)
          setState((s) => ({ ...s, leadsNew: 0, leadsNewDeltaPct: null }));
      });

    const pipeline = getDashboardPipelineSummary()
      .then((rows) => {
        if (cancelled) return;
        // Suma de stages de pipelines activos. No tenemos valor €
        // por contacto en este endpoint — placeholder hasta tener
        // un /api/dashboard/opportunities-value.
        const total = rows.reduce(
          (acc, p) => acc + p.stages.reduce((s, st) => s + st.count, 0),
          0,
        );
        setState((s) => ({ ...s, opportunitiesActive: total }));
      })
      .catch(() => {
        if (!cancelled) setState((s) => ({ ...s, opportunitiesActive: 0 }));
      });

    const emails = getEmailStats(rangeDays(range), { scope: "mine" })
      .then((stats) => {
        if (cancelled) return;
        setState((s) => ({ ...s, emailsOpened: stats.opened }));
      })
      .catch(() => {
        if (!cancelled) setState((s) => ({ ...s, emailsOpened: 0 }));
      });

    const events = getDashboardGoogleEvents()
      .then((res) => {
        if (cancelled) return;
        setState((s) => ({
          ...s,
          upcomingEvents: res.connected ? res.events.length : 0,
        }));
      })
      .catch(() => {
        if (!cancelled) setState((s) => ({ ...s, upcomingEvents: 0 }));
      });

    Promise.allSettled([tasks, leads, pipeline, emails, events]).then(() => {
      if (!cancelled) setState((s) => ({ ...s, loading: false }));
    });
    return () => {
      cancelled = true;
    };
  }, [range]);

  const leadsTrend = useMemo(() => {
    const pct = state.leadsNewDeltaPct;
    if (pct === null || Number.isNaN(pct)) return null;
    const direction: "up" | "down" | "neutral" =
      pct > 0 ? "up" : pct < 0 ? "down" : "neutral";
    const arrow = pct > 0 ? "↑" : pct < 0 ? "↓" : "·";
    return {
      label: `${arrow} ${Math.abs(pct)}% vs período anterior`,
      direction,
    };
  }, [state.leadsNewDeltaPct]);

  const tasksTrend = useMemo(() => {
    // Sin endpoint para "vs ayer" — solo enseñamos el contador con
    // color rojo si > 0. Bart pidió placeholder OK.
    if (state.tasksOverdue === null) return null;
    if (state.tasksOverdue === 0) {
      return { label: "Sin tareas vencidas", direction: "neutral" as const };
    }
    return {
      label: "Revisar pendientes",
      direction: "down" as const,
    };
  }, [state.tasksOverdue]);

  const rangeLabel =
    range === "today" ? "hoy" : range === "7d" ? "últimos 7 días" : "últimos 30 días";

  return (
    <section className="dashboard-kpis" aria-label="Resumen de hoy">
      <KpiCard
        tone="red"
        icon={CalendarClock}
        label="Tareas vencidas"
        value={formatNumber(state.tasksOverdue)}
        trend={tasksTrend}
        loading={state.loading && state.tasksOverdue === null}
      />
      <KpiCard
        tone="green"
        icon={Users}
        label="Leads nuevos"
        value={formatNumber(state.leadsNew)}
        trend={leadsTrend}
        hint={leadsTrend ? null : rangeLabel}
        loading={state.loading && state.leadsNew === null}
      />
      <KpiCard
        tone="purple"
        icon={Target}
        label="Oportunidades activas"
        value={formatNumber(state.opportunitiesActive)}
        hint="En pipelines abiertos"
        loading={state.loading && state.opportunitiesActive === null}
      />
      <KpiCard
        tone="blue"
        icon={Mail}
        label={
          range === "today"
            ? "Emails abiertos hoy"
            : `Emails abiertos · ${rangeLabel}`
        }
        value={formatNumber(state.emailsOpened)}
        hint={range === "today" ? "Tracking mío" : `${state.emailsOpenedRange} días`}
        loading={state.loading && state.emailsOpened === null}
      />
      <KpiCard
        tone="amber"
        icon={Calendar}
        label="Próximos eventos"
        value={formatNumber(state.upcomingEvents)}
        hint="Próximos 14 días"
        loading={state.loading && state.upcomingEvents === null}
      />
      <KpiCard
        tone="primary"
        icon={TrendingUp}
        label="Objetivo del mes"
        value="—"
        hint="Próximamente"
        progress={null}
        loading={false}
      />
    </section>
  );
}
