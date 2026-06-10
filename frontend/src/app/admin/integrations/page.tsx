"use client";

import { Plus } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { ErrorState } from "../../components/ErrorState";
import { IntegrationAccountCard } from "../../components/IntegrationAccountCard";
import { IntegrationAccountModal } from "../../components/IntegrationAccountModal";
import { IntegrationSystemTabs } from "../../components/IntegrationSystemTabs";
import { PageHeader } from "../../components/PageHeader";
import { getCurrentUser, type User } from "../../lib/api";
import { extractErrorMessage } from "../../lib/errors";
import {
  createIntegrationAccount,
  deleteIntegrationAccount,
  listIntegrationAccounts,
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
  const [activeSystem, setActiveSystem] = useState<ExternalSystem>("agilecrm");
  const [expandedKey, setExpandedKey] = useState<string | null>(null);
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

  const tabs = useMemo(
    () =>
      SYSTEMS.map((system) => ({
        system,
        label: SYSTEM_LABEL[system],
        count: accounts.filter((a) => a.system === system).length,
      })),
    [accounts],
  );

  const activeAccounts = useMemo(
    () =>
      accounts
        .filter((account) => account.system === activeSystem)
        .sort((a, b) => a.sync_priority - b.sync_priority),
    [accounts, activeSystem],
  );

  return (
    <main className="shell">
      <PageHeader
        title="Cuentas de integración"
        eyebrow="Administración"
        description="Múltiples cuentas por sistema. AgileCRM y Freshdesk admiten una cuenta por mercado o equipo; Brevo y FactuSOL típicamente quedan en una sola cuenta. Las API keys se cifran en reposo."
        actions={
          isAdmin ? (
            <button
              type="button"
              className="button small"
              onClick={() =>
                setModal({ kind: "create", system: activeSystem })
              }
            >
              <Plus size={12} aria-hidden /> Añadir cuenta
            </button>
          ) : undefined
        }
      />

      {isLoading ? <p className="muted">Cargando cuentas…</p> : null}
      {error ? <ErrorState title="Error" message={error} /> : null}
      {message ? <div className="success-state">{message}</div> : null}

      {!isLoading ? (
        <>
          <IntegrationSystemTabs
            tabs={tabs}
            active={activeSystem}
            onChange={(system) => {
              setActiveSystem(system);
              setExpandedKey(null);
            }}
          />

          <section className="integration-account-list">
            {activeAccounts.length === 0 ? (
              <p className="muted">
                Sin cuentas configuradas en {SYSTEM_LABEL[activeSystem]}.
              </p>
            ) : (
              activeAccounts.map((account) => {
                const key = compoundKey(account);
                const isExpanded = expandedKey === key;
                return (
                  <IntegrationAccountCard
                    key={account.id}
                    account={account}
                    expanded={isExpanded}
                    isAdmin={Boolean(isAdmin)}
                    onToggleExpanded={() =>
                      setExpandedKey(isExpanded ? null : key)
                    }
                    onEdit={() => setModal({ kind: "edit", account })}
                    onDelete={() => setModal({ kind: "delete", account })}
                    onReload={loadAccounts}
                    onError={setError}
                    onSuccess={setMessage}
                  />
                );
              })
            )}
          </section>
        </>
      ) : null}

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
