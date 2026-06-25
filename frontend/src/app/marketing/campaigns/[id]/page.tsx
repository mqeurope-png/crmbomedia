"use client";

import { ExternalLink } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { CampaignTimelineChart } from "../../../components/CampaignTimelineChart";
import { HtmlPreview } from "../../../components/HtmlPreview";
import { ConfirmDialog } from "../../../components/ConfirmDialog";
import { ErrorState } from "../../../components/ErrorState";
import { PageHeader } from "../../../components/PageHeader";
import {
  backfillBrevoCampaignRecipients,
  CAMPAIGN_STATUS_LABEL,
  campaignRates,
  campaignStatusClass,
  cancelBrevoCampaignSchedule,
  deleteBrevoCampaign,
  getBrevoCampaign,
  getBrevoCampaignRecipients,
  getBrevoCampaignTimeline,
  refreshBrevoCampaignStats,
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
  const [confirmBackfill, setConfirmBackfill] = useState(false);
  const [backfillBusy, setBackfillBusy] = useState(false);
  // Bug 6: estado del botón "Sincronizar stats" — paralelo a
  // backfill destinatarios.
  const [refreshStatsBusy, setRefreshStatsBusy] = useState(false);

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
            {/* Sprint Brevo Backfill: solo aplica a campañas ya enviadas.
                Encola un job RQ que tira de los recipients del export de
                Brevo. Bart restringe a manager+ desde el backend; aquí
                el botón sale para cualquiera que pueda ver la ficha y el
                403 sale del API si no tienen rol suficiente. */}
            {isSent ? (
              <button
                type="button"
                className="button secondary small"
                disabled={backfillBusy}
                onClick={() => setConfirmBackfill(true)}
              >
                {backfillBusy ? "Encolando…" : "Sincronizar destinatarios"}
              </button>
            ) : null}
            {/* Bug 6 fix (Bart 2026-06-25): para campañas recientes el
             * TTL de 5min hacía que la cabecera quedara en 0 incluso
             * tras recibir destinatarios via backfill. Botón paralelo
             * que fuerza refresh inmediato sin chequeo de edad. */}
            {isSent ? (
              <button
                type="button"
                className="button secondary small"
                disabled={refreshStatsBusy}
                onClick={async () => {
                  setRefreshStatsBusy(true);
                  setMessage(null);
                  try {
                    const fresh = await refreshBrevoCampaignStats(campaign.id);
                    setCampaign(fresh);
                    // PR-Fix-Regresiones-PR237 Bug 6. El endpoint OK
                    // no garantiza que Brevo tenga stats todavía —
                    // típicamente devuelve todo 0 durante 1-2h tras
                    // envío. Detectamos el caso "todo cero" y damos
                    // feedback honesto al admin en lugar de fingir
                    // éxito (que era lo que Bart veía: click sin
                    // cambios).
                    const freshStats =
                      (fresh.stats as Record<string, number> | null) ?? {};
                    const total = Object.values(freshStats).reduce(
                      (acc, v) => acc + (Number(v) || 0),
                      0,
                    );
                    if (total === 0) {
                      setMessage(
                        "Brevo aún no tiene stats disponibles para esta " +
                          "campaña — son normales en envíos recientes " +
                          "(<2 h). Vuelve a intentar más tarde.",
                      );
                    } else {
                      setMessage("Stats actualizadas desde Brevo.");
                    }
                  } catch (err) {
                    setMessage(
                      `No se pudieron refrescar las stats: ${
                        err instanceof Error ? err.message : String(err)
                      }`,
                    );
                  } finally {
                    setRefreshStatsBusy(false);
                  }
                }}
              >
                {refreshStatsBusy ? "Refrescando…" : "Sincronizar stats"}
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

      {/* Bug 5 fix (Bart 2026-06-25): KPIs ahora son clicables y
       * cambian la pestaña de "Destinatarios" abajo para mostrar la
       * lista correspondiente. Pasar a una página /contacts filtrada
       * con acciones masivas queda como follow-up (requiere fetch
       * full IDs y encodearlos en URL state). Mientras, la
       * navegación dentro del panel ya cubre el "ver quiénes". */}
      <section className="stats-grid" aria-label="Estadísticas">
        <article className="stat-card">
          <span>{stats.sent ?? "—"}</span>
          <p>Enviados</p>
        </article>
        <button
          type="button"
          className="stat-card stat-card-link"
          onClick={() => setRecipientTab("delivered")}
        >
          <span>{stats.delivered ?? "—"}</span>
          <p>Entregados</p>
        </button>
        <button
          type="button"
          className="stat-card stat-card-link"
          onClick={() => setRecipientTab("opened")}
        >
          <span>
            {stats.uniqueViews ?? stats.viewed ?? "—"}
            {rates.openRate != null ? ` (${rates.openRate}%)` : ""}
          </span>
          <p>Abiertos (OR)</p>
        </button>
        <button
          type="button"
          className="stat-card stat-card-link"
          onClick={() => setRecipientTab("clicked")}
        >
          <span>
            {stats.uniqueClicks ?? stats.clickers ?? "—"}
            {rates.clickRate != null ? ` (${rates.clickRate}%)` : ""}
          </span>
          <p>Clicks (CTR)</p>
        </button>
        <button
          type="button"
          className="stat-card stat-card-link"
          onClick={() => setRecipientTab("bounces")}
        >
          <span>
            {(stats.hardBounces ?? 0) + (stats.softBounces ?? 0) || "—"}
          </span>
          <p>Rebotes</p>
        </button>
        <button
          type="button"
          className="stat-card stat-card-link"
          onClick={() => setRecipientTab("unsubscribed")}
        >
          <span>{stats.unsubscriptions ?? "—"}</span>
          <p>Bajas</p>
        </button>
        <article className="stat-card">
          <span>{stats.complaints ?? "—"}</span>
          <p>Spam</p>
        </article>
      </section>

      <section className="panel">
        <h3>Contenido</h3>
        {campaign.html_content ? (
          <HtmlPreview html={campaign.html_content} />
        ) : (
          <p className="muted">
            Brevo no ha devuelto el HTML para esta campaña aún. Vuelve a
            cargar en unos segundos o abre la campaña en Brevo para
            editar el contenido.
          </p>
        )}
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
      <ConfirmDialog
        open={confirmBackfill}
        title="Sincronizar destinatarios"
        message={
          `Vamos a encolar un backfill de los recipients de "${campaign.name}". ` +
          "Tira del export de Brevo y rellena los eventos (delivered, opened, " +
          "clicked, bounces, unsubscribed) en la BD. Tarda ~5-10 min en background."
        }
        confirmLabel="Encolar"
        onConfirm={async () => {
          setConfirmBackfill(false);
          setBackfillBusy(true);
          setError(null);
          try {
            const enq = await backfillBrevoCampaignRecipients(
              campaign.brevo_campaign_id,
            );
            setMessage(
              `Sincronización en marcha (sync_log_id=${enq.sync_log_id ?? "—"}). ` +
                "Los eventos aparecerán en la pestaña 'Destinatarios por evento' en ~5-10 min.",
            );
          } catch (err) {
            setError(
              extractErrorMessage(
                err,
                "No se pudo encolar el backfill de destinatarios.",
              ),
            );
          } finally {
            setBackfillBusy(false);
          }
        }}
        onCancel={() => setConfirmBackfill(false)}
      />
    </main>
  );
}
