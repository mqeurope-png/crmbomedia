"use client";

import { useEffect, useState } from "react";
import { extractErrorMessage } from "../../lib/errors";
import {
  getWooWebhookStatus,
  regenerateWooWebhookSecret,
  type WooWebhookStatus,
} from "../../lib/erpApi";

/** B-3: panel de webhook de una tienda WooCommerce. Muestra la URL del
 *  receptor, el secreto enmascarado (últimos 4) con opción de regenerar, y
 *  las instrucciones para darlo de alta en el admin de WordPress. */
export function WooWebhookModal({
  storeId,
  storeName,
  onClose,
}: {
  storeId: string;
  storeName: string;
  onClose: () => void;
}) {
  const [status, setStatus] = useState<WooWebhookStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [revealed, setRevealed] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);

  useEffect(() => {
    getWooWebhookStatus(storeId)
      .then(setStatus)
      .catch((e) => setError(extractErrorMessage(e, "No se pudo cargar el estado del webhook.")));
  }, [storeId]);

  async function copy(text: string, key: string) {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(key);
      setTimeout(() => setCopied((k) => (k === key ? null : k)), 1500);
    } catch {
      setError("No se pudo copiar al portapapeles.");
    }
  }

  async function regenerate() {
    if (!window.confirm(
      "¿Regenerar el secret? El anterior dejará de validar hasta que lo " +
      "actualices en el admin de WordPress de la tienda.",
    )) return;
    setBusy(true);
    setError(null);
    try {
      const r = await regenerateWooWebhookSecret(storeId);
      setRevealed(r.webhook_secret);
      setStatus((s) => (s ? { ...s, webhook_secret_last4: r.webhook_secret.slice(-4) } : s));
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo regenerar el secret."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-overlay" role="dialog" aria-modal="true" aria-label={`Webhook de ${storeName}`}>
      <div className="modal-dialog">
        <h2>Webhook · {storeName}</h2>
        {error ? <p className="form-error">{error}</p> : null}
        {!status ? (
          <p className="muted">Cargando…</p>
        ) : (
          <>
            <div className="field">
              <span>Webhook URL</span>
              <div style={{ display: "flex", gap: 8 }}>
                <input type="text" readOnly value={status.webhook_url} aria-label="Webhook URL" />
                <button type="button" className="button small secondary"
                  onClick={() => copy(status.webhook_url, "url")}>
                  {copied === "url" ? "Copiado" : "Copiar"}
                </button>
              </div>
            </div>

            <div className="field">
              <span>Webhook Secret</span>
              {revealed ? (
                <div className="form-info" role="status">
                  <strong>Guarda este secret ahora</strong> — no volverá a ser visible
                  completo. Actualízalo en el admin de WordPress de la tienda para que
                  los webhooks sigan funcionando.
                  <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
                    <input type="text" readOnly value={revealed} aria-label="Nuevo secret" />
                    <button type="button" className="button small secondary"
                      onClick={() => copy(revealed, "secret")}>
                      {copied === "secret" ? "Copiado" : "Copiar"}
                    </button>
                    <button type="button" className="button small"
                      onClick={() => setRevealed(null)}>
                      Entendido
                    </button>
                  </div>
                </div>
              ) : (
                <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  <code aria-label="Secret enmascarado">{`••••••••${status.webhook_secret_last4}`}</code>
                  <button type="button" className="button small secondary"
                    onClick={regenerate} disabled={busy}>
                    {busy ? "Regenerando…" : "Regenerar"}
                  </button>
                </div>
              )}
            </div>

            <div className="muted small" style={{ marginTop: 8 }}>
              <p>
                Configura este webhook en tu WordPress admin → WooCommerce → Ajustes →
                Avanzado → Webhooks → Añadir. Crea uno por cada topic
                (<code>Order created</code>, <code>Order updated</code>,
                {" "}<code>Order payment complete</code>), todos apuntando a la misma URL
                y secret. Status: <strong>Active</strong>. API Version: <strong>v3</strong>.
              </p>
              <p>
                Recibidos 24h: <strong>{status.count_24h}</strong>
                {status.errors_24h > 0 ? (
                  <> · <span className="badge bad">{status.errors_24h} errores</span></>
                ) : null}
                {status.topics_received_24h.length > 0
                  ? ` · topics: ${status.topics_received_24h.join(", ")}`
                  : ""}
              </p>
            </div>
          </>
        )}
        <div className="modal-actions">
          <button type="button" className="button secondary" onClick={onClose}>
            Cerrar
          </button>
        </div>
      </div>
    </div>
  );
}
