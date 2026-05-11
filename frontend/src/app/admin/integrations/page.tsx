"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { ErrorState } from "../../components/ErrorState";
import { IntegrationAccountModal } from "../../components/IntegrationAccountModal";
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
  type IntegrationAccountUpdatePayload,
} from "../../lib/integrationSettings";

const SYSTEMS: ExternalSystem[] = ["agilecrm", "brevo", "freshdesk", "factusol"];
const SYSTEM_LABEL: Record<ExternalSystem, string> = {
  agilecrm: "AgileCRM",
  brevo: "Brevo",
  freshdesk: "Freshdesk",
  factusol: "FactuSOL",
};

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

type ModalState =
  | { kind: "closed" }
  | { kind: "create"; system: ExternalSystem }
  | { kind: "edit"; account: IntegrationAccount }
  | { kind: "delete"; account: IntegrationAccount; force?: boolean; detail?: string };

export default function IntegrationAccountsPage() {
  const [user, setUser] = useState<User | null>(null);
  const [accounts, setAccounts] = useState<IntegrationAccount[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [apiKeyDrafts, setApiKeyDrafts] = useState<Record<string, string>>({});
  const [modal, setModal] = useState<ModalState>({ kind: "closed" });

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

  function closeModal() {
    setModal({ kind: "closed" });
  }

  async function onCreateSubmit(
    system: ExternalSystem,
    payload: IntegrationAccountCreatePayload,
  ) {
    setError(null);
    setMessage(null);
    await createIntegrationAccount(system, payload);
    closeModal();
    setMessage(
      `Cuenta '${payload.account_id}' creada en ${SYSTEM_LABEL[system]}`,
    );
    await loadAccounts();
  }

  async function onEditSubmit(
    account: IntegrationAccount,
    payload: IntegrationAccountUpdatePayload,
  ) {
    setError(null);
    setMessage(null);
    await updateIntegrationAccount(account.system, account.account_id, payload);
    closeModal();
    setMessage(`Cuenta '${account.account_id}' actualizada`);
    await loadAccounts();
  }

  async function onConfirmDelete(account: IntegrationAccount, force: boolean) {
    setError(null);
    setMessage(null);
    try {
      await deleteIntegrationAccount(account.system, account.account_id, { force });
      closeModal();
      setMessage(force ? "Cuenta eliminada (forzada)" : "Cuenta eliminada");
      await loadAccounts();
    } catch (err) {
      const detail = extractErrorMessage(err, "No se pudo borrar la cuenta");
      // Backend signals 409 with a "?force=true" hint when external references exist.
      if (!force && detail.includes("?force=true")) {
        setModal({ kind: "delete", account, force: true, detail });
        return;
      }
      setError(detail);
      closeModal();
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
                  onClick={() => setModal({ kind: "create", system })}
                >
                  + Añadir cuenta
                </button>
              ) : null}
            </div>

            {list.length === 0 ? (
              <p className="muted">Sin cuentas configuradas.</p>
            ) : null}

            <div className="item-list">
              {list.map((account) => {
                const compound = compoundKey(account);
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
                              onClick={() => setModal({ kind: "edit", account })}
                            >
                              Editar
                            </button>
                            <button
                              className="button secondary small"
                              type="button"
                              onClick={() => setModal({ kind: "delete", account })}
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

      {modal.kind === "create" ? (
        <IntegrationAccountModal
          mode="create"
          open
          system={modal.system}
          onClose={closeModal}
          onSubmit={(payload) => onCreateSubmit(modal.system, payload)}
        />
      ) : null}

      {modal.kind === "edit" ? (
        <IntegrationAccountModal
          mode="edit"
          open
          account={modal.account}
          onClose={closeModal}
          onSubmit={(payload) => onEditSubmit(modal.account, payload)}
        />
      ) : null}

      {modal.kind === "delete" ? (
        <ConfirmDialog
          open
          title={
            modal.force
              ? "Forzar borrado de cuenta"
              : "Eliminar cuenta de integración"
          }
          message={
            modal.force && modal.detail
              ? `${modal.detail}\n\n¿Forzar el borrado de todos modos?`
              : `¿Eliminar la cuenta ${modal.account.display_name} (${modal.account.account_id})? Esta acción no se puede deshacer.`
          }
          confirmLabel={modal.force ? "Forzar eliminación" : "Eliminar"}
          onConfirm={() => onConfirmDelete(modal.account, modal.force === true)}
          onCancel={closeModal}
        />
      ) : null}
    </main>
  );
}
