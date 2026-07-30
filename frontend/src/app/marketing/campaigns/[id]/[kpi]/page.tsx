"use client";

/**
 * Sprint Campaign-Deeplink. La antigua página estática por KPI
 * (`/marketing/campaigns/{id}/{kpi}`) ahora REDIRIGE a `/contacts` con
 * el filtro `brevo_campaign_interaction` de esa métrica + campaña ya
 * aplicado — donde el operador tiene filtros AND/OR, guardar como vista
 * y acciones masivas.
 *
 * El redirect es client-side (router.replace) porque la app usa auth
 * por token en localStorage: un server component no puede resolver el
 * `brevo_campaign_id` (int) que necesita el filtro. Se preserva el
 * marcador: quien abra la URL antigua aterriza en el listado filtrado.
 *
 * NO se borran los componentes del listado estático (ContactKpiTable,
 * getBrevoCampaignContactsByKpi) — solo se deja de renderizar esta
 * página, por si Bart necesita revertir.
 */
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { PageHeader } from "../../../../components/PageHeader";
import { campaignKpiContactsHref } from "../../../../lib/campaignDeepLink";
import { type CampaignKpi, getBrevoCampaign } from "../../../../lib/brevoApi";

const VALID_KPIS: CampaignKpi[] = [
  "sent",
  "delivered",
  "opened",
  "clicked",
  "bounces",
  "unsubscribed",
  "complained",
];

function isValidKpi(value: string): value is CampaignKpi {
  return (VALID_KPIS as string[]).includes(value);
}

export default function CampaignKpiRedirectPage() {
  const params = useParams<{ id: string; kpi: string }>();
  const router = useRouter();
  const campaignId = params.id;
  const kpi = params.kpi;
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!isValidKpi(kpi)) {
      setError(`El KPI "${kpi}" no existe para esta campaña.`);
      return;
    }
    let cancelled = false;
    getBrevoCampaign(campaignId)
      .then((campaign) => {
        if (cancelled) return;
        router.replace(
          campaignKpiContactsHref(campaign.brevo_campaign_id, kpi),
        );
      })
      .catch(() => {
        if (!cancelled) {
          setError(
            "No se pudo resolver la campaña para aplicar el filtro. " +
              "Vuelve a la ficha de la campaña e inténtalo de nuevo.",
          );
        }
      });
    return () => {
      cancelled = true;
    };
  }, [campaignId, kpi, router]);

  return (
    <main className="shell">
      <PageHeader
        title="Abriendo contactos…"
        eyebrow="Campañas"
        crumbs={[
          { label: "Marketing", href: "/marketing/campaigns" },
          { label: "Campañas", href: "/marketing/campaigns" },
          { label: "Contactos" },
        ]}
      />
      {error ? (
        <p className="muted">{error}</p>
      ) : (
        <p className="muted">
          Redirigiendo a la lista de contactos con el filtro aplicado…
        </p>
      )}
    </main>
  );
}
