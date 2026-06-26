"use client";

/**
 * PR-Bugs-4-5amp-7-9. Página dedicada por KPI del widget "Mis stats
 * de campañas" del dashboard. Replica el patrón de `/dashboard/
 * leads-prioritarios` (PR #241): tabla simple, sin filtros, sin
 * acciones masivas, hasta 200 filas.
 *
 * KPIs soportados (de `MyCampaignKpi`):
 *   - received  → "Contactos que recibieron"
 *   - opened    → "Contactos que abrieron"
 *   - clicked   → "Contactos que clickearon"
 *
 * Si el operador llega con un KPI desconocido, redirigimos al
 * dashboard.
 */
import { Users } from "lucide-react";
import { useParams, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ContactKpiTable } from "../../../components/ContactKpiTable";
import { PageHeader } from "../../../components/PageHeader";
import { PeriodSelector } from "../../../components/dashboard/PeriodSelector";
import {
  getDashboardMyCampaignContacts,
  type DashboardWindow,
  type MyCampaignKpi,
  type PriorityLead,
} from "../../../lib/dashboardApi";

const KPI_LABEL: Record<MyCampaignKpi, { title: string; description: string }> =
  {
    received: {
      title: "Recibieron tus campañas",
      description:
        "Contactos asignados a ti que recibieron al menos una de tus campañas de Brevo dentro del periodo seleccionado.",
    },
    opened: {
      title: "Abrieron tus campañas",
      description:
        "Contactos asignados a ti que abrieron alguna campaña enviada en el periodo. Incluye también los que llegaron a hacer clic.",
    },
    clicked: {
      title: "Clickearon tus campañas",
      description:
        "Contactos asignados a ti que hicieron clic en algún enlace de una campaña enviada en el periodo.",
    },
  };

function parseWindow(raw: string | null): DashboardWindow {
  if (!raw) return { period: "30d" };
  if (["3d", "7d", "14d", "15d", "30d"].includes(raw)) {
    return { period: raw as DashboardWindow["period"] };
  }
  return { period: "30d" };
}

function isValidKpi(value: string): value is MyCampaignKpi {
  return value === "received" || value === "opened" || value === "clicked";
}

export default function MisStatsKpiPage() {
  const params = useParams<{ kpi: string }>();
  const searchParams = useSearchParams();
  const kpi = params.kpi;
  const initialWindow = useMemo(
    () => parseWindow(searchParams.get("window")),
    [searchParams],
  );
  const [window_, setWindow] = useState<DashboardWindow>(initialWindow);
  const [rows, setRows] = useState<PriorityLead[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const valid = isValidKpi(kpi);
  const label = valid ? KPI_LABEL[kpi] : null;

  const load = useCallback(async () => {
    if (!valid) return;
    setLoading(true);
    setError(null);
    try {
      const data = await getDashboardMyCampaignContacts(
        kpi as MyCampaignKpi,
        window_,
        200,
      );
      setRows(data);
    } catch (err) {
      // eslint-disable-next-line no-console
      console.error(`mis-stats/${kpi} load failed:`, err);
      setError(
        "No se han podido cargar los contactos. Reintenta más tarde.",
      );
      setRows(null);
    } finally {
      setLoading(false);
    }
  }, [valid, kpi, window_]);

  useEffect(() => {
    void load();
  }, [load]);

  if (!valid || !label) {
    return (
      <main className="shell">
        <PageHeader
          title="KPI desconocido"
          eyebrow="Dashboard"
          crumbs={[
            { label: "Dashboard", href: "/" },
            { label: "Mis stats" },
          ]}
        />
        <p className="muted">
          El KPI &quot;{kpi}&quot; no existe. Vuelve al dashboard y elige uno
          desde el widget &quot;Mis stats de campañas&quot;.
        </p>
      </main>
    );
  }

  return (
    <main className="shell">
      <PageHeader
        title={label.title}
        eyebrow="Mis stats de campañas"
        description={label.description}
        actions={<PeriodSelector value={window_} onChange={setWindow} />}
        crumbs={[
          { label: "Dashboard", href: "/" },
          { label: label.title },
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
      ) : !rows || rows.length === 0 ? (
        <div className="empty-state">
          <Users size={32} aria-hidden />
          <p className="muted">No hay contactos en este periodo.</p>
        </div>
      ) : (
        <ContactKpiTable rows={rows} signalLabel="Última interacción" />
      )}
    </main>
  );
}
