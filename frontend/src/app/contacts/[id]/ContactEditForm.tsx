"use client";

/**
 * PR-Ficha-Fix → PR-Editar-Completo. Modal "Editar contacto" con
 * TODOS los campos single-value de la ficha.
 *
 * Diseño:
 *   - Modal grande (2 columnas en desktop) con secciones:
 *     básicos / teléfonos / estado / profesional / dirección /
 *     custom_fields / estado de envíos (admin-only).
 *   - Footer "Cancelar" / "Guardar".
 *   - Sub-modal de confirmación con resumen de cambios.
 *
 * El payload es `Record<string, unknown>` (mismo shape que el
 * `handlePatch` del page.tsx). Solo enviamos los campos
 * efectivamente modificados — el handler hace `exclude_unset` con
 * Pydantic, así que las keys no presentes no tocan la BD.
 *
 * Nuevos campos respecto al PR-Ficha-Fix original:
 *   - Empresa (typeahead /api/companies).
 *   - Propietario (selector users activos).
 *   - Origen del lead (texto).
 *   - Teléfonos múltiples (lista con label + primary).
 *   - Estado de envíos: si está dado de baja y es admin, checkbox
 *     "Reactivar envíos comerciales" que dispara unsubscribe_action.
 */
import { Plus, Save, Star, Trash2, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { extractErrorMessage } from "../../lib/errors";
import {
  getCompanies,
  getContactUnsubscribeStatus,
  getCurrentUser,
  getUsers,
  type Company,
  type Contact,
  type ContactUnsubscribeStatus,
  type User,
} from "../../lib/api";
import {
  listContactPhones,
  type ContactPhone,
} from "../../lib/contactChannelsApi";
import { formatBackendDateTime } from "../../lib/dates";

type Props = {
  contact: Contact;
  open: boolean;
  onClose: () => void;
  onPatch: (payload: Record<string, unknown>) => Promise<void>;
};

type PhoneDraft = {
  // Cliente-side key estable para el render; NO es el id de BD.
  key: string;
  // id de BD (si la row ya existía) — no se persiste, solo nos
  // sirve para distinguir "phone preservado" de "phone nuevo" en
  // el diff. El backend hace REPLACE strategy, así que da igual.
  id: string | null;
  number: string;
  label: string;
  is_primary: boolean;
};

type DraftState = {
  first_name: string;
  last_name: string;
  email: string;
  job_title: string;
  origin: string;
  company_id: string | null;
  /** Solo para display en el selector. */
  company_name: string;
  owner_id: string | null;
  commercial_status: string;
  lead_score: string;
  linkedin_url: string;
  personal_website: string;
  address_line: string;
  address_city: string;
  address_state: string;
  address_postal_code: string;
  address_country_name: string;
  phones: PhoneDraft[];
  custom_fields: Record<string, string>;
  resubscribe: boolean;
};

const STATUS_OPTIONS: ReadonlyArray<[string, string]> = [
  ["new", "Nuevo"],
  ["qualified", "Cualificado"],
  ["working", "Trabajando"],
  ["won", "Cliente"],
  ["lost", "Perdido"],
];

function makePhoneKey(): string {
  return `phone-${Math.random().toString(36).slice(2, 10)}`;
}

function draftFromContact(contact: Contact, phones: ContactPhone[]): DraftState {
  const customRaw =
    contact.custom_fields && typeof contact.custom_fields === "object"
      ? contact.custom_fields
      : {};
  const custom: Record<string, string> = {};
  for (const [k, v] of Object.entries(customRaw)) {
    custom[k] = v == null ? "" : String(v);
  }
  return {
    first_name: contact.first_name ?? "",
    last_name: contact.last_name ?? "",
    email: contact.email ?? "",
    job_title: contact.job_title ?? "",
    origin: contact.origin ?? "",
    company_id: contact.company_id ?? null,
    company_name: "", // se rellena al abrir si hay company_id
    owner_id: contact.owner_user_id ?? null,
    commercial_status: contact.commercial_status ?? "new",
    lead_score:
      contact.lead_score === null || contact.lead_score === undefined
        ? ""
        : String(contact.lead_score),
    linkedin_url: contact.linkedin_url ?? "",
    personal_website: contact.personal_website ?? "",
    address_line: contact.address_line ?? "",
    address_city: contact.address_city ?? "",
    address_state: contact.address_state ?? "",
    address_postal_code: contact.address_postal_code ?? "",
    address_country_name: contact.address_country_name ?? "",
    phones: phones.map((p) => ({
      key: `phone-${p.id}`,
      id: p.id,
      number: p.number ?? "",
      label: p.label ?? "",
      is_primary: p.is_primary,
    })),
    custom_fields: custom,
    resubscribe: false,
  };
}

function buildPayload(
  initial: DraftState,
  current: DraftState,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  const scalarKeys: ReadonlyArray<keyof DraftState> = [
    "first_name",
    "last_name",
    "email",
    "job_title",
    "origin",
    "commercial_status",
    "linkedin_url",
    "personal_website",
    "address_line",
    "address_city",
    "address_state",
    "address_postal_code",
    "address_country_name",
  ];
  for (const key of scalarKeys) {
    if (initial[key] !== current[key]) {
      const value = current[key] as string;
      out[key] = value.trim() === "" ? null : value.trim();
    }
  }
  if (initial.lead_score !== current.lead_score) {
    const raw = current.lead_score.trim();
    out.lead_score = raw === "" ? null : Number(raw);
  }
  if (initial.company_id !== current.company_id) {
    out.company_id = current.company_id;
  }
  if (initial.owner_id !== current.owner_id) {
    out.owner_id = current.owner_id;
  }
  // Phones: comparación por contenido — si cualquier número/label/
  // primary difiere, mandamos el array completo. El backend replaza.
  const initialPhonesSig = JSON.stringify(
    initial.phones.map((p) => [p.number, p.label, p.is_primary]),
  );
  const currentPhonesSig = JSON.stringify(
    current.phones.map((p) => [p.number, p.label, p.is_primary]),
  );
  if (initialPhonesSig !== currentPhonesSig) {
    out.phones = current.phones
      .filter((p) => p.number.trim() !== "")
      .map((p) => ({
        number: p.number.trim(),
        label: p.label.trim() || null,
        is_primary: p.is_primary,
      }));
  }
  const initialJson = JSON.stringify(initial.custom_fields);
  const currentJson = JSON.stringify(current.custom_fields);
  if (initialJson !== currentJson) {
    out.custom_fields = current.custom_fields;
  }
  if (current.resubscribe) {
    out.unsubscribe_action = "resubscribe";
  }
  return out;
}

const FIELD_LABELS: Record<string, string> = {
  first_name: "Nombre",
  last_name: "Apellidos",
  email: "Email",
  phone: "Teléfono",
  phones: "Teléfonos",
  job_title: "Puesto",
  origin: "Origen del lead",
  company_id: "Empresa",
  owner_id: "Propietario",
  commercial_status: "Estado del ciclo",
  lead_score: "Lead score",
  linkedin_url: "LinkedIn",
  personal_website: "Web personal",
  address_line: "Calle",
  address_city: "Ciudad",
  address_state: "Estado / Provincia",
  address_postal_code: "Código postal",
  address_country_name: "País",
  custom_fields: "Campos personalizados",
  unsubscribe_action: "Reactivar envíos",
};

export function ContactEditForm({ contact, open, onClose, onPatch }: Props) {
  const [phones, setPhones] = useState<ContactPhone[]>([]);
  const initial = useMemo(
    () => draftFromContact(contact, phones),
    [contact, phones],
  );
  const [draft, setDraft] = useState<DraftState>(initial);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [unsubStatus, setUnsubStatus] =
    useState<ContactUnsubscribeStatus | null>(null);
  const [isAdmin, setIsAdmin] = useState(false);
  const [companyQuery, setCompanyQuery] = useState("");
  const [companyResults, setCompanyResults] = useState<Company[]>([]);
  const [usersList, setUsersList] = useState<User[]>([]);

  useEffect(() => {
    if (!open) return;
    setError(null);
    setConfirmOpen(false);
    // Phones reales del contacto.
    listContactPhones(contact.id)
      .then(setPhones)
      .catch(() => setPhones([]));
    // Unsub status para enseñar la sección si aplica.
    getContactUnsubscribeStatus(contact.id)
      .then(setUnsubStatus)
      .catch(() => setUnsubStatus(null));
    // Current user para decidir si renderizar el checkbox reactivar.
    getCurrentUser()
      .then((u) => setIsAdmin(u.role === "admin"))
      .catch(() => setIsAdmin(false));
    // Lista de users activos para el selector Propietario.
    getUsers({ limit: 100 })
      .then((rows) => setUsersList(rows.filter((u) => u.is_active)))
      .catch(() => setUsersList([]));
  }, [open, contact.id]);

  useEffect(() => {
    if (open) {
      setDraft(initial);
    }
  }, [open, initial]);

  // Lookup display name de la empresa al abrir el modal (si tiene).
  // El endpoint /api/companies no admite by-id; hacemos una búsqueda
  // amplia y filtramos. Si la empresa no cabe en los primeros 50,
  // queda placeholder con el id (se ve raro pero no rompe).
  useEffect(() => {
    if (!open || !draft.company_id) return;
    if (draft.company_name) return;
    getCompanies({ limit: 50 })
      .then((page) => {
        const hit = page.items.find((c) => c.id === draft.company_id);
        if (hit) {
          setDraft((prev) => ({
            ...prev,
            company_name: hit.name,
          }));
        }
      })
      .catch(() => undefined);
  }, [open, draft.company_id, draft.company_name]);

  // Debounced typeahead empresa.
  useEffect(() => {
    if (!open) return;
    const handle = window.setTimeout(() => {
      getCompanies({ q: companyQuery, limit: 12 })
        .then((page) => setCompanyResults(page.items))
        .catch(() => setCompanyResults([]));
    }, 250);
    return () => window.clearTimeout(handle);
  }, [open, companyQuery]);

  if (!open) return null;

  const customFieldKeys = Object.keys(initial.custom_fields).sort();
  const payload = buildPayload(initial, draft);
  const dirty = Object.keys(payload).length > 0;

  function updateField<K extends keyof DraftState>(
    key: K,
    value: DraftState[K],
  ): void {
    setDraft((prev) => ({ ...prev, [key]: value }));
  }

  function updateCustomField(key: string, value: string): void {
    setDraft((prev) => ({
      ...prev,
      custom_fields: { ...prev.custom_fields, [key]: value },
    }));
  }

  function updatePhone<K extends keyof PhoneDraft>(
    key: string,
    field: K,
    value: PhoneDraft[K],
  ): void {
    setDraft((prev) => ({
      ...prev,
      phones: prev.phones.map((p) =>
        p.key === key ? { ...p, [field]: value } : p,
      ),
    }));
  }

  function setPhonePrimary(key: string): void {
    setDraft((prev) => ({
      ...prev,
      phones: prev.phones.map((p) => ({
        ...p,
        is_primary: p.key === key,
      })),
    }));
  }

  function removePhone(key: string): void {
    setDraft((prev) => {
      const next = prev.phones.filter((p) => p.key !== key);
      // Si quitamos al primary, promueve al primero superviviente.
      if (
        prev.phones.find((p) => p.key === key)?.is_primary &&
        next.length > 0 &&
        !next.some((p) => p.is_primary)
      ) {
        next[0] = { ...next[0], is_primary: true };
      }
      return { ...prev, phones: next };
    });
  }

  function addPhone(): void {
    setDraft((prev) => ({
      ...prev,
      phones: [
        ...prev.phones,
        {
          key: makePhoneKey(),
          id: null,
          number: "",
          label: "",
          is_primary: prev.phones.length === 0,
        },
      ],
    }));
  }

  function validate(): string | null {
    if (!draft.first_name.trim()) return "El nombre es obligatorio.";
    if (!draft.email.trim()) return "El email es obligatorio.";
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(draft.email.trim())) {
      return "El email no tiene un formato válido.";
    }
    for (const p of draft.phones) {
      if (p.number.trim() && p.number.trim().replace(/[^\d+]/g, "").length < 6) {
        return "Hay un teléfono demasiado corto.";
      }
    }
    return null;
  }

  function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    const validationError = validate();
    if (validationError) {
      setError(validationError);
      return;
    }
    setError(null);
    if (!dirty) {
      onClose();
      return;
    }
    setConfirmOpen(true);
  }

  async function handleConfirm() {
    setSubmitting(true);
    setError(null);
    try {
      await onPatch(payload);
      setConfirmOpen(false);
      onClose();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudieron guardar los cambios."));
      setConfirmOpen(false);
    } finally {
      setSubmitting(false);
    }
  }

  const changedFieldLabels = Object.keys(payload).map(
    (key) => FIELD_LABELS[key] ?? key,
  );

  return (
    <div
      className="modal-overlay"
      role="dialog"
      aria-modal="true"
      aria-labelledby="contact-edit-title"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="modal-dialog contact-edit-modal">
        <div className="modal-header">
          <h2 id="contact-edit-title">Editar contacto</h2>
          <button
            type="button"
            className="modal-close"
            onClick={onClose}
            aria-label="Cerrar"
          >
            <X size={16} aria-hidden />
          </button>
        </div>
        <div className="modal-body">
          <form className="contact-edit-grid" onSubmit={handleSubmit}>
            <fieldset className="contact-edit-section">
              <legend>Datos básicos</legend>
              <label>
                <span>Nombre *</span>
                <input
                  type="text"
                  value={draft.first_name}
                  onChange={(e) => updateField("first_name", e.target.value)}
                  required
                  maxLength={120}
                />
              </label>
              <label>
                <span>Apellidos</span>
                <input
                  type="text"
                  value={draft.last_name}
                  onChange={(e) => updateField("last_name", e.target.value)}
                  maxLength={160}
                />
              </label>
              <label>
                <span>Email *</span>
                <input
                  type="email"
                  value={draft.email}
                  onChange={(e) => updateField("email", e.target.value)}
                  required
                />
              </label>
              <label>
                <span>Puesto</span>
                <input
                  type="text"
                  value={draft.job_title}
                  onChange={(e) => updateField("job_title", e.target.value)}
                  maxLength={200}
                />
              </label>
              <label>
                <span>Empresa</span>
                <input
                  type="text"
                  list="contact-edit-companies"
                  value={
                    draft.company_id
                      ? draft.company_name || draft.company_id
                      : companyQuery
                  }
                  onChange={(e) => {
                    const value = e.target.value;
                    setCompanyQuery(value);
                    // Si el valor coincide con un nombre de la lista,
                    // resolvemos a id. Si no, dejamos NULL hasta que
                    // matchee.
                    const hit = companyResults.find((c) => c.name === value);
                    if (hit) {
                      updateField("company_id", hit.id);
                      updateField("company_name", hit.name);
                    } else if (value === "") {
                      updateField("company_id", null);
                      updateField("company_name", "");
                    }
                  }}
                  placeholder="Busca por nombre…"
                />
                <datalist id="contact-edit-companies">
                  {companyResults.map((c) => (
                    <option key={c.id} value={c.name} />
                  ))}
                </datalist>
              </label>
              <label>
                <span>Propietario</span>
                <select
                  value={draft.owner_id ?? ""}
                  onChange={(e) =>
                    updateField("owner_id", e.target.value || null)
                  }
                >
                  <option value="">(Sin propietario)</option>
                  {usersList.map((u) => (
                    <option key={u.id} value={u.id}>
                      {u.full_name || u.email}
                    </option>
                  ))}
                </select>
              </label>
            </fieldset>

            <fieldset className="contact-edit-section">
              <legend>Estado</legend>
              <label>
                <span>Estado del ciclo</span>
                <select
                  value={draft.commercial_status}
                  onChange={(e) =>
                    updateField("commercial_status", e.target.value)
                  }
                >
                  {STATUS_OPTIONS.map(([v, label]) => (
                    <option key={v} value={v}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                <span>Lead score</span>
                <input
                  type="number"
                  className="lead-score-input"
                  value={draft.lead_score}
                  step={1}
                  onChange={(e) => updateField("lead_score", e.target.value)}
                  placeholder="—"
                />
              </label>
              <label>
                <span>Origen del lead</span>
                <input
                  type="text"
                  value={draft.origin}
                  onChange={(e) => updateField("origin", e.target.value)}
                  maxLength={120}
                  placeholder="ej: Web form, Evento Barcelona…"
                />
              </label>
            </fieldset>

            <fieldset className="contact-edit-section contact-edit-section-wide">
              <legend>Teléfonos</legend>
              <ul className="contact-edit-phones">
                {draft.phones.length === 0 ? (
                  <li className="muted small">Sin teléfonos.</li>
                ) : (
                  draft.phones.map((phone) => (
                    <li key={phone.key} className="contact-edit-phone-row">
                      <button
                        type="button"
                        className={`contact-edit-phone-star${
                          phone.is_primary ? " is-primary" : ""
                        }`}
                        onClick={() => setPhonePrimary(phone.key)}
                        title={
                          phone.is_primary
                            ? "Primary"
                            : "Marcar como primary"
                        }
                        aria-label="Marcar como primary"
                      >
                        <Star
                          size={13}
                          aria-hidden
                          fill={phone.is_primary ? "currentColor" : "none"}
                        />
                      </button>
                      <input
                        type="tel"
                        value={phone.number}
                        onChange={(e) =>
                          updatePhone(phone.key, "number", e.target.value)
                        }
                        placeholder="+34 600 …"
                        maxLength={80}
                        className="contact-edit-phone-number"
                      />
                      <input
                        type="text"
                        value={phone.label}
                        onChange={(e) =>
                          updatePhone(phone.key, "label", e.target.value)
                        }
                        placeholder="label"
                        maxLength={80}
                        className="contact-edit-phone-label"
                      />
                      <button
                        type="button"
                        className="icon-button danger"
                        onClick={() => removePhone(phone.key)}
                        title="Quitar"
                        aria-label="Quitar teléfono"
                      >
                        <Trash2 size={12} aria-hidden />
                      </button>
                    </li>
                  ))
                )}
              </ul>
              <button
                type="button"
                className="button secondary small"
                onClick={addPhone}
              >
                <Plus size={12} aria-hidden /> Añadir teléfono
              </button>
            </fieldset>

            <fieldset className="contact-edit-section">
              <legend>Información profesional</legend>
              <label>
                <span>LinkedIn</span>
                <input
                  type="url"
                  value={draft.linkedin_url}
                  onChange={(e) => updateField("linkedin_url", e.target.value)}
                  placeholder="https://linkedin.com/in/…"
                  maxLength={500}
                />
              </label>
              <label>
                <span>Web personal</span>
                <input
                  type="url"
                  value={draft.personal_website}
                  onChange={(e) =>
                    updateField("personal_website", e.target.value)
                  }
                  placeholder="https://…"
                  maxLength={500}
                />
              </label>
            </fieldset>

            <fieldset className="contact-edit-section">
              <legend>Dirección</legend>
              <label>
                <span>Calle</span>
                <input
                  type="text"
                  value={draft.address_line}
                  onChange={(e) => updateField("address_line", e.target.value)}
                  maxLength={500}
                />
              </label>
              <label>
                <span>Ciudad</span>
                <input
                  type="text"
                  value={draft.address_city}
                  onChange={(e) => updateField("address_city", e.target.value)}
                  maxLength={120}
                />
              </label>
              <label>
                <span>Estado / Provincia</span>
                <input
                  type="text"
                  value={draft.address_state}
                  onChange={(e) => updateField("address_state", e.target.value)}
                  maxLength={120}
                />
              </label>
              <label>
                <span>Código postal</span>
                <input
                  type="text"
                  value={draft.address_postal_code}
                  onChange={(e) =>
                    updateField("address_postal_code", e.target.value)
                  }
                  maxLength={20}
                />
              </label>
              <label>
                <span>País</span>
                <input
                  type="text"
                  value={draft.address_country_name}
                  onChange={(e) =>
                    updateField("address_country_name", e.target.value)
                  }
                  maxLength={255}
                />
              </label>
            </fieldset>

            {customFieldKeys.length > 0 ? (
              <fieldset className="contact-edit-section contact-edit-section-wide">
                <legend>Campos personalizados</legend>
                {customFieldKeys.map((key) => (
                  <label key={key}>
                    <span>{key}</span>
                    <input
                      type="text"
                      value={draft.custom_fields[key] ?? ""}
                      onChange={(e) => updateCustomField(key, e.target.value)}
                    />
                  </label>
                ))}
              </fieldset>
            ) : null}

            {unsubStatus && unsubStatus.is_unsubscribed ? (
              <fieldset className="contact-edit-section contact-edit-section-wide contact-edit-unsub">
                <legend>Estado de envíos</legend>
                <p className="small">
                  <strong>Dado de baja</strong>{" "}
                  {unsubStatus.rows[0] ? (
                    <>
                      desde{" "}
                      {formatBackendDateTime(
                        unsubStatus.rows[0].unsubscribed_at,
                      )}
                      {" "}
                      (scope <code>{unsubStatus.rows[0].scope}</code>, origen{" "}
                      <code>{unsubStatus.rows[0].source}</code>)
                    </>
                  ) : null}
                  .
                </p>
                {isAdmin ? (
                  <label className="contact-edit-resubscribe">
                    <input
                      type="checkbox"
                      checked={draft.resubscribe}
                      onChange={(e) =>
                        updateField("resubscribe", e.target.checked)
                      }
                    />
                    <span>
                      Reactivar envíos comerciales al guardar (borra la baja).
                    </span>
                  </label>
                ) : (
                  <p className="muted small">
                    Solo un admin puede reactivar al contacto.
                  </p>
                )}
              </fieldset>
            ) : null}

            {error ? (
              <p className="form-error contact-edit-error">{error}</p>
            ) : null}

            <div className="modal-footer contact-edit-footer">
              <button
                type="button"
                className="button secondary"
                onClick={onClose}
                disabled={submitting}
              >
                Cancelar
              </button>
              <button
                type="submit"
                className="button"
                disabled={submitting || !dirty}
                title={
                  dirty
                    ? undefined
                    : "Modifica al menos un campo para guardar"
                }
              >
                <Save size={12} aria-hidden /> Guardar
              </button>
            </div>
          </form>
        </div>
      </div>

      {confirmOpen ? (
        <div
          className="modal-overlay contact-edit-confirm-overlay"
          role="dialog"
          aria-modal="true"
          aria-labelledby="contact-edit-confirm-title"
        >
          <div className="modal-dialog small contact-edit-confirm-dialog">
            <div className="modal-header">
              <h3 id="contact-edit-confirm-title">Confirmar cambios</h3>
            </div>
            <div className="modal-body">
              <p>
                Vas a actualizar{" "}
                {changedFieldLabels.length === 1 ? (
                  <strong>{changedFieldLabels[0]}</strong>
                ) : (
                  <>
                    los siguientes campos:{" "}
                    <strong>{changedFieldLabels.join(", ")}</strong>
                  </>
                )}
                . ¿Confirmar?
              </p>
            </div>
            <div className="modal-footer">
              <button
                type="button"
                className="button secondary"
                onClick={() => setConfirmOpen(false)}
                disabled={submitting}
              >
                Volver a editar
              </button>
              <button
                type="button"
                className="button"
                onClick={handleConfirm}
                disabled={submitting}
              >
                {submitting ? "Guardando…" : "Confirmar cambios"}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
