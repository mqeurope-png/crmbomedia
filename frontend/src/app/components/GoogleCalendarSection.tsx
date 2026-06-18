"use client";

import { AlertTriangle, CheckCircle2, Mail, Plug } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import {
  disconnectGoogle,
  getGoogleScopesStatus,
  getGoogleStatus,
  startGoogleConnect,
  type GoogleScopesStatus,
  type GoogleStatus,
} from "../lib/googleApi";
import {
  getEmailAliases,
  putEmailAliasPreferences,
  type EmailAlias,
} from "../lib/emailsApi";
import { extractErrorMessage } from "../lib/errors";
import { GmailAliasMultiSelect } from "./GmailAliasMultiSelect";

/** Google Calendar block inside /account.
 *
 * Three visible states:
 *   - admin hasn't configured GOOGLE_OAUTH_* → just shows a hint.
 *   - user not connected → CTA to start the OAuth flow.
 *   - user connected → either prompts for calendar selection or
 *     shows the chosen calendar + change/disconnect controls.
 */
export function GoogleCalendarSection() {
  const searchParams = useSearchParams();
  const [status, setStatus] = useState<GoogleStatus | null>(null);
  const [scopes, setScopes] = useState<GoogleScopesStatus | null>(null);
  const [aliases, setAliases] = useState<EmailAlias[] | null>(null);
  const [aliasesLoading, setAliasesLoading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showGmailToast, setShowGmailToast] = useState(false);

  // "?gmail_connected=1" lands on the user after the OAuth callback
  // when they already had a calendar. Show a one-shot success toast
  // so they know the reauth went through.
  useEffect(() => {
    if (searchParams?.get("gmail_connected") === "1") {
      setShowGmailToast(true);
    }
  }, [searchParams]);

  const reload = useCallback(async () => {
    try {
      const [data, scopesData] = await Promise.all([
        getGoogleStatus(),
        getGoogleScopesStatus().catch(() => null),
      ]);
      setStatus(data);
      setScopes(scopesData);
      setError(null);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo cargar el estado."));
    }
  }, []);

  const reloadAliases = useCallback(async () => {
    setAliasesLoading(true);
    try {
      setAliases(await getEmailAliases());
    } catch {
      setAliases([]);
    } finally {
      setAliasesLoading(false);
    }
  }, []);

  useEffect(() => {
    reload().finally(() => setLoading(false));
  }, [reload]);

  // Once we know Gmail is authorised, fetch the aliases. The
  // backend hits Google's settings.sendAs.list endpoint on demand —
  // no DB cache, the spec is explicit about that.
  useEffect(() => {
    if (scopes?.gmail_send && scopes?.gmail_settings && aliases === null) {
      reloadAliases();
    }
  }, [scopes, aliases, reloadAliases]);

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

  const needsGmailReauth =
    scopes !== null && (!scopes.gmail_send || !scopes.gmail_modify);

  const gmailReady = !!scopes?.gmail_send && !!scopes?.gmail_modify;
  const needsSettingsScope = !!scopes?.gmail_send && !scopes?.gmail_settings;

  return (
    <>
      {showGmailToast ? (
        <p className="form-success">
          <CheckCircle2 size={11} aria-hidden /> Gmail autorizado correctamente.
          Los aliases aparecen abajo.
        </p>
      ) : null}
      <p className="muted small">
        Cuenta: <strong>{status.google_email}</strong>
      </p>
      {needsGmailReauth ? (
        <div className="form-warning">
          <span>
            Necesitamos permisos adicionales para enviar emails desde el CRM.
          </span>
          <button
            type="button"
            className="button small"
            onClick={handleConnect}
            disabled={busy}
          >
            Autorizar Gmail
          </button>
        </div>
      ) : null}
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
            <strong>{status.selected_calendar?.summary ?? "—"}</strong>{" "}
            <Link className="small muted" href="/account/google-setup">
              Cambiar
            </Link>
          </p>
        </>
      )}

      <h4 className="google-subheading">
        <Mail size={11} aria-hidden /> Gmail (envío de emails)
      </h4>
      {gmailReady ? (
        <>
          {needsSettingsScope ? (
            <p className="form-warning small">
              Para ver tus aliases necesitamos un permiso adicional.{" "}
              <button
                type="button"
                className="link-button"
                onClick={handleConnect}
                disabled={busy}
              >
                Reautorizar
              </button>
              .
            </p>
          ) : null}
          {aliasesLoading || aliases === null ? (
            <p className="muted small">Cargando aliases…</p>
          ) : aliases.length === 0 ? (
            <p className="muted small">
              Sin aliases &quot;Send mail as&quot; configurados en tu Gmail.
            </p>
          ) : (
            <GmailAliasMultiSelect
              aliases={aliases}
              onSave={async (prefs) => {
                const next = await putEmailAliasPreferences(prefs);
                setAliases(next);
                return next;
              }}
              onRefresh={reloadAliases}
              refreshing={aliasesLoading}
            />
          )}
        </>
      ) : (
        <button
          type="button"
          className="button small"
          onClick={handleConnect}
          disabled={busy}
        >
          <Plug size={11} aria-hidden /> Autorizar envío de emails
        </button>
      )}

      <div className="actions">
        <button
          className="button small danger"
          type="button"
          onClick={handleDisconnect}
          disabled={busy}
        >
          Desconectar Google
        </button>
      </div>
    </>
  );
}

