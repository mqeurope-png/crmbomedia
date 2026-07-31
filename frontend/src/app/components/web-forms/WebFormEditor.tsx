"use client";

import { ArrowDown, ArrowUp, ChevronDown, Save, Trash2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { getUsers, type User } from "../../lib/api";
import { extractErrorMessage } from "../../lib/errors";
import {
  ASSIGNMENT_MODES,
  blankField,
  blankForm,
  createForm,
  FIELD_TYPES,
  FORM_LANGUAGES,
  getContactFieldsMappable,
  getForm,
  listEmailTemplates,
  updateForm,
  type EmailTemplateItem,
  type FormField,
  type MappableField,
  type WebFormBase,
} from "../../lib/formsApi";
import { WebFormPreview } from "./WebFormPreview";
import { WebFormTagsPicker } from "./WebFormTagsPicker";

/** Espejo del slugify del backend para prellenar el field_key vacío. */
function slugifyKey(label: string): string {
  const norm = label
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
  return norm || "campo";
}

function autofillFieldKeys(fields: FormField[]): FormField[] {
  const seen = new Set<string>();
  return fields.map((f) => {
    let key = (f.field_key || "").trim() || slugifyKey(f.label);
    const base = key;
    let n = 2;
    while (seen.has(key)) key = `${base}_${n++}`;
    seen.add(key);
    return { ...f, field_key: key };
  });
}

type FormState = WebFormBase & { fields: FormField[] };

/** Builder de 3 columnas: campos (izq) · preview (centro) · config (der). */
export function WebFormEditor({ formId }: { formId: string }) {
  const router = useRouter();
  const isNew = formId === "new";
  const [form, setForm] = useState<FormState | null>(isNew ? blankForm() : null);
  const [users, setUsers] = useState<User[]>([]);
  const [mappable, setMappable] = useState<{ standard: MappableField[]; custom: MappableField[] }>({ standard: [], custom: [] });
  const [templates, setTemplates] = useState<EmailTemplateItem[]>([]);
  const [moreOpen, setMoreOpen] = useState<Record<number, boolean>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getUsers().then((rows) => setUsers(rows.filter((u) => u.is_active))).catch(() => undefined);
    getContactFieldsMappable().then(setMappable).catch(() => undefined);
    listEmailTemplates().then(setTemplates).catch(() => undefined);
    if (!isNew) {
      getForm(formId)
        .then((f) => setForm(f))
        .catch((err) => setError(extractErrorMessage(err, "No se pudo cargar el formulario.")));
    }
  }, [formId, isNew]);

  if (!form) {
    return <p className="muted">{error ?? "Cargando…"}</p>;
  }

  const patch = (over: Partial<FormState>) => setForm({ ...form, ...over });

  function patchField(idx: number, over: Partial<FormField>) {
    const fields = form!.fields.map((f, i) => (i === idx ? { ...f, ...over } : f));
    setForm({ ...form!, fields });
  }

  function addField() {
    const fields = [...form!.fields, blankField(form!.fields.length)];
    setForm({ ...form!, fields });
  }

  function removeField(idx: number) {
    const fields = form!.fields
      .filter((_, i) => i !== idx)
      .map((f, i) => ({ ...f, position: i }));
    setForm({ ...form!, fields });
  }

  function moveField(idx: number, dir: -1 | 1) {
    const target = idx + dir;
    if (target < 0 || target >= form!.fields.length) return;
    const fields = [...form!.fields];
    [fields[idx], fields[target]] = [fields[target], fields[idx]];
    setForm({ ...form!, fields: fields.map((f, i) => ({ ...f, position: i })) });
  }

  // --- opciones de select/checkbox (Bug 3) ---
  function addOption(idx: number) {
    const opts = [...form!.fields[idx].options, { value: "", label: "" }];
    patchField(idx, { options: opts });
  }
  function patchOption(idx: number, oi: number, over: Partial<{ value: string; label: string }>) {
    const opts = form!.fields[idx].options.map((o, i) => (i === oi ? { ...o, ...over } : o));
    patchField(idx, { options: opts });
  }
  function removeOption(idx: number, oi: number) {
    patchField(idx, { options: form!.fields[idx].options.filter((_, i) => i !== oi) });
  }

  async function save() {
    setBusy(true);
    setError(null);
    // Bug 3: un desplegable necesita al menos una opción.
    const badSelect = form!.fields.find(
      (f) => f.field_type === "select" && f.options.length === 0,
    );
    if (badSelect) {
      setError(`El desplegable «${badSelect.label || badSelect.field_key}» necesita al menos una opción.`);
      setBusy(false);
      return;
    }
    // Bug 1: autogenera field_key vacíos (slugify + colisión) antes de enviar.
    const withKeys = autofillFieldKeys(form!.fields);
    const payload = { ...form!, fields: withKeys.map((f, i) => ({ ...f, position: i })) };
    try {
      const saved = isNew ? await createForm(payload) : await updateForm(formId, payload);
      router.push(`/admin/forms/${saved.id}/embed`);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo guardar el formulario."));
      setBusy(false);
    }
  }

  return (
    <div className="wf-editor">
      {error ? <p className="form-error">{error}</p> : null}
      <div className="wf-editor-grid">
        {/* Izquierda: campos */}
        <section className="wf-editor-col" aria-label="Campos">
          <header className="wf-editor-col-head">
            <h2>Campos</h2>
            <button type="button" className="button small" onClick={addField}>
              + Añadir campo
            </button>
          </header>
          <ul className="wf-fields">
            {form.fields.map((f, i) => (
              <li key={f.id ?? i} className="wf-field-card">
                <div className="wf-field-row">
                  <input
                    type="text" placeholder="Etiqueta" value={f.label}
                    aria-label={`Etiqueta campo ${i + 1}`}
                    onChange={(e) => patchField(i, { label: e.target.value })}
                  />
                  <input
                    type="text" placeholder="Nombre técnico (opcional)" value={f.field_key}
                    aria-label={`Clave campo ${i + 1}`}
                    onChange={(e) => patchField(i, { field_key: e.target.value })}
                  />
                </div>
                <span className="muted small wf-field-hint">
                  Nombre interno usado en submissions/reports. Se genera
                  automáticamente desde la etiqueta si lo dejas vacío.
                </span>
                <div className="wf-field-row">
                  <select
                    value={f.field_type} aria-label={`Tipo campo ${i + 1}`}
                    onChange={(e) => patchField(i, { field_type: e.target.value as FormField["field_type"] })}
                  >
                    {FIELD_TYPES.map((t) => (
                      <option key={t.value} value={t.value}>{t.label}</option>
                    ))}
                  </select>
                  <label className="checkbox-inline small">
                    <input type="checkbox" checked={f.is_required}
                      onChange={(e) => patchField(i, { is_required: e.target.checked })} />
                    Obligatorio
                  </label>
                </div>

                {/* Bug 1: mapear a campo del contacto (no aplica a tags:
                    van a la tabla contact_tags, no a una columna). */}
                {f.field_type !== "tags" ? (
                  <select
                    className="wf-field-map"
                    value={f.maps_to_contact_field ?? ""}
                    aria-label={`Mapear campo ${i + 1}`}
                    onChange={(e) => patchField(i, { maps_to_contact_field: e.target.value || null })}
                  >
                    <option value="">— Sin mapear (solo guardar) —</option>
                    <optgroup label="Campos del contacto">
                      {mappable.standard.map((m) => (
                        <option key={m.value} value={m.value}>{m.label}</option>
                      ))}
                    </optgroup>
                    {mappable.custom.length > 0 ? (
                      <optgroup label="Personalizados">
                        {mappable.custom.map((m) => (
                          <option key={m.value} value={m.value}>{m.label}</option>
                        ))}
                      </optgroup>
                    ) : null}
                  </select>
                ) : null}

                {/* v2 Bug 2: sub-panel de tags del CRM */}
                {f.field_type === "tags" ? (
                  <WebFormTagsPicker
                    value={f.options}
                    fieldIndex={i}
                    onChange={(opts) => patchField(i, { options: opts })}
                  />
                ) : null}

                {/* Bug 3: opciones de select/checkbox */}
                {f.field_type === "select" || f.field_type === "checkbox" ? (
                  <div className="wf-field-options" aria-label={`Opciones campo ${i + 1}`}>
                    <span className="muted small">Opciones</span>
                    {f.options.map((o, oi) => (
                      <div className="wf-option-row" key={oi}>
                        <input
                          type="text" placeholder="Valor" value={o.value}
                          aria-label={`Valor opción ${oi + 1} campo ${i + 1}`}
                          onChange={(e) => patchOption(i, oi, { value: e.target.value })}
                        />
                        <input
                          type="text" placeholder="Etiqueta" value={o.label}
                          aria-label={`Etiqueta opción ${oi + 1} campo ${i + 1}`}
                          onChange={(e) => patchOption(i, oi, { label: e.target.value })}
                        />
                        <button type="button" className="button small danger"
                          aria-label={`Borrar opción ${oi + 1} campo ${i + 1}`}
                          onClick={() => removeOption(i, oi)}>
                          <Trash2 size={11} aria-hidden />
                        </button>
                      </div>
                    ))}
                    <button type="button" className="button small secondary"
                      onClick={() => addOption(i)}>+ Añadir opción</button>
                  </div>
                ) : null}

                {/* Bugs 4/5/6: placeholder, help_text, default_value */}
                <button type="button" className="link-button small"
                  aria-expanded={!!moreOpen[i]}
                  onClick={() => setMoreOpen({ ...moreOpen, [i]: !moreOpen[i] })}>
                  <ChevronDown size={11} aria-hidden /> Más opciones
                </button>
                {moreOpen[i] ? (
                  <div className="wf-field-more">
                    <input type="text" placeholder="Placeholder (opcional)"
                      aria-label={`Placeholder campo ${i + 1}`}
                      value={f.placeholder ?? ""}
                      onChange={(e) => patchField(i, { placeholder: e.target.value })} />
                    <input type="text" placeholder="Texto de ayuda (opcional)"
                      aria-label={`Ayuda campo ${i + 1}`}
                      value={f.help_text ?? ""}
                      onChange={(e) => patchField(i, { help_text: e.target.value })} />
                    <input type="text" placeholder="Valor por defecto (opcional)"
                      aria-label={`Default campo ${i + 1}`}
                      value={f.default_value ?? ""}
                      onChange={(e) => patchField(i, { default_value: e.target.value })} />
                  </div>
                ) : null}

                <div className="wf-field-actions">
                  <button type="button" className="button small secondary" onClick={() => moveField(i, -1)} aria-label={`Subir campo ${i + 1}`}>
                    <ArrowUp size={12} aria-hidden />
                  </button>
                  <button type="button" className="button small secondary" onClick={() => moveField(i, 1)} aria-label={`Bajar campo ${i + 1}`}>
                    <ArrowDown size={12} aria-hidden />
                  </button>
                  <button type="button" className="button small danger" onClick={() => removeField(i)} aria-label={`Borrar campo ${i + 1}`}>
                    <Trash2 size={12} aria-hidden />
                  </button>
                </div>
              </li>
            ))}
          </ul>
        </section>

        {/* Centro: preview */}
        <section className="wf-editor-col" aria-label="Vista previa">
          <WebFormPreview name={form.name} fields={form.fields} />
        </section>

        {/* Derecha: config */}
        <section className="wf-editor-col wf-editor-config" aria-label="Configuración">
          <h2>Configuración</h2>
          <label>Nombre
            <input type="text" value={form.name} onChange={(e) => patch({ name: e.target.value })} />
          </label>
          <label>Slug
            <input type="text" value={form.slug} onChange={(e) => patch({ slug: e.target.value })}
              placeholder="contacto-mbo-es" />
          </label>
          <div className="wf-config-row">
            <label>Marca
              <input type="text" value={form.brand ?? ""} onChange={(e) => patch({ brand: e.target.value })} />
            </label>
            <label>Idioma
              <select value={form.language} onChange={(e) => patch({ language: e.target.value })}>
                {FORM_LANGUAGES.map((l) => <option key={l.value} value={l.value}>{l.label}</option>)}
              </select>
            </label>
          </div>
          <label className="checkbox-inline">
            <input type="checkbox" checked={form.recaptcha_enabled}
              onChange={(e) => patch({ recaptcha_enabled: e.target.checked })} />
            reCAPTCHA v3 (anti-spam)
          </label>

          <fieldset className="wf-config-group">
            <legend>Al enviar</legend>
            <select value={form.submit_success_mode}
              onChange={(e) => patch({ submit_success_mode: e.target.value as "modal" | "redirect" })}>
              <option value="modal">Mostrar mensaje (modal)</option>
              <option value="redirect">Redirigir a URL</option>
            </select>
            {form.submit_success_mode === "modal" ? (
              <textarea rows={2} placeholder="Mensaje de gracias"
                value={form.submit_success_message ?? ""}
                onChange={(e) => patch({ submit_success_message: e.target.value })} />
            ) : (
              <input type="text" placeholder="https://…/gracias"
                value={form.submit_redirect_url ?? ""}
                onChange={(e) => patch({ submit_redirect_url: e.target.value })} />
            )}
            <label className="checkbox-inline small">
              <input type="checkbox" checked={form.send_confirmation_email}
                onChange={(e) => patch({ send_confirmation_email: e.target.checked })} />
              Enviar email de confirmación al lead
            </label>
            {form.send_confirmation_email ? (
              <select
                aria-label="Plantilla de email de confirmación"
                value={form.confirmation_email_template_id ?? ""}
                onChange={(e) => patch({ confirmation_email_template_id: e.target.value || null })}
              >
                <option value="">— Selecciona plantilla —</option>
                {templates.map((t) => (
                  <option key={t.id} value={t.id}>{t.name}</option>
                ))}
              </select>
            ) : null}
          </fieldset>

          <fieldset className="wf-config-group">
            <legend>Asignación</legend>
            <select value={form.assignment_mode}
              onChange={(e) => patch({ assignment_mode: e.target.value as WebFormBase["assignment_mode"] })}>
              {ASSIGNMENT_MODES.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
            </select>
            {form.assignment_mode === "fixed_owner" ? (
              <select value={form.fixed_owner_user_id ?? ""}
                aria-label="Propietario fijo"
                onChange={(e) => patch({ fixed_owner_user_id: e.target.value || null })}>
                <option value="">— Elige usuario —</option>
                {users.map((u) => <option key={u.id} value={u.id}>{u.full_name || u.email}</option>)}
              </select>
            ) : null}
            <label className="checkbox-inline small">
              <input type="checkbox" checked={form.notify_owner_on_new}
                onChange={(e) => patch({ notify_owner_on_new: e.target.checked })} />
              Notificar al owner de cada lead nuevo
            </label>
          </fieldset>
        </section>
      </div>

      <div className="wf-editor-footer">
        <button type="button" className="button" disabled={busy} onClick={save}>
          <Save size={14} aria-hidden /> {busy ? "Guardando…" : "Guardar formulario"}
        </button>
      </div>
    </div>
  );
}
