/**
 * Sprint Campaign-Deeplink. Construye la URL de `/contacts` con el
 * filtro compuesto `brevo_campaign_interaction` ya aplicado, para que
 * los cards de la ficha de campaña ("Ver") lleven al stack completo de
 * contactos (filtros AND/OR encima, guardar vista, acciones masivas).
 *
 * Formato reutilizado tal cual de `/contacts` (readUrlState /
 * serializeUrlState): querystring `rules=<base64(encodeURIComponent(
 * JSON.stringify(tree)))>` con el árbol IR del query builder.
 */
import type { CampaignKpi } from "./brevoApi";

/** KPI del card → `action` del filtro `brevo_campaign_interaction`.
 *  `delivered` reusa la acción `received` (event_type email.delivered).
 *  `bounces` → `bounced` (hard+soft). `complained` → `spam`
 *  (email.spam_complaint). */
export const KPI_TO_BREVO_ACTION: Record<CampaignKpi, string> = {
  sent: "sent",
  delivered: "received",
  opened: "opened",
  clicked: "clicked",
  bounces: "bounced",
  unsubscribed: "unsubscribed",
  complained: "spam",
};

/** Árbol IR de un filtro sobre una interacción de campaña Brevo. */
export function campaignInteractionRules(
  brevoCampaignId: number,
  action: string,
): Record<string, unknown> {
  return {
    operator: "AND",
    children: [
      {
        type: "rule",
        field: "brevo_campaign_interaction",
        comparator: "matches",
        value: {
          campaigns: [brevoCampaignId],
          action,
          period: "all",
        },
      },
    ],
  };
}

/** Codifica un árbol de reglas al querystring que `/contacts` deserializa. */
export function encodeRulesParam(tree: Record<string, unknown>): string {
  return btoa(encodeURIComponent(JSON.stringify(tree)));
}

/** Href a `/contacts` con el filtro de la métrica `kpi` de la campaña
 *  `brevoCampaignId` pre-aplicado. */
export function campaignKpiContactsHref(
  brevoCampaignId: number,
  kpi: CampaignKpi,
): string {
  const action = KPI_TO_BREVO_ACTION[kpi];
  const rules = encodeRulesParam(
    campaignInteractionRules(brevoCampaignId, action),
  );
  return `/contacts?rules=${rules}`;
}
