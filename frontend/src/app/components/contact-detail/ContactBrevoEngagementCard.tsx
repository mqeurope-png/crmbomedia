"use client";

/**
 * Card de Resumen ficha contacto — Engagement con campañas Brevo.
 * PR-Dc. Pulla del endpoint nuevo
 * `GET /api/contacts/{id}/brevo-engagement` (PR-Dc backend) que
 * agrega los `activity_events` con `campaign_brevo_id IS NOT NULL`
 * por campaña.
 */
import { ArrowUpRight, MousePointerClick, Send, Sparkles } from "lucide-react";
import { useEffect, useState } from "react";
import { apiFetch } from "../../lib/api";

type BrevoEngagementRecent = {
  brevo_campaign_id: number;
  name: string | null;
  sent_at: string | null;
  status: "clicked" | "opened" | "no_open";
};

type BrevoEngagementSummary = {
  campaigns_total: number;
  opens: number;
  opens_pct: number;
  clicks: number;
  clicks_pct: number;
  recent: BrevoEngagementRecent[];
};

type Props = { contactId: string };

function formatDate(value: string | null): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString("es-ES", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}

function chipFor(
  status: BrevoEngagementRecent["status"],
): { label: string; tone: string } {
  if (status === "clicked") return { label: "Clickado", tone: "is-success" };
  if (status === "opened") return { label: "Abierto", tone: "is-info" };
  return { label: "No abierto", tone: "is-muted" };
}

export function ContactBrevoEngagementCard({ contactId }: Props) {
  const [data, setData] = useState<BrevoEngagementSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    apiFetch<BrevoEngagementSummary>(
      `/api/contacts/${contactId}/brevo-engagement`,
    )
      .then((res) => {
        if (!cancelled) setData(res);
      })
      .catch(() => {
        if (!cancelled) setError("No se pudo cargar el engagement Brevo.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [contactId]);

  return (
    <article className="card contact-summary-card">
      <header className="contact-summary-card-header">
        <h3>
          <Sparkles size={14} aria-hidden /> Engagement con campañas Brevo
        </h3>
      </header>
      {loading ? (
        <p className="muted small">Cargando…</p>
      ) : error ? (
        <p className="form-error">{error}</p>
      ) : !data || data.campaigns_total === 0 ? (
        <p className="muted small">Sin engagement con campañas todavía.</p>
      ) : (
        <>
          <div className="contact-summary-engagement">
            <div className="contact-summary-engagement-item">
              <span className="contact-summary-engagement-label">Campañas</span>
              <span className="contact-summary-engagement-value">
                {data.campaigns_total}
              </span>
              <span className="contact-summary-engagement-icon" aria-hidden>
                <Send size={14} />
              </span>
            </div>
            <div className="contact-summary-engagement-item">
              <span className="contact-summary-engagement-label">
                OR (aperturas)
              </span>
              <span className="contact-summary-engagement-value">
                {data.opens}
                <span className="muted small"> · {data.opens_pct}%</span>
              </span>
            </div>
            <div className="contact-summary-engagement-item">
              <span className="contact-summary-engagement-label">CTR (clics)</span>
              <span className="contact-summary-engagement-value">
                {data.clicks}
                <span className="muted small"> · {data.clicks_pct}%</span>
              </span>
              <span className="contact-summary-engagement-icon" aria-hidden>
                <MousePointerClick size={14} />
              </span>
            </div>
          </div>
          {data.recent.length > 0 ? (
            <ul className="contact-brevo-recent">
              {data.recent.map((c) => {
                const chip = chipFor(c.status);
                return (
                  <li key={c.brevo_campaign_id} className="contact-brevo-recent-row">
                    <span className="contact-brevo-recent-name">
                      {c.name ?? `Campaña #${c.brevo_campaign_id}`}
                    </span>
                    <span className="muted small">{formatDate(c.sent_at)}</span>
                    <span className={`chip ${chip.tone}`}>{chip.label}</span>
                    <a
                      href={`/marketing/campaigns/${c.brevo_campaign_id}`}
                      className="contact-brevo-recent-link"
                      aria-label={`Abrir ${c.name ?? "campaña"}`}
                    >
                      <ArrowUpRight size={12} aria-hidden />
                    </a>
                  </li>
                );
              })}
            </ul>
          ) : null}
        </>
      )}
    </article>
  );
}
