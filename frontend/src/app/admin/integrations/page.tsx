"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ErrorState } from "../../components/ErrorState";
import { getCurrentUser, type User } from "../../lib/api";
import { extractErrorMessage } from "../../lib/errors";
import {
  createIntegrationAccount,
  deleteIntegrationAccount,
  deleteIntegrationAccountApiKey,
  listIntegrationAccounts,
  setIntegrationAccountApiKey,
  updateIntegrationAccount,
  type ExternalSystem,
  type IntegrationAccount,
  type IntegrationAccountCreatePayload,
  type IntegrationMode,
  type IntegrationStatus,
  type QuotaStrategy,
} from "../../lib/integrationSettings";

const SYSTEMS: ExternalSystem[] = ["agilecrm", "brevo", "freshdesk", "factusol"];
const SYSTEM_LABEL: Record<ExternalSystem, string> = {
  agilecrm: "AgileCRM",
  brevo: "Brevo",
  freshdesk: "Freshdesk",
  factusol: "FactuSOL",
};

const MODES: IntegrationMode[] = ["sandbox", "live"];
const STATUSES: IntegrationStatus[] = ["not_configured", "configured", "paused"];
const QUOTA_STRATEGIES: QuotaStrategy[] = ["keep_newest", "keep_oldest", "none"];

const ACCOUNT_ID_PATTERN = /^[a-z0-9](?:[a-z0-9_-]*[a-z0-9])?$/;

function supportsQuota(system: ExternalSystem): boolean {
  return system === "agilecrm";
}

function formatDate(value?: string | null): string {
  if (!value) return "";
  try {
    return new Date(value).toLocaleString();
  } catch {
    return value;
  }
}

type CreateDraft = {
  account_id: string;
  display_name: string;
  mode: IntegrationMode;
  api_base_url: string;
  account_label: string;
  quota_max_contacts: string;
  quota_strategy: QuotaStrategy;
  sync_priority: string;
  notes: string;
};

const EMPTY_DRAFT: CreateDraft = {
  account_id: "",
  display_name: "",
  mode: "sandbox",
  api_base_url: "",
  account_label: "",
  quota_max_contacts: "",
  quota_strategy: "none",
  sync_priority: "100",
  notes: "",
};

export default function IntegrationAccountsPage() {
  const [user, setUser] = useState<User | null>(null);
  const [accounts, setAccounts] = useState<IntegrationAccount[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [apiKeyDrafts, setApiKeyDrafts] = useState<Record<string, string>>({});
  const [openCreate, setOpenCreate] = useState<ExternalSystem | null>(null);
  const [createDrafts, setCreateDrafts] = useState<Record<ExternalSystem, CreateDraft>>({
    agilecrm: { ...EMPTY_DRAFT },
    brevo: { ...EMPTY_DRAFT },
    freshdesk: { ...EMPTY_DRAFT },
    factusol: { ...EMPTY_DRAFT },
  });
  const [openEdit, setOpenEdit] = useState<string | null>(null);

  const isAdmin = user?.role === "admin";

  async function loadAccounts() {
    setAccounts(await listIntegrationAccounts());
  }

  useEffect(() => {
    Promise.all([getCurrentUser(), loadAccounts()])
      .then(([currentUser]) => setUser(currentUser))
      .catch((err) =>
        setError(extractErrorMessage(err, "No se pudieron cargar las cuentas")),
      )
      .finally(() => setIsLoading(false));
  }, []);

  function compoundKey(account: IntegrationAccount): string {
    return `${account.system}:${account.account_id}`;
  }

  async function onCreate(system: ExternalSystem) {
    setError(null);
    setMessage(null);
    const draft = createDrafts[system];
    if (!ACCOUNT_ID_PATTERN.test(draft.account_id)) {
      setError(
        "account_id inválido. Usa solo minúsculas, números, '_' y '-' (ej. 'agilecrm-es').",
      );
      return;
    }
    const payload: IntegrationAccountCreatePayload = {
      account_id: draft.account_id,
      display_name: draft.display_name,
      mode: draft.mode,
      api_base_url: draft.api_base_url || null,
      account_label: draft.account_label || null,
      notes: draft.notes || null,
      sync_priority: Number(draft.sync_priority) || 100,
    };
    if (supportsQuota(system) && draft.quota_max_contacts) {
      payload.quota_max_contacts = Number(draft.quota_max_contacts);
      payload.quota_strategy = draft.quota_strategy;
    }
    try {
      await createIntegrationAccount(system, payload);
      setCreateDrafts((prev) => ({ ...prev, [system]: { ...EMPTY_DRAFT } }));
      setOpenCreate(null);
      setMessage(`Cuenta '${draft.account_id}' creada en ${SYSTEM_LABEL[system]}`);
      await loadAccounts();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo crear la cuenta"));
    }
  }

  async function onSave(account: IntegrationAccount, form: HTMLFormElement) {
    setError(null);
    setMessage(null);
    const data = new FormData(form);
    try {
      const quotaRaw = String(data.get("quota_max_contacts") ?? "").trim();
      const payload = {
        display_name: String(data.get("display_name") ?? account.display_name),
        enabled: data.get("enabled") === "true",
        mode: String(data.get("mode") ?? account.mode) as IntegrationMode,
        status: String(data.get("status") ?? account.status) as IntegrationStatus,
        api_base_url: String(data.get("api_base_url") ?? "") || null,
        account_label: String(data.get("account_label") ?? "") || null,
        credential_status: String(
          data.get("credential_status") ?? account.credential_status,
        ),
        notes: String(data.get("notes") ?? "") || null,
        sync_priority:
          Number(data.get("sync_priority") ?? account.sync_priority) || 100,
        quota_max_contacts: quotaRaw ? Number(quotaRaw) : null,
        quota_strategy:
          (String(data.get("quota_strategy") ?? "none") as QuotaStrategy) || null,
      };
      await updateIntegrationAccount(account.system, account.account_id, payload);
      setMessage(`Cuenta '${account.account_id}' actualizada`);
      setOpenEdit(null);
      await loadAccounts();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo guardar la cuenta"));
    }
  }

  async function onSaveApiKey(account: IntegrationAccount) {
    setError(null);
    setMessage(null);
    const key = compoundKey(account);
    const draft = (apiKeyDrafts[key] ?? "").trim();
    if (!draft) {
      setError("Introduce una API key antes de guardar.");
      return;
    }
    try {
      await setIntegrationAccountApiKey(account.system, account.account_id, draft);
      setApiKeyDrafts((prev) => ({ ...prev, [key]: "" }));
      setMessage(`API key guardada para ${account.display_name}`);
      await loadAccounts();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo guardar la API key"));
    }
  }

  async function onDeleteApiKey(account: IntegrationAccount) {
    setError(null);
    setMessage(null);
    const confirmed = window.confirm(
      `¿Borrar la API key de ${account.display_name} (${account.account_id})? La integración quedará desactivada hasta que se reintroduzca.`,
    );
    if (!confirmed) return;
    try {
      await deleteIntegrationAccountApiKey(account.system, account.account_id);
      const key = compoundKey(account);
      setApiKeyDrafts((prev) => ({ ...prev, [key]: "" }));
      setMessage(`API key borrada para ${account.display_name}`);
      await loadAccounts();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo borrar la API key"));
    }
  }

  async function onDeleteAccount(account: IntegrationAccount) {
    setError(null);
    setMessage(null);
    const confirmed = window.confirm(
      `¿Borrar la cuenta '${account.account_id}' de ${SYSTEM_LABEL[account.system]}? Esta acción es irreversible.`,
    );
    if (!confirmed) return;
    try {
      await deleteIntegrationAccount(account.system, account.account_id);
      setMessage("Cuenta eliminada");
      await loadAccounts();
    } catch (err) {
      // If the backend reports references, offer to force.
      const detail = extractErrorMessage(err, "");
      if (detail.includes("?force=true")) {
        const force = window.confirm(
          `${detail}\n\n¿Forzar el borrado de todos modos?`,
        );
        if (!force) return;
        try {
          await deleteIntegrationAccount(account.system, account.account_id, {
            force: true,
          });
          setMessage("Cuenta eliminada (forzada)");
          await loadAccounts();
        } catch (err2) {
          setError(extractErrorMessage(err2, "No se pudo borrar la cuenta"));
        }
        return;
      }
      setError(extractErrorMessage(err, "No se pudo borrar la cuenta"));
    }
  }

  function setDraftField<K extends keyof CreateDraft>(
    system: ExternalSystem,
    field: K,
    value: CreateDraft[K],
  ) {
    setCreateDrafts((prev) => ({
      ...prev,
      [system]: { ...prev[system], [field]: value },
    }));
  }

  return (
    <main className="shell">
      <Link href="/" className="back-link">← Volver al dashboard</Link>
      <section className="hero compact">
        <p className="eyebrow">Administración</p>
        <h1>Cuentas de integración</h1>
        <p className="lead">
          Múltiples cuentas por sistema. AgileCRM y Freshdesk admiten una cuenta
          por mercado o equipo; Brevo y FactuSOL típicamente quedan en una sola
          cuenta. Las API keys se cifran en reposo.
        </p>
      </section>

      {isLoading ? <p className="muted">Cargando cuentas...</p> : null}
      {error ? <ErrorState title="Error" message={error} /> : null}
      {message ? <div className="success-state">{message}</div> : null}

      {SYSTEMS.map((system) => {
        const list = accounts
          .filter((a) => a.system === system)
          .sort((a, b) => a.sync_priority - b.sync_priority);
        const draft = createDrafts[system];
        const isCreateOpen = openCreate === system;
        return (
          <section className="card" key={system}>
            <div className="section-title">
              <h2>
                {SYSTEM_LABEL[system]}{" "}
                <span className="muted">
                  ({list.length} {list.length === 1 ? "cuenta" : "cuentas"})
                </span>
              </h2>
              {isAdmin ? (
                <button
                  className="button secondary small"
                  type="button"
                  onClick={() => setOpenCreate(isCreateOpen ? null : system)}
                >
                  {isCreateOpen ? "Cancelar" : "+ Añadir cuenta"}
                </button>
              ) : null}
            </div>

            {isAdmin && isCreateOpen ? (
              <form
                className="form-card embedded"
                onSubmit={(event) => {
                  event.preventDefault();
                  onCreate(system);
                }}
              >
                <label>
                  account_id (slug)
                  <input
                    required
                    placeholder="agilecrm-es"
                    value={draft.account_id}
                    onChange={(event) =>
                      setDraftField(system, "account_id", event.target.value)
                    }
                  />
                </label>
                <label>
                  Nombre visible
                  <input
                    required
                    placeholder="AgileCRM España"
                    value={draft.display_name}
                    onChange={(event) =>
                      setDraftField(system, "display_name", event.target.value)
                    }
                  />
                </label>
                <label>
                  Modo
                  <select
                    value={draft.mode}
                    onChange={(event) =>
                      setDraftField(system, "mode", event.target.value as IntegrationMode)
                    }
                  >
                    {MODES.map((mode) => (
                      <option key={mode} value={mode}>{mode}</option>
                    ))}
                  </select>
                </label>
                <label>
                  URL base API
                  <input
                    placeholder="https://api.example.com"
                    value={draft.api_base_url}
                    onChange={(event) =>
                      setDraftField(system, "api_base_url", event.target.value)
                    }
                  />
                </label>
                <label>
                  Etiqueta de cuenta
                  <input
                    placeholder="Producción ES"
                    value={draft.account_label}
                    onChange={(event) =>
                      setDraftField(system, "account_label", event.target.value)
                    }
                  />
                </label>
                <label>
                  Prioridad de sincronización
                  <input
                    type="number"
                    min={0}
                    max={10000}
                    value={draft.sync_priority}
                    onChange={(event) =>
                      setDraftField(system, "sync_priority", event.target.value)
                    }
                  />
                </label>
                {supportsQuota(system) ? (
                  <>
                    <label>
                      Cuota máxima de contactos
                      <input
                        type="number"
                        min={1}
                        placeholder="800"
                        value={draft.quota_max_contacts}
                        onChange={(event) =>
                          setDraftField(system, "quota_max_contacts", event.target.value)
                        }
                      />
                    </label>
                    <label>
                      Estrategia de cuota
                      <select
                        value={draft.quota_strategy}
                        onChange={(event) =>
                          setDraftField(
                            system,
                            "quota_strategy",
                            event.target.value as QuotaStrategy,
                          )
                        }
                      >
                        {QUOTA_STRATEGIES.map((q) => (
                          <option key={q} value={q}>{q}</option>
                        ))}
                      </select>
                    </label>
                  </>
                ) : null}
                <label>
                  Notas
                  <input
                    placeholder="Comentarios internos"
                    value={draft.notes}
                    onChange={(event) => setDraftField(system, "notes", event.target.value)}
                  />
                </label>
                <button className="button" type="submit">Crear cuenta</button>
              </form>
            ) : null}

            {list.length === 0 && !isCreateOpen ? (
              <p className="muted">Sin cuentas configuradas.</p>
            ) : null}

            <div className="item-list">
              {list.map((account) => {
                const compound = compoundKey(account);
                const isEditing = openEdit === compound;
                return (
                  <article className="card embedded" key={account.id}>
                    <header className="section-title">
                      <div>
                        <strong>{account.display_name}</strong>{" "}
                        <code>{account.account_id}</code>
                      </div>
                      <div className="actions">
                        {isAdmin ? (
                          <>
                            <button
                              className="button secondary small"
                              type="button"
                              onClick={() => setOpenEdit(isEditing ? null : compound)}
                            >
                              {isEditing ? "Cerrar" : "Editar"}
                            </button>
                            <button
                              className="button secondary small"
                              type="button"
                              onClick={() => onDeleteAccount(account)}
                            >
                              Borrar
                            </button>
                          </>
                        ) : null}
                      </div>
                    </header>
                    <p className="muted">
                      Modo: <code>{account.mode}</code> · Estado:{" "}
                      <code>{account.status}</code> · Credenciales:{" "}
                      <code>{account.credential_status}</code>
                      {supportsQuota(account.system) && account.quota_max_contacts != null ? (
                        <>
                          {" "}· Cuota: {account.quota_max_contacts} ({account.quota_strategy})
                        </>
                      ) : null}
                      {account.account_label ? <> · {account.account_label}</> : null}
                    </p>

                    {isAdmin && isEditing ? (
                      <form
                        className="form-card embedded"
                        onSubmit={(event) => {
                          event.preventDefault();
                          onSave(account, event.currentTarget);
                        }}
                      >
                        <label>Nombre visible<input name="display_name" defaultValue={account.display_name} /></label>
                        <label>
                          Activada
                          <select name="enabled" defaultValue={String(account.enabled)}>
                            <option value="false">No</option>
                            <option value="true">Sí</option>
                          </select>
                        </label>
                        <label>
                          Modo
                          <select name="mode" defaultValue={account.mode}>
                            {MODES.map((mode) => <option key={mode} value={mode}>{mode}</option>)}
                          </select>
                        </label>
                        <label>
                          Configuración
                          <select name="status" defaultValue={account.status}>
                            {STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
                          </select>
                        </label>
                        <label>URL base API<input name="api_base_url" defaultValue={account.api_base_url ?? ""} /></label>
                        <label>Etiqueta<input name="account_label" defaultValue={account.account_label ?? ""} /></label>
                        <label>Estado de credenciales<input name="credential_status" defaultValue={account.credential_status} /></label>
                        <label>Prioridad sync<input type="number" name="sync_priority" defaultValue={account.sync_priority} min={0} max={10000} /></label>
                        {supportsQuota(account.system) ? (
                          <>
                            <label>Cuota máxima contactos<input type="number" name="quota_max_contacts" defaultValue={account.quota_max_contacts ?? ""} min={1} /></label>
                            <label>
                              Estrategia cuota
                              <select name="quota_strategy" defaultValue={account.quota_strategy ?? "none"}>
                                {QUOTA_STRATEGIES.map((q) => <option key={q} value={q}>{q}</option>)}
                              </select>
                            </label>
                          </>
                        ) : null}
                        <label>Notas<input name="notes" defaultValue={account.notes ?? ""} /></label>
                        <button className="button" type="submit">Guardar</button>
                      </form>
                    ) : null}

                    {isAdmin ? (
                      <div className="form-card embedded api-key-block">
                        <h3>API key</h3>
                        <p className="muted">
                          {account.has_api_key
                            ? `Configurada el ${formatDate(account.api_key_set_at)}${
                                account.api_key_last_used_at
                                  ? ` · último uso: ${formatDate(account.api_key_last_used_at)}`
                                  : ""
                              }`
                            : "Sin configurar"}
                        </p>
                        <label>
                          {account.has_api_key ? "Reemplazar API key" : "Nueva API key"}
                          <input
                            type="password"
                            autoComplete="off"
                            value={apiKeyDrafts[compound] ?? ""}
                            placeholder="Pega aquí la API key del proveedor"
                            onChange={(event) =>
                              setApiKeyDrafts((prev) => ({
                                ...prev,
                                [compound]: event.target.value,
                              }))
                            }
                          />
                        </label>
                        <div className="actions">
                          <button
                            className="button"
                            type="button"
                            onClick={() => onSaveApiKey(account)}
                          >
                            Guardar API key
                          </button>
                          {account.has_api_key ? (
                            <button
                              className="button secondary"
                              type="button"
                              onClick={() => onDeleteApiKey(account)}
                            >
                              Borrar API key
                            </button>
                          ) : null}
                        </div>
                      </div>
                    ) : null}
                  </article>
                );
              })}
            </div>
          </section>
        );
      })}
    </main>
  );
}
