"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  getIntegrationSettings,
  updateIntegrationSetting,
  type IntegrationMode,
  type IntegrationSetting,
  type IntegrationStatus,
} from "../../lib/integrationSettings";

const modes: IntegrationMode[] = ["sandbox", "live"];
const statuses: IntegrationStatus[] = ["not_configured", "configured", "paused"];

export default function IntegrationSettingsPage() {
  const [settings, setSettings] = useState<IntegrationSetting[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  async function loadSettings() {
    setSettings(await getIntegrationSettings());
  }

  useEffect(() => {
    loadSettings()
      .catch((err) => setError(err instanceof Error ? err.message : "No se pudieron cargar los ajustes"))
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
          Configura AgileCRM, Brevo, Freshdesk y FactuSOL sin llamar todavía a APIs externas ni
          guardar secretos en el repositorio.
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
            <form className="form-card embedded" onSubmit={(event) => {
              event.preventDefault();
              onSave(setting, event.currentTarget);
            }}>
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
          </article>
        ))}
      </section>
    </main>
  );
}
