"use client";

import { AlertTriangle, CheckCircle2, Mail } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { getCurrentUser } from "../lib/api";
import {
  getGoogleScopesStatus,
  getGoogleStatus,
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
 * PR-OAuth-Google-Unificado. La CONEXIÓN es org-wide y la gestiona el
 * admin en /admin/integrations. Aquí cada usuario gestiona solo lo
 * suyo:
 *   - admin no configuró GOOGLE_OAUTH_* → hint.
 *   - org no conectada → aviso (admin: enlace a /admin/integrations).
 *   - org conectada → elige calendario (per-user) + configura aliases
 *     Send-As (per-user). El conectar/desconectar ya NO vive aquí.
 */
export function GoogleCalendarSection() {
  const searchParams = useSearchParams();
  const [status, setStatus] = useState<GoogleStatus | null>(null);
  const [scopes, setScopes] = useState<GoogleScopesStatus | null>(null);
  const [isAdmin, setIsAdmin] = useState(false);
  const [aliases, setAliases] = useState<EmailAlias[] | null>(null);
  const [aliasesLoading, setAliasesLoading] = useState(false);
  const [loading, setLoading] = useState(true);
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
    getCurrentUser()
      .then((u) => setIsAdmin(u.role === "admin"))
      .catch(() => setIsAdmin(false));
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

  // PR-OAuth-Google-Unificado. La conexión es org-wide; si no está
  // activa, el usuario no puede conectarla — es tarea del admin.
  if (!status.connected) {
    return (
      <>
        <p className="muted small">
          La cuenta de Google de la organización no está conectada, así que la
          sincronización de tareas y emails está detenida.
        </p>
        {isAdmin ? (
          <Link className="button small" href="/admin/integrations">
            Gestionar conexión Google (admin)
          </Link>
        ) : (
          <p className="muted small">
            Avisa a un administrador para que conecte (o reconecte) la cuenta
            Google del equipo.
          </p>
        )}
      </>
    );
  }

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
        Cuenta del equipo: <strong>{status.google_email}</strong>
      </p>
      {status.requires_calendar_selection ? (
        <>
          <p className="form-warning">
            <AlertTriangle size={11} aria-hidden /> Falta elegir el calendario
            donde sincronizar tus tareas.
          </p>
          <div className="actions">
            <Link className="button small" href="/account/google-setup">
              Elegir calendario
            </Link>
          </div>
        </>
      ) : (
        <p className="muted small">
          <CheckCircle2 size={11} aria-hidden /> Tu calendario:{" "}
          <strong>{status.selected_calendar?.summary ?? "—"}</strong>{" "}
          <Link className="small muted" href="/account/google-setup">
            Cambiar
          </Link>
        </p>
      )}

      <h4 className="google-subheading">
        <Mail size={11} aria-hidden /> Gmail (alias de envío)
      </h4>
      {gmailReady ? (
        <>
          {needsSettingsScope ? (
            <p className="form-warning small">
              Para ver tus aliases hace falta un permiso adicional de Gmail que
              solo puede conceder el administrador al reconectar la cuenta del
              equipo.
            </p>
          ) : null}
          {aliasesLoading || aliases === null ? (
            <p className="muted small">Cargando aliases…</p>
          ) : aliases.length === 0 ? (
            <p className="muted small">
              Sin aliases &quot;Send mail as&quot; configurados en la cuenta del
              equipo.
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
        <p className="muted small">
          El envío de emails desde el CRM aún no está autorizado para la cuenta
          del equipo.{" "}
          {isAdmin ? (
            <Link href="/admin/integrations">
              Autorízalo desde la configuración de integraciones.
            </Link>
          ) : (
            "Avisa a un administrador para que lo autorice."
          )}
        </p>
      )}
    </>
  );
}
