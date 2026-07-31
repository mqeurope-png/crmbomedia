"use client";

import type { FormField } from "../../lib/formsApi";

/** Preview live del formulario tal y como lo verá el usuario final.
 *  Read-only (los inputs no se envían) — solo para el builder. */
export function WebFormPreview({
  name,
  fields,
}: {
  name: string;
  fields: FormField[];
}) {
  const visible = fields
    .filter((f) => !f.is_hidden && f.field_type !== "hidden")
    .slice()
    .sort((a, b) => a.position - b.position);

  return (
    <div className="wf-preview" aria-label="Vista previa del formulario">
      <h3 className="wf-preview-title">{name || "Formulario sin nombre"}</h3>
      <form
        className="wf-preview-form"
        onSubmit={(e) => e.preventDefault()}
      >
        {visible.length === 0 ? (
          <p className="muted small">Añade campos para ver la vista previa.</p>
        ) : (
          visible.map((f, i) => (
            <div className="wf-preview-field" key={f.id ?? `${f.field_key}-${i}`}>
              {f.field_type === "checkbox" ? (
                <label className="wf-preview-check">
                  <input type="checkbox" disabled /> {f.label || f.field_key}
                  {f.is_required ? <span className="wf-req"> *</span> : null}
                </label>
              ) : f.field_type === "tags" ? (
                <>
                  <label>{f.label || f.field_key}</label>
                  <div className="wf-preview-tags">
                    {f.options.length === 0 ? (
                      <span className="muted small">Sin tags configuradas.</span>
                    ) : (
                      f.options.map((o, oi) => (
                        <label className="wf-preview-check" key={o.tag_id ?? oi}>
                          <input type="checkbox" disabled /> {o.label}
                        </label>
                      ))
                    )}
                  </div>
                </>
              ) : (
                <>
                  <label>
                    {f.label || f.field_key}
                    {f.is_required ? <span className="wf-req"> *</span> : null}
                  </label>
                  {renderControl(f)}
                </>
              )}
              {f.help_text ? (
                <span className="muted small">{f.help_text}</span>
              ) : null}
            </div>
          ))
        )}
        <button type="submit" className="button" disabled>
          Enviar
        </button>
      </form>
    </div>
  );
}

function renderControl(f: FormField) {
  if (f.field_type === "textarea") {
    return <textarea placeholder={f.placeholder ?? ""} rows={3} disabled />;
  }
  if (f.field_type === "select") {
    return (
      <select disabled>
        <option value="">—</option>
        {f.options.map((o, i) => (
          <option key={`${o.value}-${i}`} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    );
  }
  const type = f.field_type === "email" || f.field_type === "tel" ? f.field_type : "text";
  return <input type={type} placeholder={f.placeholder ?? ""} disabled />;
}
