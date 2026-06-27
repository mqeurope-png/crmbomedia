"use client";

import {
  AlertTriangle,
  CheckCircle2,
  Plug,
  RefreshCw,
} from "lucide-react";
import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import {
  disconnectGoogle,
  getGoogleStatus,
  startGoogleConnect,
  type GoogleStatus,
} from "../lib/googleApi";
import { extractErrorMessage } from "../lib/errors";

/** PR-OAuth-Google-Unificado. Bloque admin del estado de la conexión
 *  Google ORG (una sola cuenta compartida por todo el equipo). Vive en
 *  /admin/integrations y solo lo ve el admin.
 *
 *  Aquí se gestiona el CICLO DE VIDA de la conexión (conectar /
 *  reconectar / desconectar). La selección de calendario y los aliases
 *  Send-As siguen siendo per-user y viven en /account. */
export function OrgGoogleIntegrationSection({
  onError,
  onMessage,
}: {
  onError?: (msg: string | null) => void;
  onMessage?: (msg: string | null) => void;
}) {
  const searchParams = useSearchParams();
  const [status, setStatus] = useState<GoogleStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);
  const [connectedToast, setConnectedToast] = useState(false);

  const reload = useCallback(async () => {
    try {
      setStatus(await getGoogleStatus());
      setLocalError(null);
    } catch (err) {
      setLocalError(
        extractErrorMessage(err, "No se pudo cargar el estado de Google."),
      );
    }
  }, []);

  useEffect(() => {
    reload().finally(() => setLoading(false));
  }, [reload]);

  // "?google_connected=1" aterriza tras el callback OAuth (el backend
  // redirige aquí al admin que conectó la cuenta org).
  useEffect(() => {
    if (searchParams?.get("google_connected") === "1") {
      setConnectedToast(true);
    }
  }, [searchParams]);

  async function handleConnect() {
    setBusy(true);
    setLocalError(null);
    onError?.(null);
    try {
      await startGoogleConnect();
    } catch (err) {
      const msg = extractErrorMessage(
        err,
        "No se pudo iniciar la conexión con Google.",
      );
      setLocalError(msg);
      onError?.(msg);
      setBusy(false);
    }
  }

  async function handleDisconnect() {
    if (
      !window.confirm(
        "¿Desconectar la cuenta Google de la organización? El sync de emails y calendario de TODO el equipo se detendrá hasta que vuelvas a conectar. Las tareas y emails ya sincronizados se conservan.",
      )
    ) {
      return;
    }
    setBusy(true);
    setLocalError(null);
    onError?.(null);
    try {
      await disconnectGoogle();
      await reload();
      onMessage?.("Cuenta Google de la organización desconectada.");
    } catch (err) {
      const msg = extractErrorMessage(err, "No se pudo desconectar.");
      setLocalError(msg);
      onError?.(msg);
    } finally {
      setBusy(false);
    }
  }

  if (loading) {
    return (
      <section className="org-google-card card">
        <p className="muted small">Cargando conexión Google…</p>
      </section>
    );
  }
  if (!status) return null;

  const fmt = (iso: string | null | undefined) =>
    iso
      ? new Date(iso).toLocaleString("es-ES", {
          day: "2-digit",
          month: "short",
          year: "numeric",
          hour: "2-digit",
          minute: "2-digit",
        })
      : "—";

  const integStatus = status.status ?? (status.connected ? "active" : null);
  const isActive = status.connected && integStatus === "active";
  const needsReconnect = integStatus === "needs_reconnect";

  return (
    <section className="org-google-card card">
      <header className="section-title">
        <h2>
          <Plug size={16} aria-hidden /> Google (cuenta de la organización)
        </h2>
      </header>

      <p className="muted small">
        Una sola cuenta de Google compartida por todo el equipo. Aquí la
        conectas/desconectas; cada usuario elige su calendario y sus aliases de
        envío en su propia página de cuenta.
      </p>

      {connectedToast ? (
        <p className="form-success">
          <CheckCircle2 size={11} aria-hidden /> Cuenta Google conectada
          correctamente.
        </p>
      ) : null}
      {localError ? <p className="form-error">{localError}</p> : null}

      {!status.configured ? (
        <p className="muted small">
          Las credenciales OAuth de Google todavía no están configuradas en el
          entorno. Añade <code>GOOGLE_OAUTH_CLIENT_ID</code>,{" "}
          <code>GOOGLE_OAUTH_CLIENT_SECRET</code> y{" "}
          <code>GOOGLE_OAUTH_REDIRECT_URI</code> para poder conectar.
        </p>
      ) : !integStatus ? (
        <>
          <p className="muted small">
            La organización todavía no ha conectado ninguna cuenta de Google.
          </p>
          <div className="actions">
            <button
              type="button"
              className="button small"
              onClick={handleConnect}
              disabled={busy}
            >
              <Plug size={11} aria-hidden /> Conectar Google
            </button>
          </div>
        </>
      ) : (
        <>
          <dl className="org-google-meta">
            <div>
              <dt>Cuenta</dt>
              <dd>
                <strong>{status.google_email ?? "—"}</strong>
              </dd>
            </div>
            <div>
              <dt>Estado</dt>
              <dd>
                {isActive ? (
                  <span className="badge ok">
                    <CheckCircle2 size={11} aria-hidden /> Activa
                  </span>
                ) : needsReconnect ? (
                  <span className="badge bad">
                    <AlertTriangle size={11} aria-hidden /> Necesita reconexión
                  </span>
                ) : (
                  <span className="badge muted">Desconectada</span>
                )}
              </dd>
            </div>
            <div>
              <dt>Conectada</dt>
              <dd>{fmt(status.connected_at)}</dd>
            </div>
            <div>
              <dt>Último sync</dt>
              <dd>{fmt(status.last_sync_at)}</dd>
            </div>
            {/* PR-Hotfix-OAuth-Banner Bug 14. La fecha que importa al admin
                es la del REFRESH token (reconexión), no la del access token
                (1h, se refresca solo). */}
            <div className="org-google-reconnect">
              <dt>Reconexión requerida antes de</dt>
              <dd>
                <strong>
                  {status.refresh_token_expires_at
                    ? fmt(status.refresh_token_expires_at)
                    : "Sin caducidad (app verificada)"}
                </strong>
              </dd>
            </div>
          </dl>
          {/* Informativo, no actionable: el access token se renueva solo. */}
          <p className="muted small">
            Sesión Google actual: caduca {fmt(status.token_expires_at)} (se
            renueva automáticamente, no requiere acción).
          </p>

          {needsReconnect ? (
            <p className="form-error">
              <AlertTriangle size={11} aria-hidden /> La conexión está caída. El
              sync de emails y calendario de todo el equipo está detenido.
              Reconecta para reanudarlo.
            </p>
          ) : isActive && status.refresh_token_expiring_soon ? (
            <p className="form-warning">
              <AlertTriangle size={11} aria-hidden /> La reconexión de Google
              vence pronto. Reconecta para no perder el sync del equipo.
            </p>
          ) : null}

          <div className="actions">
            <button
              type="button"
              className="button small"
              onClick={handleConnect}
              disabled={busy}
            >
              <RefreshCw
                size={11}
                aria-hidden
                className={busy ? "spin" : undefined}
              />{" "}
              {needsReconnect ? "Reconectar Google" : "Reconectar / cambiar cuenta"}
            </button>
            <button
              type="button"
              className="button small danger"
              onClick={handleDisconnect}
              disabled={busy}
            >
              Desconectar
            </button>
          </div>
        </>
      )}
    </section>
  );
}
