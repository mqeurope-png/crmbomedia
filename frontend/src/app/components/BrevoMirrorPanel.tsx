"use client";

import { ExternalLink, RefreshCw } from "lucide-react";
import { useState } from "react";
import { brevoMirrorParts, type Segment } from "../lib/api";
import { refreshBrevoSegment } from "../lib/brevoApi";
import { extractErrorMessage } from "../lib/errors";

type Props = {
  segment: Segment;
  onRefreshed: () => void | Promise<void>;
};

/**
 * Replaces the rule editor on segments whose membership is managed in
 * Brevo (`segment.external_source` matches `brevo:*`). Brevo's API
 * doesn't expose the filter tree — only the current member list — so
 * editing rules from the CRM would be misleading. The panel explains
 * the model, shows the last-refresh stamp, lets the operator force a
 * refresh, and deeplinks into Brevo for the actual filter edit.
 */
export function BrevoMirrorPanel({ segment, onRefreshed }: Props) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const parts = brevoMirrorParts(segment);

  async function handleRefresh() {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      await refreshBrevoSegment(segment.id);
      setMessage(
        "Refresco encolado. La membresía aparecerá actualizada en unos segundos.",
      );
      // Give the worker a tiny head-start before re-fetching.
      window.setTimeout(() => onRefreshed(), 1500);
    } catch (err) {
      setError(
        extractErrorMessage(err, "No se pudo encolar el refresco."),
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="brevo-mirror-panel">
      <header>
        <div>
          <span className="status-pill is-on">Espejo Brevo</span>
          <p className="muted small">
            Las reglas de este segmento se gestionan en Brevo. La membresía
            se actualiza automáticamente cada{" "}
            {segment.external_refresh_interval_minutes ?? 360}{" "}
            minutos y al pulsar &ldquo;Refrescar ahora&rdquo;.
          </p>
        </div>
        <div className="actions">
          <button
            type="button"
            className="button"
            disabled={busy}
            onClick={handleRefresh}
          >
            <RefreshCw size={12} aria-hidden />{" "}
            {busy ? "Encolando…" : "Refrescar ahora desde Brevo"}
          </button>
          {parts ? (
            <a
              href={`https://app.brevo.com/contact/segment-details/${parts.brevoSegmentId}`}
              target="_blank"
              rel="noreferrer"
              className="button secondary"
            >
              <ExternalLink size={12} aria-hidden /> Abrir en Brevo
            </a>
          ) : null}
        </div>
      </header>
      {error ? <p className="danger-text">{error}</p> : null}
      {message ? <div className="success-state">{message}</div> : null}
      <dl className="brevo-mirror-stats">
        <div>
          <dt>Contactos sincronizados</dt>
          <dd>{segment.cached_count ?? "?"}</dd>
        </div>
        <div>
          <dt>Último refresco</dt>
          <dd className="muted">
            {segment.external_last_refreshed_at
              ? new Date(segment.external_last_refreshed_at).toLocaleString(
                  "es-ES",
                )
              : "Nunca"}
          </dd>
        </div>
        <div>
          <dt>ID Brevo</dt>
          <dd className="muted small">
            <code>{parts?.brevoSegmentId ?? segment.external_source}</code>
          </dd>
        </div>
      </dl>
      <p className="muted small">
        Para editar las reglas, abre el segmento en Brevo. Los cambios se
        reflejan aquí en el siguiente refresco. Los contactos que están en
        el segmento Brevo pero no existen aún en el CRM se ignoran — no se
        crean contactos desde aquí.
      </p>
    </section>
  );
}
