"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { getCurrentUser, type User } from "../../lib/api";
import {
  deleteIntegrationApiKey,
  getIntegrationSettings,
  setIntegrationApiKey,
  updateIntegrationSetting,
  type IntegrationMode,
  type IntegrationSetting,
  type IntegrationStatus,
} from "../../lib/integrationSettings";
import { extractErrorMessage } from "../../lib/errors";

const modes: IntegrationMode[] = ["sandbox", "live"];
const statuses: IntegrationStatus[] = ["not_configured", "configured", "paused"];

function formatDate(value?: string | null): string {
  if (!value) return "";
  try {
    return new Date(value).toLocaleString();
  } catch {
    return value;
  }
}

export default function IntegrationSettingsPage() {
  const [user, setUser] = useState<User | null>(null);
  const [settings, setSettings] = useState<IntegrationSetting[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [apiKeyDrafts, setApiKeyDrafts] = useState<Record<string, string>>({});

  async function loadSettings() {
    setSettings(await getIntegrationSettings());
  }

  useEffect(() => {
    Promise.all([getCurrentUser(), loadSettings()])
      .then(([currentUser]) => setUser(currentUser))
      .catch((err) =>
        setError(extractErrorMessage(err, "No se pudieron cargar los ajustes")),
      )
      .finally(() => setIsLoading(false));
  }, []);

  const isAdmin = user?.role === "admin";

  async function onSave(setting: IntegrationSetting, form: HTMLFormElement) {
    setError(null);
    setMessage(null);
    const data = new FormData(form);
    try {
      await updateIntegrationSetting(setting.system, {
        display_name: data.get("display_name"),
        enabled: data.get("enabled") === "true",
        mode: data.get("mode"),
        status: data.get("status"),
        api_base_url: data.get("api_base_url") || null,
        account_label: data.get("account_label") || null,
        credential_status: data.get("credential_status") || "not_configured",
        notes: data.get("notes") || null,
      });
      setMessage(`Ajustes guardados para ${setting.display_name}`);
      await loadSettings();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudieron guardar los ajustes"));
    }
  }

  async function onSaveApiKey(setting: IntegrationSetting) {
    setError(null);
    setMessage(null);
    const draft = (apiKeyDrafts[setting.system] ?? "").trim();
    if (!draft) {
      setError("Introduce una API key antes de guardar.");
      return;
    }
    try {
      await setIntegrationApiKey(setting.system, draft);
      setApiKeyDrafts((prev) => ({ ...prev, [setting.system]: "" }));
      setMessage(`API key guardada para ${setting.display_name}`);
      await loadSettings();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo guardar la API key"));
    }
  }

  async function onDeleteApiKey(setting: IntegrationSetting) {
    setError(null);
    setMessage(null);
    const confirmed = window.confirm(
      `¿Borrar la API key de ${setting.display_name}? La integración quedará desactivada hasta que se reintroduzca.`,
    );
    if (!confirmed) return;
    try {
      await deleteIntegrationApiKey(setting.system);
      setApiKeyDrafts((prev) => ({ ...prev, [setting.system]: "" }));
      setMessage(`API key borrada para ${setting.display_name}`);
      await loadSettings();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo borrar la API key"));
    }
  }

  return (
    <main className="shell">
      <Link href="/" className="back-link">← Volver al dashboard</Link>
      <section className="hero compact">
        <p className="eyebrow">Administración</p>
        <h1>Ajustes de integraciones</h1>
        <p className="lead">
          Configura AgileCRM, Brevo, Freshdesk y FactuSOL. Las API keys se cifran en reposo y solo
          se almacenan para uso interno de los conectores; la app nunca las devuelve en claro.
        </p>
      </section>

      {isLoading ? <p className="muted">Cargando ajustes...</p> : null}
      {error ? <div className="error-state">{error}</div> : null}
      {message ? <div className="success-state">{message}</div> : null}

      <section className="grid two">
        {settings.map((setting) => (
          <article className="card" key={setting.system}>
            <h2>{setting.display_name}</h2>
            <p className="muted">Sistema: {setting.system}</p>
            <form
              className="form-card embedded"
              onSubmit={(event) => {
                event.preventDefault();
                onSave(setting, event.currentTarget);
              }}
            >
              <label>Nombre visible<input name="display_name" defaultValue={setting.display_name} /></label>
              <label>Estado operativo<select name="enabled" defaultValue={String(setting.enabled)}><option value="false">Desactivada</option><option value="true">Activada internamente</option></select></label>
              <label>Modo<select name="mode" defaultValue={setting.mode}>{modes.map((mode) => <option key={mode} value={mode}>{mode}</option>)}</select></label>
              <label>Configuración<select name="status" defaultValue={setting.status}>{statuses.map((status) => <option key={status} value={status}>{status}</option>)}</select></label>
              <label>URL base API futura<input name="api_base_url" defaultValue={setting.api_base_url ?? ""} placeholder="https://api.example.com" /></label>
              <label>Cuenta o entorno<input name="account_label" defaultValue={setting.account_label ?? ""} placeholder="Sandbox / producción" /></label>
              <label>Estado de credenciales<input name="credential_status" defaultValue={setting.credential_status} placeholder="not_configured" /></label>
              <label>Notas internas<input name="notes" defaultValue={setting.notes ?? ""} /></label>
              <button className="button" type="submit">Guardar ajustes</button>
            </form>

            {isAdmin ? (
              <div className="form-card embedded api-key-block">
                <h3>API key</h3>
                <p className="muted">
                  {setting.has_api_key
                    ? `Configurada el ${formatDate(setting.api_key_set_at)}${
                        setting.api_key_last_used_at
                          ? ` · último uso: ${formatDate(setting.api_key_last_used_at)}`
                          : ""
                      }`
                    : "Sin configurar"}
                </p>
                <label>
                  {setting.has_api_key ? "Reemplazar API key" : "Nueva API key"}
                  <input
                    type="password"
                    autoComplete="off"
                    name={`api_key_${setting.system}`}
                    value={apiKeyDrafts[setting.system] ?? ""}
                    placeholder="Pega aquí la API key del proveedor"
                    onChange={(event) =>
                      setApiKeyDrafts((prev) => ({
                        ...prev,
                        [setting.system]: event.target.value,
                      }))
                    }
                  />
                </label>
                <div className="actions">
                  <button
                    className="button"
                    type="button"
                    onClick={() => onSaveApiKey(setting)}
                  >
                    Guardar API key
                  </button>
                  {setting.has_api_key ? (
                    <button
                      className="button secondary"
                      type="button"
                      onClick={() => onDeleteApiKey(setting)}
                    >
                      Borrar API key
                    </button>
                  ) : null}
                </div>
              </div>
            ) : null}
          </article>
        ))}
      </section>
    </main>
  );
}
