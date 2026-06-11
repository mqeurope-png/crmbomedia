"use client";

import { AlertTriangle, CheckCircle2, Plug } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import {
  disconnectGoogle,
  getGoogleStatus,
  startGoogleConnect,
  type GoogleStatus,
} from "../lib/googleApi";
import { extractErrorMessage } from "../lib/errors";

/** Google Calendar block inside /account.
 *
 * Three visible states:
 *   - admin hasn't configured GOOGLE_OAUTH_* → just shows a hint.
 *   - user not connected → CTA to start the OAuth flow.
 *   - user connected → either prompts for calendar selection or
 *     shows the chosen calendar + change/disconnect controls.
 */
export function GoogleCalendarSection() {
  const [status, setStatus] = useState<GoogleStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      const data = await getGoogleStatus();
      setStatus(data);
      setError(null);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo cargar el estado."));
    }
  }, []);

  useEffect(() => {
    reload().finally(() => setLoading(false));
  }, [reload]);

  async function handleConnect() {
    setBusy(true);
    setError(null);
    try {
      await startGoogleConnect();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo iniciar la conexión."));
      setBusy(false);
    }
  }

  async function handleDisconnect() {
    if (
      !window.confirm(
        "¿Desconectar Google Calendar? Las tareas ya sincronizadas seguirán existiendo pero no se actualizarán más.",
      )
    ) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await disconnectGoogle();
      await reload();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo desconectar."));
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <p className="muted small">Cargando…</p>;
  if (!status) return null;

  if (!status.configured) {
    return (
      <p className="muted small">
        Tu administrador todavía no ha configurado las credenciales OAuth de
        Google. Pídele que añada <code>GOOGLE_OAUTH_CLIENT_ID</code>,{" "}
        <code>GOOGLE_OAUTH_CLIENT_SECRET</code> y{" "}
        <code>GOOGLE_OAUTH_REDIRECT_URI</code> en el entorno.
      </p>
    );
  }

  if (error) {
    return (
      <>
        <p className="form-error">{error}</p>
        <button
          className="button small secondary"
          type="button"
          onClick={() => {
            setError(null);
            setLoading(true);
            reload().finally(() => setLoading(false));
          }}
        >
          Reintentar
        </button>
      </>
    );
  }

  if (!status.connected) {
    return (
      <>
        <p className="muted small">
          Conecta tu cuenta de Google para sincronizar tus tareas con tu
          calendario.
        </p>
        <button
          className="button small"
          type="button"
          onClick={handleConnect}
          disabled={busy}
        >
          <Plug size={11} aria-hidden /> Conectar cuenta Google
        </button>
      </>
    );
  }

  return (
    <>
      <p className="muted small">
        Cuenta: <strong>{status.google_email}</strong>
      </p>
      {status.requires_calendar_selection ? (
        <>
          <p className="form-warning">
            <AlertTriangle size={11} aria-hidden /> Falta elegir calendario
            donde sincronizar.
          </p>
          <div className="actions">
            <Link className="button small" href="/account/google-setup">
              Elegir calendario
            </Link>
            <button
              className="button small danger"
              type="button"
              onClick={handleDisconnect}
              disabled={busy}
            >
              Desconectar
            </button>
          </div>
        </>
      ) : (
        <>
          <p className="muted small">
            <CheckCircle2 size={11} aria-hidden /> Calendario:{" "}
            <strong>{status.selected_calendar?.summary ?? "—"}</strong>
          </p>
          <div className="actions">
            <Link className="button small secondary" href="/account/google-setup">
              Cambiar calendario
            </Link>
            <button
              className="button small danger"
              type="button"
              onClick={handleDisconnect}
              disabled={busy}
            >
              Desconectar
            </button>
          </div>
        </>
      )}
    </>
  );
}
