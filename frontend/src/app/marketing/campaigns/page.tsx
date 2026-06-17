"use client";

import { Plus, RefreshCw, Sparkles } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { ErrorState } from "../../components/ErrorState";
import { PageHeader } from "../../components/PageHeader";
import { getCurrentUser } from "../../lib/api";
import {
  backfillMissingBrevoCampaigns,
  CAMPAIGN_STATUS_LABEL as STATUS_LABEL,
  campaignRates,
  campaignStatusClass,
  listBrevoCampaigns,
  resolvePrimaryBrevoAccount,
  type BrevoCampaign,
} from "../../lib/brevoApi";
import { extractErrorMessage } from "../../lib/errors";

export default function MarketingCampaignsPage() {
  const [accountId, setAccountId] = useState<string | null>(null);
  const [resolved, setResolved] = useState(false);
  const [campaigns, setCampaigns] = useState<BrevoCampaign[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [statusFilter, setStatusFilter] = useState("");
  const [query, setQuery] = useState("");
  const [isAdmin, setIsAdmin] = useState(false);
  const [backfillBusy, setBackfillBusy] = useState(false);
  const [confirmBackfillAll, setConfirmBackfillAll] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    getCurrentUser()
      .then((u) => setIsAdmin(u?.role === "admin"))
      .catch(() => undefined);
  }, []);

  const load = useCallback(async (account: string, refresh = false) => {
    try {
      const rows = await listBrevoCampaigns(account, { refresh });
      setCampaigns(rows);
      setError(null);
    } catch (err) {
      setError(
        extractErrorMessage(err, "No se pudieron cargar las campañas."),
      );
    }
  }, []);

  useEffect(() => {
    resolvePrimaryBrevoAccount()
      .then(async (account) => {
        setAccountId(account);
        if (account) await load(account);
      })
      .catch(() => setError("No se pudo resolver la cuenta Brevo."))
      .finally(() => {
        setResolved(true);
        setIsLoading(false);
      });
  }, [load]);

  const filtered = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return campaigns
      .filter((c) => (statusFilter ? c.status === statusFilter : true))
      .filter((c) =>
        normalized
          ? c.name.toLowerCase().includes(normalized) ||
            (c.subject ?? "").toLowerCase().includes(normalized)
          : true,
      );
  }, [campaigns, statusFilter, query]);

  if (isLoading) {
    return (
      <main className="shell shell-wide">
        <PageHeader title="Campañas de email" eyebrow="Marketing" />
        <p className="muted">Cargando…</p>
      </main>
    );
  }

  if (resolved && !accountId) {
    return (
      <main className="shell shell-wide">
        <PageHeader title="Campañas de email" eyebrow="Marketing" />
        <ErrorState
          title="Brevo no configurado"
          message="Configura una cuenta Brevo en /admin/integrations para usar el módulo de marketing."
        />
      </main>
    );
  }

  return (
    <main className="shell shell-wide">
      <PageHeader
        title="Campañas de email"
        eyebrow="Marketing"
        description="Campañas de Brevo gestionadas desde el CRM: crea, programa, envía y mide sin salir de aquí."
        actions={
          <>
            {isAdmin ? (
              <button
                type="button"
                className="button secondary small"
                disabled={backfillBusy || !accountId}
                onClick={() => setConfirmBackfillAll(true)}
                title="Encola backfill de campañas sent sin events en BD"
              >
                <Sparkles size={12} aria-hidden />{" "}
                {backfillBusy
                  ? "Encolando…"
                  : "Sincronizar todas sin destinatarios"}
              </button>
            ) : null}
            <button
              type="button"
              className="button secondary small"
              disabled={refreshing || !accountId}
              onClick={async () => {
                if (!accountId) return;
                setRefreshing(true);
                await load(accountId, true);
                setRefreshing(false);
              }}
            >
              <RefreshCw size={12} aria-hidden />{" "}
              {refreshing ? "Refrescando…" : "Refrescar"}
            </button>
            <Link href="/marketing/campaigns/new" className="button small">
              <Plus size={12} aria-hidden /> Nueva campaña
            </Link>
          </>
        }
      />

      {message ? <div className="success-state">{message}</div> : null}
      {error ? <ErrorState title="Error" message={error} /> : null}

      <div className="marketing-filters">
        <input
          type="search"
          placeholder="Buscar por nombre o asunto…"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <select
          value={statusFilter}
          onChange={(event) => setStatusFilter(event.target.value)}
        >
          <option value="">Cualquier estado</option>
          {Object.entries(STATUS_LABEL).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </div>

      {filtered.length === 0 ? (
        <p className="muted">
          {campaigns.length === 0
            ? "No hay campañas todavía. Crea la primera o pulsa Refrescar."
            : "Ninguna campaña coincide con los filtros."}
        </p>
      ) : (
        <div className="table-wrapper">
          <table className="data-table">
            <thead>
              <tr>
                <th>Nombre</th>
                <th>Asunto</th>
                <th>Estado</th>
                <th>Fecha</th>
                <th>OR%</th>
                <th>CTR%</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((campaign) => {
                const rates = campaignRates(campaign.stats);
                return (
                  <tr key={campaign.id}>
                    <td>
                      <Link href={`/marketing/campaigns/${campaign.id}`}>
                        <strong>{campaign.name}</strong>
                      </Link>
                    </td>
                    <td className="muted">{campaign.subject ?? "—"}</td>
                    <td>
                      <span
                        className={`status-pill ${campaignStatusClass(campaign.status)}`}
                      >
                        {STATUS_LABEL[campaign.status] ?? campaign.status}
                      </span>
                    </td>
                    <td className="muted small">
                      {campaign.sent_at
                        ? new Date(campaign.sent_at).toLocaleString("es-ES")
                        : campaign.scheduled_at
                          ? `→ ${new Date(campaign.scheduled_at).toLocaleString("es-ES")}`
                          : "—"}
                    </td>
                    <td>{rates.openRate != null ? `${rates.openRate}%` : "—"}</td>
                    <td>{rates.clickRate != null ? `${rates.clickRate}%` : "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <ConfirmDialog
        open={confirmBackfillAll}
        title="Sincronizar campañas sin destinatarios"
        message={
          "Vamos a buscar campañas enviadas que no tengan eventos en la BD " +
          "(huecos del backfill histórico o campañas previas al webhook) y " +
          "encolar un único job RQ para procesarlas en serial. Puede tardar " +
          "varias horas si el hueco es grande."
        }
        confirmLabel="Encolar"
        onConfirm={async () => {
          setConfirmBackfillAll(false);
          if (!accountId) return;
          setBackfillBusy(true);
          setError(null);
          setMessage(null);
          try {
            const enq = await backfillMissingBrevoCampaigns(accountId);
            if (enq.status === "skipped") {
              setMessage(
                "No hay campañas pendientes. Todas las sent tienen events en BD.",
              );
            } else {
              setMessage(
                `Backfill encolado para ${enq.campaigns_to_process ?? 0} ` +
                  `campañas (sync_log_id=${enq.sync_log_id}).`,
              );
            }
          } catch (err) {
            setError(
              extractErrorMessage(
                err,
                "No se pudo encolar el backfill global.",
              ),
            );
          } finally {
            setBackfillBusy(false);
          }
        }}
        onCancel={() => setConfirmBackfillAll(false)}
      />
    </main>
  );
}
