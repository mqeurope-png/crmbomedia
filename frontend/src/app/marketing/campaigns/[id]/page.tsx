"use client";

import { ExternalLink } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { CampaignTimelineChart } from "../../../components/CampaignTimelineChart";
import { ConfirmDialog } from "../../../components/ConfirmDialog";
import { ErrorState } from "../../../components/ErrorState";
import { PageHeader } from "../../../components/PageHeader";
import {
  CAMPAIGN_STATUS_LABEL,
  campaignRates,
  campaignStatusClass,
  cancelBrevoCampaignSchedule,
  deleteBrevoCampaign,
  getBrevoCampaign,
  getBrevoCampaignRecipients,
  getBrevoCampaignTimeline,
  scheduleBrevoCampaign,
  sendBrevoCampaignNow,
  sendBrevoCampaignTest,
  type BrevoCampaign,
  type BrevoCampaignRecipients,
  type BrevoCampaignTimeline,
} from "../../../lib/brevoApi";
import { extractErrorMessage } from "../../../lib/errors";

const RECIPIENT_TABS = [
  { key: "delivered", label: "Entregados" },
  { key: "opened", label: "Abiertos" },
  { key: "clicked", label: "Clicks" },
  { key: "bounces", label: "Rebotes" },
  { key: "unsubscribed", label: "Bajas" },
];

export default function CampaignDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const [campaign, setCampaign] = useState<BrevoCampaign | null>(null);
  const [timeline, setTimeline] = useState<BrevoCampaignTimeline | null>(null);
  const [recipientTab, setRecipientTab] = useState("delivered");
  const [recipients, setRecipients] = useState<BrevoCampaignRecipients | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const fresh = await getBrevoCampaign(params.id);
      setCampaign(fresh);
      setError(null);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo cargar la campaña."));
    } finally {
      setIsLoading(false);
    }
  }, [params.id]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    if (!campaign) return;
    getBrevoCampaignTimeline(campaign.id)
      .then(setTimeline)
      .catch(() => setTimeline(null));
  }, [campaign]);

  useEffect(() => {
    if (!campaign) return;
    getBrevoCampaignRecipients(campaign.id, recipientTab, { limit: 50 })
      .then(setRecipients)
      .catch(() => setRecipients(null));
  }, [campaign, recipientTab]);

  async function act(action: () => Promise<{ message: string }>) {
    setError(null);
    setMessage(null);
    try {
      const result = await action();
      setMessage(result.message);
      await refresh();
    } catch (err) {
      setError(extractErrorMessage(err, "La acción falló."));
    }
  }

  if (isLoading) {
    return (
      <main className="shell shell-wide">
        <PageHeader title="Campaña" eyebrow="Marketing" />
        <p className="muted">Cargando…</p>
      </main>
    );
  }
  if (error && !campaign) {
    return (
      <main className="shell shell-wide">
        <PageHeader
          title="Campaña"
          eyebrow="Marketing"
          crumbs={[{ label: "Campañas", href: "/marketing/campaigns" }]}
        />
        <ErrorState title="No se pudo cargar" message={error} />
      </main>
    );
  }
  if (!campaign) return null;

  const rates = campaignRates(campaign.stats);
  const stats = campaign.stats ?? {};
  const isDraft = campaign.status === "draft";
  const isQueued = campaign.status === "queued";
  const isSent = campaign.status === "sent";

  return (
    <main className="shell shell-wide">
      <PageHeader
        title={campaign.name}
        eyebrow="Campaña"
        description={campaign.subject ?? undefined}
        crumbs={[
          { label: "Campañas", href: "/marketing/campaigns" },
          { label: campaign.name },
        ]}
        actions={
          <>
            <span
              className={`status-pill ${campaignStatusClass(campaign.status)}`}
            >
              {CAMPAIGN_STATUS_LABEL[campaign.status] ?? campaign.status}
            </span>
            {isDraft || isQueued ? (
              <button
                type="button"
                className="button small"
                onClick={() => act(() => sendBrevoCampaignNow(campaign.id))}
              >
                Enviar ahora
              </button>
            ) : null}
            {isDraft ? (
              <button
                type="button"
                className="button secondary small"
                onClick={() => {
                  const value = window.prompt(
                    "Fecha/hora de envío (ISO, mínimo +1h). Ej: 2026-07-01T10:00",
                  );
                  if (value) {
                    act(() =>
                      scheduleBrevoCampaign(
                        campaign.id,
                        new Date(value).toISOString(),
                      ),
                    );
                  }
                }}
              >
                Programar
              </button>
            ) : null}
            {isQueued ? (
              <button
                type="button"
                className="button secondary small"
                onClick={() =>
                  act(() => cancelBrevoCampaignSchedule(campaign.id))
                }
              >
                Cancelar programación
              </button>
            ) : null}
            {isDraft || isQueued ? (
              <button
                type="button"
                className="button secondary small"
                onClick={() => {
                  const emails = window.prompt(
                    "Emails para el test (máx. 3, separados por coma):",
                  );
                  if (emails) {
                    act(() =>
                      sendBrevoCampaignTest(
                        campaign.id,
                        emails
                          .split(",")
                          .map((value) => value.trim())
                          .filter(Boolean)
                          .slice(0, 3),
                      ),
                    );
                  }
                }}
              >
                Enviar prueba
              </button>
            ) : null}
            {isDraft ? (
              <button
                type="button"
                className="button secondary small danger-text"
                onClick={() => setConfirmDelete(true)}
              >
                Borrar
              </button>
            ) : null}
            <a
              href={`https://app.brevo.com/camp/show/${campaign.brevo_campaign_id}`}
              target="_blank"
              rel="noreferrer"
              className="button secondary small"
            >
              <ExternalLink size={12} aria-hidden /> Abrir en Brevo
            </a>
          </>
        }
      />

      {error ? <p className="danger-text">{error}</p> : null}
      {message ? <div className="success-state">{message}</div> : null}

      <section className="stats-grid" aria-label="Estadísticas">
        <article className="stat-card">
          <span>{stats.sent ?? "—"}</span>
          <p>Enviados</p>
        </article>
        <article className="stat-card">
          <span>{stats.delivered ?? "—"}</span>
          <p>Entregados</p>
        </article>
        <article className="stat-card">
          <span>
            {stats.uniqueViews ?? stats.viewed ?? "—"}
            {rates.openRate != null ? ` (${rates.openRate}%)` : ""}
          </span>
          <p>Abiertos (OR)</p>
        </article>
        <article className="stat-card">
          <span>
            {stats.uniqueClicks ?? stats.clickers ?? "—"}
            {rates.clickRate != null ? ` (${rates.clickRate}%)` : ""}
          </span>
          <p>Clicks (CTR)</p>
        </article>
        <article className="stat-card">
          <span>
            {(stats.hardBounces ?? 0) + (stats.softBounces ?? 0) || "—"}
          </span>
          <p>Rebotes</p>
        </article>
        <article className="stat-card">
          <span>{stats.unsubscriptions ?? "—"}</span>
          <p>Bajas</p>
        </article>
        <article className="stat-card">
          <span>{stats.complaints ?? "—"}</span>
          <p>Spam</p>
        </article>
      </section>

      {isSent || timeline?.timeline.length ? (
        <section className="panel">
          <h3>Aperturas y clicks por día</h3>
          {timeline && timeline.timeline.length > 0 ? (
            <CampaignTimelineChart points={timeline.timeline} />
          ) : (
            <p className="muted">
              Aún no hay eventos de webhook para esta campaña. Configura el
              webhook de Brevo en /admin/integrations para alimentar este
              gráfico.
            </p>
          )}
        </section>
      ) : null}

      {timeline && timeline.top_clicks.length > 0 ? (
        <section className="panel">
          <h3>URLs más clickeadas</h3>
          <table className="data-table">
            <thead>
              <tr>
                <th>URL</th>
                <th>Clicks</th>
              </tr>
            </thead>
            <tbody>
              {timeline.top_clicks.map((row) => (
                <tr key={row.url}>
                  <td className="muted small">{row.url}</td>
                  <td>{row.count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ) : null}

      <section className="panel">
        <h3>Destinatarios por evento</h3>
        <div className="tab-bar">
          {RECIPIENT_TABS.map((tab) => (
            <button
              key={tab.key}
              type="button"
              className={`tab${recipientTab === tab.key ? " is-active" : ""}`}
              onClick={() => setRecipientTab(tab.key)}
            >
              {tab.label}
            </button>
          ))}
        </div>
        {recipients === null ? (
          <p className="muted">Cargando…</p>
        ) : recipients.items.length === 0 ? (
          <p className="muted">
            Sin eventos de este tipo todavía (se alimentan por webhook).
          </p>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Contacto</th>
                <th>Email</th>
                <th>Cuándo</th>
                <th>Detalle</th>
              </tr>
            </thead>
            <tbody>
              {recipients.items.map((item, index) => (
                <tr key={`${item.contact_id}-${index}`}>
                  <td>
                    <Link href={`/contacts/${item.contact_id}`}>
                      {[item.first_name, item.last_name]
                        .filter(Boolean)
                        .join(" ") || "(Sin nombre)"}
                    </Link>
                  </td>
                  <td className="muted small">{item.email ?? "—"}</td>
                  <td className="muted small">
                    {new Date(item.occurred_at).toLocaleString("es-ES")}
                  </td>
                  <td className="muted small">{item.detail ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <ConfirmDialog
        open={confirmDelete}
        title="Borrar campaña"
        message={`¿Borrar la campaña "${campaign.name}"? Se elimina también en Brevo.`}
        confirmLabel="Borrar"
        onConfirm={async () => {
          try {
            await deleteBrevoCampaign(campaign.id);
            router.push("/marketing/campaigns");
          } catch (err) {
            setError(extractErrorMessage(err, "No se pudo borrar."));
            setConfirmDelete(false);
          }
        }}
        onCancel={() => setConfirmDelete(false)}
      />
    </main>
  );
}
