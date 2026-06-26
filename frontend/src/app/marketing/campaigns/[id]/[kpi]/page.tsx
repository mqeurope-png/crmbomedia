"use client";

/**
 * PR-Bugs-4-5amp-7-9. Página dedicada por KPI de una campaña Brevo.
 * Reusa el patrón de `/dashboard/leads-prioritarios` (PR #241): tabla
 * compacta con hasta 200 contactos, sin filtros adicionales.
 *
 * Coexiste con la pestaña "Destinatarios" del detalle de campaña
 * (`/marketing/campaigns/{id}`): la pestaña sigue funcionando para
 * uso rápido; esta página es para "ver lista completa". El operador
 * llega por click en el número del KPI de la cabecera.
 */
import { ExternalLink, Users } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { ContactKpiTable } from "../../../../components/ContactKpiTable";
import { PageHeader } from "../../../../components/PageHeader";
import {
  type CampaignKpi,
  getBrevoCampaign,
  getBrevoCampaignContactsByKpi,
  type BrevoCampaign,
} from "../../../../lib/brevoApi";
import type { PriorityLead } from "../../../../lib/dashboardApi";

const KPI_LABEL: Record<CampaignKpi, { title: string; description: string }> = {
  sent: {
    title: "Enviados",
    description:
      "Contactos a los que se intentó entregar esta campaña (incluye los que llegaron a abrir o hacer clic).",
  },
  delivered: {
    title: "Entregados",
    description:
      "Contactos cuyo proveedor confirmó la entrega de la campaña.",
  },
  opened: {
    title: "Abrieron",
    description:
      "Contactos que abrieron la campaña al menos una vez. Incluye los que clickearon.",
  },
  clicked: {
    title: "Clickearon",
    description:
      "Contactos que hicieron clic en algún enlace de la campaña.",
  },
  bounces: {
    title: "Rebotes",
    description:
      "Contactos con rebote (hard o soft) según Brevo.",
  },
  unsubscribed: {
    title: "Se dieron de baja",
    description:
      "Contactos que se dieron de baja a través de esta campaña.",
  },
  complained: {
    title: "Reportaron spam",
    description:
      "Contactos que marcaron la campaña como spam (quejas reportadas por el proveedor de correo).",
  },
};

const VALID_KPIS = Object.keys(KPI_LABEL) as CampaignKpi[];

function isValidKpi(value: string): value is CampaignKpi {
  return (VALID_KPIS as string[]).includes(value);
}

export default function CampaignKpiPage() {
  const params = useParams<{ id: string; kpi: string }>();
  const campaignId = params.id;
  const kpi = params.kpi;
  const valid = isValidKpi(kpi);

  const [campaign, setCampaign] = useState<BrevoCampaign | null>(null);
  const [rows, setRows] = useState<PriorityLead[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!valid) return;
    setLoading(true);
    setError(null);
    try {
      const [fresh, contacts] = await Promise.all([
        getBrevoCampaign(campaignId),
        getBrevoCampaignContactsByKpi(campaignId, kpi as CampaignKpi, 200),
      ]);
      setCampaign(fresh);
      setRows(contacts);
    } catch (err) {
      // eslint-disable-next-line no-console
      console.error(`campaigns/${campaignId}/${kpi} load failed:`, err);
      setError(
        "No se han podido cargar los contactos. Reintenta más tarde.",
      );
      setRows(null);
    } finally {
      setLoading(false);
    }
  }, [valid, campaignId, kpi]);

  useEffect(() => {
    void load();
  }, [load]);

  if (!valid) {
    return (
      <main className="shell">
        <PageHeader
          title="KPI desconocido"
          eyebrow="Campañas"
          crumbs={[
            { label: "Marketing", href: "/marketing/campaigns" },
            { label: "Campañas", href: "/marketing/campaigns" },
            { label: "KPI" },
          ]}
        />
        <p className="muted">
          El KPI &quot;{kpi}&quot; no existe para esta campaña.
        </p>
      </main>
    );
  }

  const label = KPI_LABEL[kpi as CampaignKpi];
  const campaignName = campaign?.name ?? "Campaña";

  return (
    <main className="shell">
      <PageHeader
        title={`${label.title} — ${campaignName}`}
        eyebrow="Campañas"
        description={label.description}
        crumbs={[
          { label: "Marketing", href: "/marketing/campaigns" },
          { label: campaignName, href: `/marketing/campaigns/${campaignId}` },
          { label: label.title },
        ]}
        actions={
          campaign ? (
            <Link
              href={`/marketing/campaigns/${campaignId}`}
              className="button secondary small"
            >
              <ExternalLink size={12} aria-hidden /> Volver a la campaña
            </Link>
          ) : null
        }
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
          <p className="muted">
            No hay contactos registrados para este KPI.
          </p>
        </div>
      ) : (
        <ContactKpiTable rows={rows} signalLabel="Último evento" />
      )}
    </main>
  );
}
