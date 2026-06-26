"use client";

import { Download, Plus, RefreshCw } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { GmailBackfillSection } from "../../components/GmailBackfillSection";
import { PerContactBackfillBanner } from "../../components/PerContactBackfillBanner";
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
  triggerGmailTemplatesImport,
  triggerSyncAllAccounts,
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
  const [syncingAll, setSyncingAll] = useState(false);
  // QoL post-Notes — checkbox "Sincronización completa". Pasa
  // `full_sync=true` al backend, que encola con payload {"full_sync":
  // true}. El handler de cada integración lo respeta para ignorar la
  // watermark del último sync y re-fetch todo el universo. Útil para
  // recuperar campos nuevos del mapper (Note1..Note10) en contactos
  // antiguos.
  const [fullSync, setFullSync] = useState(false);
  // Importador one-shot de templates Gmail con prefijo `[TPL] `.
  const [importingTpls, setImportingTpls] = useState(false);
  const [deleteAfterImport, setDeleteAfterImport] = useState(false);

  const isAdmin = user?.role === "admin";

  async function onImportGmailTemplates() {
    const confirmMsg = deleteAfterImport
      ? "Importa todos los drafts Gmail con prefijo `[TPL] ` a las plantillas CRM y BORRA cada draft de Gmail tras un insert exitoso. El job corre en background — la página NO se queda esperando. ¿Continuar?"
      : "Importa todos los drafts Gmail con prefijo `[TPL] ` a las plantillas CRM. Los drafts Gmail NO se borran (limpia manualmente o relanza con la opción 'borrar tras importar'). El job corre en background — la página NO se queda esperando. ¿Continuar?";
    if (!window.confirm(confirmMsg)) return;
    setImportingTpls(true);
    setError(null);
    setMessage(null);
    try {
      // El endpoint async devuelve 202 + sync_log_id de inmediato.
      // En 1-3 min el worker termina; el operador refresca cuando le
      // apetezca o consulta el SyncLog directamente con el id.
      const enq = await triggerGmailTemplatesImport({
        deleteAfter: deleteAfterImport,
      });
      setMessage(
        `Import Gmail encolado (sync_log_id=${enq.sync_log_id}). ` +
          "Tarda 1-3 min en background; recarga la lista de plantillas dentro de un rato para ver las nuevas.",
      );
    } catch (err) {
      // Banner rojo prominente con detail del backend. Bart reportó
      // "silent fail" previo; ahora el detail se concatena con el
      // contexto para que el operador sepa qué acción falló.
      const detail = extractErrorMessage(
        err,
        "No se pudo encolar el import de plantillas de Gmail.",
      );
      setError(`Import Gmail falló: ${detail}`);
    } finally {
      setImportingTpls(false);
    }
  }

  async function onSyncAll() {
    if (
      fullSync &&
      !window.confirm(
        "Esto re-procesa TODOS los contactos de TODAS las cuentas habilitadas. Puede tardar varias horas. ¿Continuar?",
      )
    ) {
      return;
    }
    setSyncingAll(true);
    setError(null);
    setMessage(null);
    try {
      const result = await triggerSyncAllAccounts({ fullSync });
      const enq = result.enqueued_count;
      const skp = result.skipped_count;
      const skipNote = skp > 0 ? ` (${skp} sin operación o ya en curso)` : "";
      const kind = result.full_sync ? "Full sync" : "Sincronización";
      setMessage(
        `${kind} lanzada para ${enq} cuenta${enq === 1 ? "" : "s"} activa${
          enq === 1 ? "" : "s"
        }` +
          skipNote +
          ". Mira el progreso desde cada cuenta o el listado de sync-logs.",
      );
    } catch (err) {
      setError(
        extractErrorMessage(err, "No se pudo lanzar la sincronización global.")
      );
    } finally {
      setSyncingAll(false);
    }
  }

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
            <div className="header-actions">
              <label
                className="checkbox-inline small"
                title="Re-fetch completo: ignora la watermark del último sync y procesa todo el universo. Recomendado tras cambios del mapper (p.ej. recuperar Note1..Note10 históricas)."
              >
                <input
                  type="checkbox"
                  checked={fullSync}
                  disabled={syncingAll}
                  onChange={(e) => setFullSync(e.target.checked)}
                />
                Sync completa
              </label>
              <button
                type="button"
                className="button small secondary"
                onClick={onSyncAll}
                disabled={syncingAll}
                title="Encola un sync para cada cuenta habilitada de todos los sistemas"
              >
                <RefreshCw
                  size={12}
                  aria-hidden
                  className={syncingAll ? "spin" : undefined}
                />{" "}
                {syncingAll
                  ? "Sincronizando…"
                  : fullSync
                  ? "Re-fetch completo (full sync)"
                  : "Sincronizar todas las cuentas"}
              </button>
              <button
                type="button"
                className="button small"
                onClick={() =>
                  setModal({ kind: "create", system: activeSystem })
                }
              >
                <Plus size={12} aria-hidden /> Añadir cuenta
              </button>
              <label
                className="checkbox-inline small"
                title="Si marcado, cada draft Gmail se borra de tu buzón tras un insert exitoso. Si no, se queda en Gmail y limpias manualmente."
              >
                <input
                  type="checkbox"
                  checked={deleteAfterImport}
                  disabled={importingTpls}
                  onChange={(e) => setDeleteAfterImport(e.target.checked)}
                />
                Borrar de Gmail tras importar
              </label>
              <button
                type="button"
                className="button small secondary"
                onClick={onImportGmailTemplates}
                disabled={importingTpls}
                title="One-shot: copia los drafts Gmail con prefijo [TPL] a las plantillas CRM"
              >
                <Download
                  size={12}
                  aria-hidden
                  className={importingTpls ? "spin" : undefined}
                />{" "}
                {importingTpls
                  ? "Importando plantillas…"
                  : "📥 Importar plantillas desde Gmail"}
              </button>
            </div>
          ) : undefined
        }
      />

      {isLoading ? <p className="muted">Cargando cuentas…</p> : null}
      {error ? <ErrorState title="Error" message={error} /> : null}
      {message ? <div className="success-state">{message}</div> : null}

      {!isLoading ? (
        <>
          {/* PR-Auto-Backfill-Gmail-Por-Contacto. Banner que aparece tras
              un sync masivo con contactos nuevos sin histórico Gmail. */}
          {isAdmin ? <PerContactBackfillBanner onError={setError} /> : null}

          <IntegrationSystemTabs
            tabs={tabs}
            active={activeSystem}
            onChange={(system) => {
              setActiveSystem(system);
              setExpandedKey(null);
            }}
          />

          {activeSystem === "brevo" && isAdmin ? (
            // Sprint-Push-CRM-Brevo. Link al panel de mappings owner ↔ lista
            // Brevo. Vive en página aparte porque la tabla es grande y la
            // página de cuentas ya tiene mucho contenido.
            <div
              className="info-banner"
              style={{ margin: "0.5rem 0 1rem 0" }}
            >
              <strong>Push CRM → Brevo:</strong> los contactos del CRM con
              owner asignado se suben a Brevo en la lista del owner.
              {" "}
              <Link href="/admin/integrations/brevo-mappings">
                Configurar mapeo de listas →
              </Link>
            </div>
          ) : null}

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

          {/* Sprint-Backfill-Gmail. Sección admin para tirar de Gmail
              3 años de conversaciones entre alias de comerciales y
              contactos del CRM. Vive aquí (no en su propia página)
              porque conceptualmente es una operación sobre la
              integración Gmail. */}
          {isAdmin ? (
            <GmailBackfillSection
              onError={setError}
              onMessage={setMessage}
            />
          ) : null}
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
