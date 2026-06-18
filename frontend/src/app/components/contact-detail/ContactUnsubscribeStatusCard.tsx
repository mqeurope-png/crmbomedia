"use client";

/**
 * PR-Contact-Unsubscribe-Admin. Card del sidebar de la ficha contacto
 * que muestra si el contacto está dado de baja de envíos comerciales
 * (filas en `email_unsubscribes`) y deja al admin reactivarlo.
 *
 * Contexto: pre-PR el operador veía un 422 al intentar enviar email
 * ("Este contacto se ha dado de baja...") sin tener UI para
 * gestionar el estado. Ahora el card lo expone con badge + acción.
 *
 * UX:
 *   - Si NO está dado de baja: card no se renderiza (ruido evitado).
 *   - Si SÍ está dado de baja:
 *       * Badge "Dado de baja" con icono.
 *       * Fecha de la baja + scope (marketing / all).
 *       * Botón "Reactivar envíos" (admin-only).
 *       * Confirmación inline antes del DELETE.
 */
import { AlertTriangle, Ban, CheckCircle2 } from "lucide-react";
import { useEffect, useState } from "react";
import {
  clearContactUnsubscribes,
  getContactUnsubscribeStatus,
  getCurrentUser,
  type ContactUnsubscribeStatus,
} from "../../lib/api";
import { formatBackendDateTime } from "../../lib/dates";
import { extractErrorMessage } from "../../lib/errors";

type Props = {
  contactId: string;
  /** Bump para forzar refetch — el page lo incrementa tras enviar
   *  un email para que el card refleje el último estado conocido. */
  refreshKey?: number;
};

const SCOPE_LABEL: Record<string, string> = {
  marketing: "comerciales",
  all: "todos los envíos",
  transactional: "transaccionales",
};

export function ContactUnsubscribeStatusCard({
  contactId,
  refreshKey = 0,
}: Props) {
  const [status, setStatus] = useState<ContactUnsubscribeStatus | null>(null);
  const [isAdmin, setIsAdmin] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    getCurrentUser()
      .then((u) => setIsAdmin(u.role === "admin"))
      .catch(() => setIsAdmin(false));
  }, []);

  useEffect(() => {
    let cancelled = false;
    getContactUnsubscribeStatus(contactId)
      .then((data) => {
        if (!cancelled) setStatus(data);
      })
      .catch(() => {
        if (!cancelled) setStatus(null);
      });
    return () => {
      cancelled = true;
    };
  }, [contactId, refreshKey]);

  if (!status || !status.is_unsubscribed) {
    return null;
  }

  const latest = status.rows[0];
  const scopeLabel =
    SCOPE_LABEL[latest?.scope ?? "marketing"] ?? latest?.scope ?? "envíos";

  async function handleConfirm() {
    if (submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      await clearContactUnsubscribes(contactId);
      setMessage("Envíos reactivados. El contacto vuelve a recibir emails.");
      // Reload status para que el card desaparezca.
      const fresh = await getContactUnsubscribeStatus(contactId);
      setStatus(fresh);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo reactivar el contacto."));
    } finally {
      setSubmitting(false);
      setConfirming(false);
    }
  }

  return (
    <div
      className="contact-card contact-sidebar-card contact-unsubscribe-card"
      role="status"
    >
      <header className="contact-sidebar-card-header">
        <Ban size={14} aria-hidden />
        <h3>Estado de envíos</h3>
      </header>
      <div className="contact-unsubscribe-body">
        <p className="contact-unsubscribe-banner">
          <AlertTriangle size={12} aria-hidden /> Dado de baja de{" "}
          <strong>{scopeLabel}</strong>
        </p>
        {latest ? (
          <p className="muted small">
            Desde {formatBackendDateTime(latest.unsubscribed_at)} · origen:{" "}
            {latest.source}
          </p>
        ) : null}
        {status.rows.length > 1 ? (
          <p className="muted small">
            ({status.rows.length} registros de baja en total)
          </p>
        ) : null}

        {message ? (
          <p className="form-success small">
            <CheckCircle2 size={12} aria-hidden /> {message}
          </p>
        ) : null}
        {error ? <p className="form-error small">{error}</p> : null}

        {isAdmin ? (
          confirming ? (
            <div className="contact-unsubscribe-confirm">
              <p className="small">
                ¿Reactivar envíos para este contacto? La baja queda anulada en
                BD y el contacto pasa a aceptar emails comerciales otra vez.
              </p>
              <div className="contact-unsubscribe-actions">
                <button
                  type="button"
                  className="button secondary small"
                  onClick={() => setConfirming(false)}
                  disabled={submitting}
                >
                  Cancelar
                </button>
                <button
                  type="button"
                  className="button small"
                  onClick={handleConfirm}
                  disabled={submitting}
                >
                  {submitting ? "Reactivando…" : "Reactivar"}
                </button>
              </div>
            </div>
          ) : (
            <button
              type="button"
              className="button secondary small"
              onClick={() => setConfirming(true)}
            >
              Reactivar envíos
            </button>
          )
        ) : (
          <p className="muted small">
            Solo un admin puede reactivar el contacto.
          </p>
        )}
      </div>
    </div>
  );
}
