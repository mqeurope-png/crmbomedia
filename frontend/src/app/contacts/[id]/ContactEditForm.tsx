"use client";

/**
 * PR-Ficha-Fix. Modal "Editar contacto" completo, abierto desde el
 * botón ✎ Editar del header. Pre-PR-Ficha-Fix esto era un form
 * inline + un componente "ContactEditForm" en código muerto: la
 * acción del header hacía un scroll al sidebar y nada más.
 *
 * Diseño:
 *   - Modal grande (2 columnas en desktop) con 5 secciones:
 *     básicos / estado / profesional / dirección / custom_fields.
 *   - Footer "Cancelar" / "Guardar".
 *   - Al pulsar "Guardar" se abre un sub-modal de confirmación con
 *     el resumen de campos modificados — Bart no quiere PATCHes
 *     accidentales si el operador clicó por inercia.
 *   - "Volver a editar" preserva el draft.
 *
 * El payload es `Record<string, unknown>` (mismo shape que el
 * `handlePatch` del page.tsx ya consume). Solo enviamos los campos
 * efectivamente modificados — el handler hace `exclude_unset` con
 * Pydantic, así que las keys no presentes no tocan la BD.
 */
import { Save, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { extractErrorMessage } from "../../lib/errors";
import type { Contact } from "../../lib/api";

type Props = {
  contact: Contact;
  open: boolean;
  onClose: () => void;
  /** Mismo callback que usan el header y el strip — page.tsx hace
   *  `updateContact` + refetch dentro. */
  onPatch: (payload: Record<string, unknown>) => Promise<void>;
};

type DraftState = {
  first_name: string;
  last_name: string;
  email: string;
  phone: string;
  job_title: string;
  commercial_status: string;
  lead_score: string; // string para tolerar input numérico vacío
  linkedin_url: string;
  personal_website: string;
  address_line: string;
  address_city: string;
  address_state: string;
  address_postal_code: string;
  address_country_name: string;
  custom_fields: Record<string, string>;
};

const STATUS_OPTIONS: ReadonlyArray<[string, string]> = [
  ["new", "Nuevo"],
  ["qualified", "Cualificado"],
  ["working", "Trabajando"],
  ["won", "Cliente"],
  ["lost", "Perdido"],
];

function draftFromContact(contact: Contact): DraftState {
  // Custom fields llegan como Record<string, unknown> con valores que
  // pueden ser strings, números o nulls. Para el modal los normalizamos
  // a string — la persistencia los re-tipea según convenga.
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
    phone: contact.phone ?? "",
    job_title: contact.job_title ?? "",
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
    custom_fields: custom,
  };
}

function buildPayload(
  initial: DraftState,
  current: DraftState,
): Record<string, unknown> {
  /**
   * Devuelve solo los campos que cambiaron. El campo `lead_score`
   * acepta el case vacío como `null`. `custom_fields` se compara
   * shallow — si ALGÚN par cambia, se manda el dict completo (el
   * backend hace el merge final).
   */
  const out: Record<string, unknown> = {};
  const scalarKeys: ReadonlyArray<keyof DraftState> = [
    "first_name",
    "last_name",
    "email",
    "phone",
    "job_title",
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
  const initialJson = JSON.stringify(initial.custom_fields);
  const currentJson = JSON.stringify(current.custom_fields);
  if (initialJson !== currentJson) {
    // Convertimos a number cuando el original lo era — no perfecto
    // pero suficiente para GRADO_DE_INTERES y demás. Strings vacíos
    // se mandan como tales (el backend los acepta).
    out.custom_fields = current.custom_fields;
  }
  return out;
}

const FIELD_LABELS: Record<string, string> = {
  first_name: "Nombre",
  last_name: "Apellidos",
  email: "Email",
  phone: "Teléfono",
  job_title: "Puesto",
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
};

export function ContactEditForm({ contact, open, onClose, onPatch }: Props) {
  const initial = useMemo(() => draftFromContact(contact), [contact]);
  const [draft, setDraft] = useState<DraftState>(initial);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Al re-abrir el modal con un contacto distinto (o tras refresh) se
  // re-syncroniza el draft. Si está cerrado, mantenemos el último
  // draft por si el operador eligió "Volver a editar" en el sub-modal
  // de confirmación.
  useEffect(() => {
    if (open) {
      setDraft(initial);
      setError(null);
      setConfirmOpen(false);
    }
  }, [open, initial]);

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

  // Validación ligera, no bloqueante (el backend rechaza con 422 si
  // ya filtró algo extraño — vale como segunda barrera).
  function validate(): string | null {
    if (!draft.first_name.trim()) return "El nombre es obligatorio.";
    if (!draft.email.trim()) return "El email es obligatorio.";
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(draft.email.trim())) {
      return "El email no tiene un formato válido.";
    }
    if (
      draft.phone.trim() &&
      draft.phone.trim().replace(/[^\d+]/g, "").length < 6
    ) {
      return "El teléfono parece demasiado corto.";
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
      // Nada que confirmar — cerrar directamente.
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
                <span>Teléfono</span>
                <input
                  type="tel"
                  value={draft.phone}
                  onChange={(e) => updateField("phone", e.target.value)}
                  maxLength={80}
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
