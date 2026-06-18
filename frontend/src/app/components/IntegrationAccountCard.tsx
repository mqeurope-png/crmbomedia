"use client";

import {
  ChevronDown,
  ChevronUp,
  CircleDot,
  KeyRound,
  Pencil,
  Trash2,
} from "lucide-react";
import { useState } from "react";
import {
  deleteIntegrationAccountApiKey,
  setIntegrationAccountApiKey,
  type IntegrationAccount,
} from "../lib/integrationSettings";
import { formatBackendDateTime } from "../lib/dates";
import { BrevoAccountPanel } from "./BrevoAccountPanel";
import { SyncPanel } from "./SyncPanel";

type Props = {
  account: IntegrationAccount;
  expanded: boolean;
  onToggleExpanded: () => void;
  onEdit: () => void;
  onDelete: () => void;
  onReload: () => Promise<void> | void;
  onError: (message: string | null) => void;
  onSuccess: (message: string | null) => void;
  isAdmin: boolean;
};

// PR-Timezone-Fix. La util `formatBackendDateTime` ya hace fallback
// a "—" sobre null/undefined y aplica `parseBackendDate` para
// timestamps que viajen sin offset.
const formatDate = (value?: string | null) =>
  value ? formatBackendDateTime(value) : "";

/**
 * Collapsible card for one integration account. Collapsed view shows
 * the essentials (name, status, contact count proxied via account
 * label, last-key date) plus inline quick actions; expanded view
 * reveals the API-key form, the SyncPanel (which carries the full
 * sync history + retry buttons) and the auxiliary metadata.
 *
 * The page coordinates "only one expanded at a time" via the
 * `expanded` prop.
 */
export function IntegrationAccountCard({
  account,
  expanded,
  onToggleExpanded,
  onEdit,
  onDelete,
  onReload,
  onError,
  onSuccess,
  isAdmin,
}: Props) {
  const [apiKeyDraft, setApiKeyDraft] = useState("");

  async function handleSaveApiKey() {
    const draft = apiKeyDraft.trim();
    if (!draft) {
      onError("Introduce una API key antes de guardar.");
      return;
    }
    try {
      await setIntegrationAccountApiKey(
        account.system,
        account.account_id,
        draft,
      );
      onError(null);
      onSuccess(`API key guardada para ${account.display_name}`);
      setApiKeyDraft("");
      await onReload();
    } catch (err) {
      onError(
        err instanceof Error
          ? err.message
          : "No se pudo guardar la API key",
      );
    }
  }

  async function handleDeleteApiKey() {
    const confirmed = window.confirm(
      `¿Borrar la API key de ${account.display_name} (${account.account_id})? La integración quedará desactivada hasta que se reintroduzca.`,
    );
    if (!confirmed) return;
    try {
      await deleteIntegrationAccountApiKey(
        account.system,
        account.account_id,
      );
      onError(null);
      onSuccess(`API key borrada para ${account.display_name}`);
      setApiKeyDraft("");
      await onReload();
    } catch (err) {
      onError(
        err instanceof Error
          ? err.message
          : "No se pudo borrar la API key",
      );
    }
  }

  const statusDotClass =
    account.status === "configured"
      ? "is-ok"
      : account.status === "paused"
        ? "is-warn"
        : "is-off";

  return (
    <article
      className={`integration-account-card${expanded ? " is-expanded" : ""}`}
    >
      <header className="integration-account-card-header">
        <div className="integration-account-card-summary">
          <span className={`integration-status-dot ${statusDotClass}`} aria-hidden>
            <CircleDot size={12} />
          </span>
          <div className="integration-account-card-title">
            <strong>{account.display_name}</strong>
            <span className="muted small">
              <code>{account.account_id}</code> · {account.mode} ·{" "}
              {account.credential_status}
            </span>
          </div>
        </div>
        <div className="integration-account-card-actions">
          {isAdmin ? (
            <>
              <button
                type="button"
                className="button secondary small"
                onClick={onEdit}
                aria-label={`Editar ${account.display_name}`}
              >
                <Pencil size={12} aria-hidden /> Editar
              </button>
              <button
                type="button"
                className="button secondary small"
                onClick={onDelete}
                aria-label={`Borrar ${account.display_name}`}
              >
                <Trash2 size={12} aria-hidden /> Borrar
              </button>
            </>
          ) : null}
          <button
            type="button"
            className="button secondary small"
            onClick={onToggleExpanded}
            aria-expanded={expanded}
            aria-label={expanded ? "Colapsar" : "Expandir"}
          >
            {expanded ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
            {expanded ? " Colapsar" : " Expandir"}
          </button>
        </div>
      </header>

      {expanded ? (
        <div className="integration-account-card-body">
          {isAdmin ? (
            <section className="form-card embedded api-key-block">
              <h3>
                <KeyRound size={14} aria-hidden /> API key
              </h3>
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
                  value={apiKeyDraft}
                  placeholder="Pega aquí la API key del proveedor"
                  onChange={(event) => setApiKeyDraft(event.target.value)}
                />
              </label>
              <div className="actions">
                <button
                  type="button"
                  className="button"
                  onClick={handleSaveApiKey}
                >
                  Guardar API key
                </button>
                {account.has_api_key ? (
                  <button
                    type="button"
                    className="button secondary"
                    onClick={handleDeleteApiKey}
                  >
                    Borrar API key
                  </button>
                ) : null}
              </div>
            </section>
          ) : null}

          {account.system === "brevo" ? (
            <BrevoAccountPanel
              accountId={account.account_id}
              isAdmin={isAdmin}
            />
          ) : null}

          <SyncPanel
            system={account.system}
            accountId={account.account_id}
            account={account}
          />
        </div>
      ) : null}
    </article>
  );
}
