"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import {
  campaignRates,
  listBrevoCampaigns,
  resolvePrimaryBrevoAccount,
  type BrevoCampaign,
} from "../lib/brevoApi";

/**
 * Dashboard card: last sent campaign, next scheduled one, 30-day
 * aggregates. Renders nothing when no Brevo account is configured so
 * tenants without marketing never see an empty box.
 */
export function EmailMarketingWidget() {
  const [campaigns, setCampaigns] = useState<BrevoCampaign[] | null>(null);
  const [hasAccount, setHasAccount] = useState<boolean | null>(null);

  useEffect(() => {
    resolvePrimaryBrevoAccount()
      .then(async (account) => {
        if (!account) {
          setHasAccount(false);
          return;
        }
        setHasAccount(true);
        setCampaigns(await listBrevoCampaigns(account));
      })
      .catch(() => setHasAccount(false));
  }, []);

  const summary = useMemo(() => {
    if (!campaigns) return null;
    const sent = campaigns
      .filter((c) => c.status === "sent" && c.sent_at)
      .sort(
        (a, b) =>
          new Date(b.sent_at as string).getTime() -
          new Date(a.sent_at as string).getTime(),
      );
    const scheduled = campaigns
      .filter((c) => c.status === "queued" && c.scheduled_at)
      .sort(
        (a, b) =>
          new Date(a.scheduled_at as string).getTime() -
          new Date(b.scheduled_at as string).getTime(),
      );
    const cutoff = Date.now() - 30 * 24 * 3600 * 1000;
    const recent = sent.filter(
      (c) => new Date(c.sent_at as string).getTime() >= cutoff,
    );
    let totalSent = 0;
    let orSum = 0;
    let ctrSum = 0;
    let rated = 0;
    for (const campaign of recent) {
      totalSent += campaign.stats?.sent ?? 0;
      const rates = campaignRates(campaign.stats);
      if (rates.openRate != null) {
        orSum += rates.openRate;
        ctrSum += rates.clickRate ?? 0;
        rated += 1;
      }
    }
    return {
      last: sent[0] ?? null,
      next: scheduled[0] ?? null,
      totalSent,
      avgOr: rated ? Math.round((orSum / rated) * 10) / 10 : null,
      avgCtr: rated ? Math.round((ctrSum / rated) * 10) / 10 : null,
    };
  }, [campaigns]);

  if (hasAccount === false || hasAccount === null) return null;

  return (
    <article className="card email-marketing-widget">
      <div className="section-title">
        <h2>Email marketing</h2>
        <Link href="/marketing/campaigns">Ir a campañas</Link>
      </div>
      {summary === null ? (
        <p className="muted">Cargando…</p>
      ) : (
        <>
          <ul className="item-list">
            <li>
              <strong>Última enviada</strong>
              <span className="muted small">
                {summary.last
                  ? `${summary.last.name} · ${new Date(
                      summary.last.sent_at as string,
                    ).toLocaleDateString("es-ES")}${
                      campaignRates(summary.last.stats).openRate != null
                        ? ` · OR ${campaignRates(summary.last.stats).openRate}%`
                        : ""
                    }`
                  : "Ninguna todavía"}
              </span>
            </li>
            <li>
              <strong>Próxima programada</strong>
              <span className="muted small">
                {summary.next
                  ? `${summary.next.name} · ${new Date(
                      summary.next.scheduled_at as string,
                    ).toLocaleString("es-ES")}`
                  : "Ninguna"}
              </span>
            </li>
            <li>
              <strong>Últimos 30 días</strong>
              <span className="muted small">
                {summary.totalSent.toLocaleString("es-ES")} enviados
                {summary.avgOr != null
                  ? ` · OR medio ${summary.avgOr}% · CTR medio ${summary.avgCtr}%`
                  : ""}
              </span>
            </li>
          </ul>
        </>
      )}
    </article>
  );
}
