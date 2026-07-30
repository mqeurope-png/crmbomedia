"use client";

import { ArrowDown, ArrowUp, Save, Trash2 } from "lucide-react";
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
  getForm,
  updateForm,
  type FormField,
  type WebFormBase,
} from "../../lib/formsApi";
import { WebFormPreview } from "./WebFormPreview";

type FormState = WebFormBase & { fields: FormField[] };

/** Builder de 3 columnas: campos (izq) · preview (centro) · config (der). */
export function WebFormEditor({ formId }: { formId: string }) {
  const router = useRouter();
  const isNew = formId === "new";
  const [form, setForm] = useState<FormState | null>(isNew ? blankForm() : null);
  const [users, setUsers] = useState<User[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getUsers().then((rows) => setUsers(rows.filter((u) => u.is_active))).catch(() => undefined);
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

  async function save() {
    setBusy(true);
    setError(null);
    const payload = { ...form!, fields: form!.fields.map((f, i) => ({ ...f, position: i })) };
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
                    type="text" placeholder="clave (field_key)" value={f.field_key}
                    aria-label={`Clave campo ${i + 1}`}
                    onChange={(e) => patchField(i, { field_key: e.target.value })}
                  />
                </div>
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
                {["es", "en", "fr", "de"].map((l) => <option key={l} value={l}>{l.toUpperCase()}</option>)}
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
              <input type="text" placeholder="ID plantilla email (opcional)"
                value={form.confirmation_email_template_id ?? ""}
                onChange={(e) => patch({ confirmation_email_template_id: e.target.value })} />
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
