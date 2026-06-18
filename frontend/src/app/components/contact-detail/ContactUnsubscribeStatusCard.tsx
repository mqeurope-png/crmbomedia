"use client";

/**
 * PR-Contact-Unsubscribe-Admin → PR-Editar-Completo.
 *
 * Card del sidebar de la ficha contacto que muestra si el contacto
 * está dado de baja de envíos comerciales (filas en
 * `email_unsubscribes`).
 *
 * Pre PR-Editar-Completo: este card también incluía el botón
 * "Reactivar envíos" (admin-only). Bart pidió mover la acción al
 * modal Editar para evitar duplicación; ahora el card es
 * informativo y dirige al operador a usar el modal.
 *
 * UX:
 *   - Si NO está dado de baja: card no se renderiza (cero ruido).
 *   - Si SÍ está dado de baja:
 *       * Badge "Dado de baja" con icono.
 *       * Fecha de la baja + scope.
 *       * Hint: "Reactiva desde el botón Editar del header".
 */
import { AlertTriangle, Ban } from "lucide-react";
import { useEffect, useState } from "react";
import {
  getContactUnsubscribeStatus,
  type ContactUnsubscribeStatus,
} from "../../lib/api";
import { formatBackendDateTime } from "../../lib/dates";

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
        <p className="muted small">
          Para reactivar, usa el botón <strong>Editar</strong> del header (solo
          admin).
        </p>
      </div>
    </div>
  );
}
