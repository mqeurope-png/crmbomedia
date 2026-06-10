"use client";

import { History, Play } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  getBrevoHistoricalBackfillStatus,
  triggerBrevoHistoricalBackfill,
  type BrevoBackfillStatus,
} from "../lib/brevoApi";
import { extractErrorMessage } from "../lib/errors";

type Props = {
  accountId: string;
  onError: (message: string | null) => void;
  onMessage: (message: string | null) => void;
};

/**
 * Historical events backfill — the live webhook only fires from the
 * day it was configured. This section reads past campaigns from the
 * Brevo API and materialises the missing `activity_events` so each
 * contact's "Actividad email" timeline goes back to its real history.
 *
 * Admin-only: parent panel hides it from non-admins via `isAdmin`.
 * Polls the status endpoint every 8s while the run is in progress so
 * the operator sees the live counters land.
 */
export function BrevoBackfillSection({
  accountId,
  onError,
  onMessage,
}: Props) {
  const [status, setStatus] = useState<BrevoBackfillStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const refresh = useCallback(async () => {
    try {
      setStatus(await getBrevoHistoricalBackfillStatus(accountId));
    } catch (err) {
      onError(
        extractErrorMessage(err, "No se pudo cargar el estado del backfill."),
      );
    }
  }, [accountId, onError]);

  useEffect(() => {
    refresh();
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [refresh]);

  useEffect(() => {
    const isActive =
      status?.status === "pending" || status?.status === "running";
    if (!isActive) return;
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      refresh();
    }, 8000);
    return () => {
      if (timer.current) {
        clearTimeout(timer.current);
        timer.current = null;
      }
    };
  }, [status, refresh]);

  async function launch() {
    setBusy(true);
    onError(null);
    onMessage(null);
    try {
      await triggerBrevoHistoricalBackfill(accountId);
      onMessage(
        "Backfill encolado. Esto puede tardar 10-30 minutos en cuentas con muchas campañas.",
      );
      setConfirming(false);
      await refresh();
    } catch (err) {
      onError(
        extractErrorMessage(err, "No se pudo lanzar el backfill histórico."),
      );
    } finally {
      setBusy(false);
    }
  }

  const isActive =
    status?.status === "pending" || status?.status === "running";
  const lastFinished =
    status?.finished_at
      ? new Date(status.finished_at).toLocaleString("es-ES")
      : null;

  return (
    <section className="brevo-panel-section">
      <header>
        <h3>
          <History size={14} aria-hidden /> Historial de eventos (backfill)
        </h3>
      </header>
      <p className="muted small">
        Recupera del API de Brevo las aperturas, clicks, rebotes y bajas
        de campañas pasadas y las añade al historial de cada contacto del
        CRM. Útil sólo si quieres ver lo ocurrido <strong>antes</strong>{" "}
        de configurar el webhook — Brevo no envía eventos en vivo de
        campañas históricas.
      </p>

      {status?.status === "never" ? (
        <p className="muted small">Aún no se ha ejecutado ningún backfill.</p>
      ) : status ? (
        <p className="muted small">
          Último backfill:{" "}
          {isActive ? (
            <strong>En curso…</strong>
          ) : (
            <>
              {lastFinished ?? "—"} ·{" "}
              <strong>{status.campaigns_processed ?? 0}</strong> campañas ·{" "}
              <strong>{status.events_inserted_total ?? 0}</strong> eventos
              importados
              {(status.events_skipped_total ?? 0) > 0 ? (
                <>
                  {" "}
                  · {status.events_skipped_total} ya existentes
                </>
              ) : null}
              {(status.contacts_unknown_total ?? 0) > 0 ? (
                <>
                  {" "}
                  · {status.contacts_unknown_total} emails sin contacto
                </>
              ) : null}
              {(status.records_failed ?? 0) > 0 ? (
                <>
                  {" "}
                  · <span className="danger-text">
                    {status.records_failed} errores
                  </span>
                </>
              ) : null}
            </>
          )}
        </p>
      ) : null}

      {confirming ? (
        <div className="brevo-backfill-confirm">
          <p>
            Vas a procesar todas las campañas Brevo enviadas. Esto puede
            tardar varios minutos y consumir cuota del API de Brevo. La
            operación es <strong>idempotente</strong> — re-ejecutarla no
            duplica eventos.
          </p>
          <div className="actions">
            <button
              type="button"
              className="button"
              disabled={busy}
              onClick={launch}
            >
              <Play size={11} aria-hidden /> Sí, lanzar
            </button>
            <button
              type="button"
              className="button secondary"
              disabled={busy}
              onClick={() => setConfirming(false)}
            >
              Cancelar
            </button>
          </div>
        </div>
      ) : (
        <div className="actions">
          <button
            type="button"
            className="button"
            disabled={busy || isActive}
            onClick={() => setConfirming(true)}
          >
            <Play size={11} aria-hidden />{" "}
            {isActive ? "Procesando…" : "Lanzar backfill histórico"}
          </button>
        </div>
      )}
    </section>
  );
}
