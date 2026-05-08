"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ErrorState } from "../../components/ErrorState";
import {
  getCurrentUser,
  getIntegrationSettings,
  updateIntegrationSetting,
  type IntegrationMode,
  type IntegrationSetting,
  type IntegrationStatus,
  type User,
} from "../../lib/api";

const modes: IntegrationMode[] = ["sandbox", "live"];
const statuses: IntegrationStatus[] = ["not_configured", "configured", "paused"];

export default function IntegrationSettingsPage() {
  const [currentUser, setCurrentUser] = useState<User | null>(null);
  const [settings, setSettings] = useState<IntegrationSetting[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const canEdit = currentUser?.role === "admin";

  async function loadSettings() {
    const [user, integrationSettings] = await Promise.all([
      getCurrentUser(),
      getIntegrationSettings(),
    ]);
    if (!["admin", "manager"].includes(user.role)) {
      throw new Error("No tienes permisos para ver los ajustes de integraciones");
    }
    setCurrentUser(user);
    setSettings(integrationSettings);
  }

  useEffect(() => {
    loadSettings()
      .catch((err) =>
        setError(err instanceof Error ? err.message : "No se pudieron cargar los ajustes"),
      )
      .finally(() => setIsLoading(false));
  }, []);

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
      setError(err instanceof Error ? err.message : "No se pudieron guardar los ajustes");
    }
  }

  return (
    <main className="shell">
      <Link href="/" className="back-link">← Volver al dashboard</Link>
      <section className="hero compact">
        <p className="eyebrow">Administración</p>
        <h1>Ajustes de integraciones</h1>
        <p className="lead">
          Configura el estado interno de AgileCRM, Brevo, Freshdesk y FactuSOL sin llamar todavía
          a APIs externas ni guardar secretos en el repositorio.
        </p>
      </section>

      {isLoading ? <p className="muted">Cargando ajustes...</p> : null}
      {error ? <ErrorState title="Error de permisos o carga" message={error} /> : null}
      {message ? <div className="success-state">{message}</div> : null}

      {!error ? (
        <section className="grid two">
          {settings.map((setting) => (
            <article className="card" key={setting.system}>
              <div className="section-title compact-title">
                <div>
                  <p className="eyebrow">{setting.system}</p>
                  <h2>{setting.display_name}</h2>
                </div>
                <span className={`status status-${setting.status}`}>{setting.status}</span>
              </div>
              <form
                className="form-card embedded integration-form"
                onSubmit={(event) => {
                  event.preventDefault();
                  onSave(setting, event.currentTarget);
                }}
              >
                <label>
                  Nombre visible
                  <input name="display_name" defaultValue={setting.display_name} disabled={!canEdit} />
                </label>
                <label>
                  Estado operativo
                  <select name="enabled" defaultValue={String(setting.enabled)} disabled={!canEdit}>
                    <option value="false">Desactivada</option>
                    <option value="true">Activada internamente</option>
                  </select>
                </label>
                <label>
                  Modo
                  <select name="mode" defaultValue={setting.mode} disabled={!canEdit}>
                    {modes.map((mode) => <option key={mode} value={mode}>{mode}</option>)}
                  </select>
                </label>
                <label>
                  Configuración
                  <select name="status" defaultValue={setting.status} disabled={!canEdit}>
                    {statuses.map((status) => <option key={status} value={status}>{status}</option>)}
                  </select>
                </label>
                <label>
                  URL base API futura
                  <input
                    name="api_base_url"
                    defaultValue={setting.api_base_url ?? ""}
                    placeholder="https://api.example.com"
                    disabled={!canEdit}
                  />
                </label>
                <label>
                  Cuenta o entorno
                  <input
                    name="account_label"
                    defaultValue={setting.account_label ?? ""}
                    placeholder="Producción, sandbox, cliente..."
                    disabled={!canEdit}
                  />
                </label>
                <label>
                  Estado de credenciales
                  <input
                    name="credential_status"
                    defaultValue={setting.credential_status}
                    placeholder="not_configured / configured_externally"
                    disabled={!canEdit}
                  />
                </label>
                <label>
                  Notas internas
                  <input name="notes" defaultValue={setting.notes ?? ""} disabled={!canEdit} />
                </label>
                {canEdit ? <button className="button" type="submit">Guardar ajustes</button> : null}
                {!canEdit ? (
                  <p className="muted">Solo los administradores pueden editar estos ajustes.</p>
                ) : null}
              </form>
            </article>
          ))}
        </section>
      ) : null}
    </main>
  );
}
