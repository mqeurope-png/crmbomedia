"use client";

import {
  Activity,
  Check,
  Copy,
  RefreshCw,
  Webhook,
  X,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import {
  deleteIntegrationAccountWebhookSecret,
  generateIntegrationAccountWebhookSecret,
  getIntegrationAccountWebhookStats,
  regenerateIntegrationAccountWebhookSecret,
  type IntegrationAccount,
  type WebhookStats,
} from "../lib/integrationSettings";
import { formatBackendDateTime } from "../lib/dates";
import { extractErrorMessage } from "../lib/errors";

type Props = {
  account: IntegrationAccount;
  isAdmin: boolean;
  onReload: () => Promise<void> | void;
  onError: (message: string | null) => void;
  onSuccess: (message: string | null) => void;
};

/** Sprint Webhooks Agile Real-Time. The "Webhook real-time" section
 *  on each AgileCRM account card.
 *
 *  - Without a secret: a "Generar URL de webhook" button + short
 *    instructions for the operator to paste into Agile.
 *  - With a secret: shows the masked URL (revealed on click), a
 *    "Copiar" button, "Regenerar", "Desactivar" + counters from
 *    `/webhook-stats`.
 *
 *  The secret is shown ONCE — right after generate/regenerate — and
 *  never re-fetched. After that, only `has_webhook_secret` is known
 *  and a fresh URL requires a regenerate (which also rotates the
 *  token Agile needs to know about). */
export function AgileWebhookPanel({
  account,
  isAdmin,
  onReload,
  onError,
  onSuccess,
}: Props) {
  const [stats, setStats] = useState<WebhookStats | null>(null);
  const [busy, setBusy] = useState(false);
  // Plaintext URL — only ever held in memory right after a generate
  // or regenerate. Never persisted; never re-fetched.
  const [revealedUrl, setRevealedUrl] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const loadStats = useCallback(async () => {
    try {
      setStats(
        await getIntegrationAccountWebhookStats(
          account.system,
          account.account_id,
        ),
      );
    } catch {
      // Stats are surface-level; silent failure is fine — the
      // operator can still rotate / generate from the same card.
      setStats(null);
    }
  }, [account.system, account.account_id]);

  useEffect(() => {
    void loadStats();
  }, [loadStats]);

  const onGenerate = useCallback(async () => {
    setBusy(true);
    onError(null);
    try {
      const res = await generateIntegrationAccountWebhookSecret(
        account.system,
        account.account_id,
      );
      setRevealedUrl(res.url);
      onSuccess(
        `URL generada para ${account.display_name}. Cópiala y pégala en Agile — solo se muestra una vez.`,
      );
      await loadStats();
      await onReload();
    } catch (err) {
      onError(
        extractErrorMessage(err, "No se pudo generar la URL del webhook."),
      );
    } finally {
      setBusy(false);
    }
  }, [
    account.system,
    account.account_id,
    account.display_name,
    loadStats,
    onError,
    onReload,
    onSuccess,
  ]);

  const onRegenerate = useCallback(async () => {
    if (
      !window.confirm(
        "Regenerar invalida la URL anterior — Agile dejará de entregar webhooks hasta que actualices la URL allí. ¿Continuar?",
      )
    ) {
      return;
    }
    setBusy(true);
    onError(null);
    try {
      const res = await regenerateIntegrationAccountWebhookSecret(
        account.system,
        account.account_id,
      );
      setRevealedUrl(res.url);
      onSuccess(
        "Secret rotado. Pega la nueva URL en Agile cuanto antes para no perder eventos.",
      );
      await loadStats();
      await onReload();
    } catch (err) {
      onError(
        extractErrorMessage(err, "No se pudo regenerar la URL del webhook."),
      );
    } finally {
      setBusy(false);
    }
  }, [
    account.system,
    account.account_id,
    loadStats,
    onError,
    onReload,
    onSuccess,
  ]);

  const onDelete = useCallback(async () => {
    if (
      !window.confirm(
        "Desactivar elimina el secret y rechaza todos los webhooks futuros desde Agile. ¿Continuar?",
      )
    ) {
      return;
    }
    setBusy(true);
    onError(null);
    try {
      await deleteIntegrationAccountWebhookSecret(
        account.system,
        account.account_id,
      );
      setRevealedUrl(null);
      onSuccess("Webhook desactivado.");
      await loadStats();
      await onReload();
    } catch (err) {
      onError(
        extractErrorMessage(err, "No se pudo desactivar el webhook."),
      );
    } finally {
      setBusy(false);
    }
  }, [
    account.system,
    account.account_id,
    loadStats,
    onError,
    onReload,
    onSuccess,
  ]);

  const onCopy = useCallback(async () => {
    if (!revealedUrl) return;
    try {
      await navigator.clipboard.writeText(revealedUrl);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      // clipboard write can fail under unusual permission setups;
      // surface a banner so the operator selects + copies manually.
      onError(
        "El portapapeles no está disponible. Copia la URL manualmente.",
      );
    }
  }, [revealedUrl, onError]);

  const hasSecret = stats?.has_secret ?? account.has_webhook_secret ?? false;
  const last = stats?.last_received_at ?? account.webhook_last_received_at;
  const isStale = (() => {
    if (!last) return false;
    const lastMs = Date.parse(last);
    if (Number.isNaN(lastMs)) return false;
    return Date.now() - lastMs > 7 * 24 * 3600 * 1000;
  })();

  return (
    <section className="form-card embedded agile-webhook-block">
      <h3>
        <Webhook size={14} aria-hidden /> Webhook real-time
      </h3>
      <p className="muted small">
        Recibe contactos de Agile en cuestión de segundos.{" "}
        {hasSecret ? (
          <>
            Estado:{" "}
            {isStale ? (
              <span className="badge warn">
                Sin eventos los últimos 7 días
              </span>
            ) : last ? (
              <span className="badge ok">Activo</span>
            ) : (
              <span className="badge muted">Pendiente de primer evento</span>
            )}
          </>
        ) : (
          <span className="badge muted">Sin configurar</span>
        )}
      </p>

      {revealedUrl ? (
        <div className="agile-webhook-revealed">
          <label className="small">
            URL para pegar en Agile (se muestra una sola vez):
            <input
              type="text"
              readOnly
              value={revealedUrl}
              onClick={(e) => (e.target as HTMLInputElement).select()}
            />
          </label>
          <div className="actions">
            <button
              type="button"
              className="button"
              onClick={onCopy}
              disabled={busy}
            >
              {copied ? (
                <>
                  <Check size={12} aria-hidden /> Copiado
                </>
              ) : (
                <>
                  <Copy size={12} aria-hidden /> Copiar URL
                </>
              )}
            </button>
            <button
              type="button"
              className="button secondary small"
              onClick={() => setRevealedUrl(null)}
            >
              Ocultar
            </button>
          </div>
          <p className="muted small">
            Ve a Agile → Admin Settings → Integrations → Webhooks →
            New Webhook. Pega la URL. Marca los eventos{" "}
            <code>add_contact</code>, <code>update_contact</code>,{" "}
            <code>delete_contact</code>. Guarda.
          </p>
        </div>
      ) : null}

      {stats ? (
        <ul className="agile-webhook-stats">
          <li>
            <Activity size={11} aria-hidden /> {stats.received_today} hoy /
            {" "}
            {stats.received_total} total
          </li>
          <li>
            Últimas 24 h: {stats.processed_last_24h}/
            {stats.received_last_24h} procesados
            {stats.success_rate_last_24h !== null
              ? ` (${Math.round(stats.success_rate_last_24h * 100)}%)`
              : ""}
          </li>
          <li>
            Último evento:{" "}
            {last ? formatBackendDateTime(last) : "Aún ninguno"}
          </li>
        </ul>
      ) : null}

      {isAdmin ? (
        <div className="actions">
          {!hasSecret ? (
            <button
              type="button"
              className="button"
              onClick={onGenerate}
              disabled={busy}
            >
              <Webhook size={12} aria-hidden /> Generar URL de webhook
            </button>
          ) : (
            <>
              <button
                type="button"
                className="button secondary"
                onClick={onRegenerate}
                disabled={busy}
              >
                <RefreshCw size={12} aria-hidden /> Regenerar secret
              </button>
              <button
                type="button"
                className="button secondary"
                onClick={onDelete}
                disabled={busy}
              >
                <X size={12} aria-hidden /> Desactivar
              </button>
            </>
          )}
        </div>
      ) : null}
    </section>
  );
}
