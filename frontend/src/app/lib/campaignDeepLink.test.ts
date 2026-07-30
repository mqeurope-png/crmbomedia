import {
  campaignInteractionRules,
  campaignKpiContactsHref,
  encodeRulesParam,
  KPI_TO_BREVO_ACTION,
} from "./campaignDeepLink";

/** Decodifica el querystring `rules=` igual que hace `/contacts`
 *  (readUrlState): decodeURIComponent(atob(x)). */
function decodeRulesParam(param: string): unknown {
  return JSON.parse(decodeURIComponent(atob(param)));
}

describe("campaignDeepLink", () => {
  it("round-trip: encode → decode devuelve el mismo árbol", () => {
    const tree = campaignInteractionRules(48, "opened");
    const encoded = encodeRulesParam(tree);
    expect(decodeRulesParam(encoded)).toEqual(tree);
  });

  it("campaignKpiContactsHref genera /contacts?rules=<base64>", () => {
    const href = campaignKpiContactsHref(48, "clicked");
    expect(href).toMatch(/^\/contacts\?rules=/);

    const param = href.split("rules=")[1];
    const decoded = decodeRulesParam(param) as {
      operator: string;
      children: Array<{ field: string; comparator: string; value: unknown }>;
    };
    expect(decoded.operator).toBe("AND");
    expect(decoded.children).toHaveLength(1);
    expect(decoded.children[0]).toMatchObject({
      field: "brevo_campaign_interaction",
      comparator: "matches",
      value: { campaigns: [48], action: "clicked", period: "all" },
    });
  });

  it("mapea cada KPI de card a su action del filtro", () => {
    // 'delivered' reusa 'received'; 'complained' → 'spam'; 'bounces' →
    // 'bounced'. El resto es identidad.
    expect(KPI_TO_BREVO_ACTION.delivered).toBe("received");
    expect(KPI_TO_BREVO_ACTION.complained).toBe("spam");
    expect(KPI_TO_BREVO_ACTION.bounces).toBe("bounced");
    expect(KPI_TO_BREVO_ACTION.sent).toBe("sent");
    expect(KPI_TO_BREVO_ACTION.opened).toBe("opened");
  });

  it("usa el action mapeado (no el kpi crudo) en el filtro", () => {
    const href = campaignKpiContactsHref(99, "delivered");
    const decoded = decodeRulesParam(href.split("rules=")[1]) as {
      children: Array<{ value: { action: string } }>;
    };
    // 'delivered' → action 'received'.
    expect(decoded.children[0].value.action).toBe("received");
  });
});
