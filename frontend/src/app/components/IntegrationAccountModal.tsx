"use client";

import { useEffect, useState } from "react";
import { Modal } from "./Modal";
import type {
  ExternalSystem,
  IntegrationAccount,
  IntegrationAccountCreatePayload,
  IntegrationAccountUpdatePayload,
  IntegrationMode,
  IntegrationStatus,
  QuotaStrategy,
} from "../lib/integrationSettings";

const MODES: IntegrationMode[] = ["sandbox", "live"];
const STATUSES: IntegrationStatus[] = ["not_configured", "configured", "paused"];
const QUOTA_STRATEGIES: QuotaStrategy[] = ["keep_newest", "keep_oldest", "none"];

const ACCOUNT_ID_PATTERN = /^[a-z0-9](?:[a-z0-9_-]*[a-z0-9])?$/;

const SYSTEM_LABEL: Record<ExternalSystem, string> = {
  agilecrm: "AgileCRM",
  brevo: "Brevo",
  freshdesk: "Freshdesk",
  factusol: "FactuSOL",
};

function supportsQuota(system: ExternalSystem): boolean {
  return system === "agilecrm";
}

/** Human-readable label for the per-system auth identifier field. The
 * connector (server-side) decides what to do with the value; the UI
 * just nudges the operator with a meaningful prompt. */
const AUTH_IDENTIFIER_LABEL: Record<ExternalSystem, string> = {
  agilecrm: "Email de login de AgileCRM",
  brevo: "Identificador adicional (opcional)",
  freshdesk: "Subdomain o email",
  factusol: "Usuario API",
};

/** Systems where the identifier is required to authenticate. */
const AUTH_IDENTIFIER_REQUIRED: Record<ExternalSystem, boolean> = {
  agilecrm: true,
  brevo: false,
  freshdesk: false,
  factusol: false,
};

type CreateProps = {
  mode: "create";
  open: boolean;
  system: ExternalSystem;
  onClose: () => void;
  onSubmit: (payload: IntegrationAccountCreatePayload) => Promise<void>;
};

type EditProps = {
  mode: "edit";
  open: boolean;
  account: IntegrationAccount;
  onClose: () => void;
  onSubmit: (payload: IntegrationAccountUpdatePayload) => Promise<void>;
};

type Props = CreateProps | EditProps;

type FormState = {
  account_id: string;
  display_name: string;
  enabled: boolean;
  mode: IntegrationMode;
  status: IntegrationStatus;
  api_base_url: string;
  account_label: string;
  auth_identifier: string;
  credential_status: string;
  notes: string;
  quota_max_contacts: string;
  quota_strategy: QuotaStrategy;
  sync_priority: string;
};

const EMPTY_FORM: FormState = {
  account_id: "",
  display_name: "",
  // Default ON so the new account is ready to sync as soon as the
  // API key lands. The status enum still guards "not_configured"
  // accounts from triggering syncs prematurely.
  enabled: true,
  mode: "sandbox",
  status: "not_configured",
  api_base_url: "",
  account_label: "",
  auth_identifier: "",
  credential_status: "not_configured",
  notes: "",
  quota_max_contacts: "",
  quota_strategy: "none",
  sync_priority: "100",
};

function fromAccount(account: IntegrationAccount): FormState {
  return {
    account_id: account.account_id,
    display_name: account.display_name,
    enabled: account.enabled,
    mode: account.mode,
    status: account.status,
    api_base_url: account.api_base_url ?? "",
    account_label: account.account_label ?? "",
    auth_identifier: account.auth_identifier ?? "",
    credential_status: account.credential_status,
    notes: account.notes ?? "",
    quota_max_contacts:
      account.quota_max_contacts != null ? String(account.quota_max_contacts) : "",
    quota_strategy: account.quota_strategy ?? "none",
    sync_priority: String(account.sync_priority),
  };
}

export function IntegrationAccountModal(props: Props) {
  const isEdit = props.mode === "edit";
  const system: ExternalSystem = isEdit ? props.account.system : props.system;
  const supportQuotaForSystem = supportsQuota(system);

  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // Reset the form every time the modal is (re-)opened.
  useEffect(() => {
    if (!props.open) return;
    if (isEdit) {
      setForm(fromAccount(props.account));
    } else {
      setForm({ ...EMPTY_FORM });
    }
    setError(null);
    setSubmitting(false);
  }, [props.open, isEdit, isEdit ? props.account.id : ""]); // eslint-disable-line react-hooks/exhaustive-deps

  function setField<K extends keyof FormState>(field: K, value: FormState[K]) {
    setForm((prev) => ({ ...prev, [field]: value }));
  }

  function buildCreatePayload(): IntegrationAccountCreatePayload | null {
    if (!ACCOUNT_ID_PATTERN.test(form.account_id)) {
      setError(
        "account_id inválido. Usa solo minúsculas, números, '_' y '-' (ej. 'agilecrm-es').",
      );
      return null;
    }
    const identifier = form.auth_identifier.trim();
    if (AUTH_IDENTIFIER_REQUIRED[system] && !identifier) {
      setError(`${AUTH_IDENTIFIER_LABEL[system]} es obligatorio para ${SYSTEM_LABEL[system]}.`);
      return null;
    }
    const payload: IntegrationAccountCreatePayload = {
      account_id: form.account_id,
      display_name: form.display_name,
      mode: form.mode,
      api_base_url: form.api_base_url || null,
      account_label: form.account_label || null,
      auth_identifier: identifier || null,
      notes: form.notes || null,
      sync_priority: Number(form.sync_priority) || 100,
    };
    if (supportQuotaForSystem && form.quota_max_contacts) {
      payload.quota_max_contacts = Number(form.quota_max_contacts);
      payload.quota_strategy = form.quota_strategy;
    }
    return payload;
  }

  function buildUpdatePayload(): IntegrationAccountUpdatePayload {
    return {
      display_name: form.display_name,
      enabled: form.enabled,
      mode: form.mode,
      status: form.status,
      api_base_url: form.api_base_url || null,
      account_label: form.account_label || null,
      auth_identifier: form.auth_identifier.trim() || null,
      credential_status: form.credential_status,
      notes: form.notes || null,
      sync_priority: Number(form.sync_priority) || 100,
      quota_max_contacts: form.quota_max_contacts
        ? Number(form.quota_max_contacts)
        : null,
      quota_strategy: supportQuotaForSystem ? form.quota_strategy : null,
    };
  }

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      if (props.mode === "create") {
        const payload = buildCreatePayload();
        if (!payload) {
          setSubmitting(false);
          return;
        }
        await props.onSubmit(payload);
      } else {
        await props.onSubmit(buildUpdatePayload());
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : "Error inesperado";
      setError(message);
      setSubmitting(false);
    }
  }

  const title = isEdit
    ? `Editar cuenta: ${props.account.display_name}`
    : `Nueva cuenta de ${SYSTEM_LABEL[system]}`;

  return (
    <Modal open={props.open} onClose={props.onClose} title={title}>
      <form className="modal-form" onSubmit={onSubmit}>
        <label>
          account_id (slug)
          <input
            required
            placeholder="agilecrm-es"
            value={form.account_id}
            readOnly={isEdit}
            title={isEdit ? "El slug no se puede cambiar después de crear" : undefined}
            onChange={(event) => setField("account_id", event.target.value)}
          />
          {isEdit ? (
            <small className="muted">
              El slug no se puede cambiar después de crear.
            </small>
          ) : null}
        </label>
        <label>
          Nombre visible
          <input
            required
            placeholder="AgileCRM España"
            value={form.display_name}
            onChange={(event) => setField("display_name", event.target.value)}
          />
        </label>
        <label>
          Modo
          <select
            value={form.mode}
            onChange={(event) => setField("mode", event.target.value as IntegrationMode)}
          >
            {MODES.map((mode) => (
              <option key={mode} value={mode}>{mode}</option>
            ))}
          </select>
        </label>
        {isEdit ? (
          <>
            <label>
              Activada
              <select
                value={String(form.enabled)}
                onChange={(event) => setField("enabled", event.target.value === "true")}
              >
                <option value="false">No</option>
                <option value="true">Sí</option>
              </select>
            </label>
            <label>
              Estado
              <select
                value={form.status}
                onChange={(event) =>
                  setField("status", event.target.value as IntegrationStatus)
                }
              >
                {STATUSES.map((status) => (
                  <option key={status} value={status}>{status}</option>
                ))}
              </select>
            </label>
            <label>
              Estado de credenciales
              <input
                value={form.credential_status}
                onChange={(event) => setField("credential_status", event.target.value)}
              />
            </label>
          </>
        ) : null}
        <label>
          URL base API
          <input
            placeholder="https://api.example.com"
            value={form.api_base_url}
            onChange={(event) => setField("api_base_url", event.target.value)}
          />
        </label>
        <label>
          {AUTH_IDENTIFIER_LABEL[system]}
          {AUTH_IDENTIFIER_REQUIRED[system] ? " *" : ""}
          <input
            required={AUTH_IDENTIFIER_REQUIRED[system]}
            placeholder={
              system === "agilecrm"
                ? "envios@bomedia.net"
                : "Identificador adicional"
            }
            value={form.auth_identifier}
            onChange={(event) => setField("auth_identifier", event.target.value)}
          />
          <small className="muted">
            {system === "agilecrm"
              ? "AgileCRM autentica con Basic email:api_key. El email se guarda aquí (sin cifrar); la API key sigue cifrada al guardarla en su campo."
              : "Identificador en claro complementario al API key (no es secreto)."}
          </small>
        </label>
        <label>
          Etiqueta de cuenta
          <input
            placeholder="Producción ES"
            value={form.account_label}
            onChange={(event) => setField("account_label", event.target.value)}
          />
        </label>
        <label>
          Prioridad de sincronización
          <input
            type="number"
            min={0}
            max={10000}
            value={form.sync_priority}
            onChange={(event) => setField("sync_priority", event.target.value)}
          />
        </label>
        {supportQuotaForSystem ? (
          <>
            <label>
              Cuota máxima de contactos
              <input
                type="number"
                min={1}
                placeholder="800"
                value={form.quota_max_contacts}
                onChange={(event) =>
                  setField("quota_max_contacts", event.target.value)
                }
              />
            </label>
            <label>
              Estrategia de cuota
              <select
                value={form.quota_strategy}
                onChange={(event) =>
                  setField("quota_strategy", event.target.value as QuotaStrategy)
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
            value={form.notes}
            onChange={(event) => setField("notes", event.target.value)}
          />
        </label>
        {error ? <p className="modal-error">{error}</p> : null}
        <div className="modal-footer">
          <button
            type="button"
            className="button secondary"
            onClick={props.onClose}
            disabled={submitting}
          >
            Cancelar
          </button>
          <button className="button" type="submit" disabled={submitting}>
            {submitting
              ? "Guardando…"
              : isEdit
                ? "Guardar cambios"
                : "Crear cuenta"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
