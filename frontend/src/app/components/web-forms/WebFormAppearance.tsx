"use client";

import {
  APPEARANCE_ALIGN,
  APPEARANCE_FONTS,
  APPEARANCE_THEMES,
  type FormAppearance,
} from "../../lib/formsApi";

type Props = {
  value: FormAppearance | null | undefined;
  onChange: (next: FormAppearance) => void;
};

/** Ancho, alineación y estilo del formulario. Cada opción vacía («Por
 *  defecto») deja el aspecto de siempre, así que un formulario publicado no
 *  cambia hasta que se toca aquí. El servidor valida colores y rangos. */
export function WebFormAppearance({ value, onChange }: Props) {
  const ap = value ?? {};
  const set = <K extends keyof FormAppearance>(k: K, v: FormAppearance[K]) =>
    onChange({ ...ap, [k]: v === "" || v === undefined ? null : v });

  const num = (raw: string): number | null => {
    if (raw.trim() === "") return null;
    const n = Number(raw);
    return Number.isFinite(n) ? Math.round(n) : null;
  };

  return (
    <fieldset className="wf-config-group wf-appearance">
      <legend>Apariencia</legend>
      <div className="wf-config-row">
        <label>Ancho (%)
          <input type="number" min={25} max={100} step={5} placeholder="Por defecto"
            value={ap.width_pct ?? ""}
            onChange={(e) => set("width_pct", num(e.target.value))} />
        </label>
        <label>Ancho máximo (px)
          <input type="number" min={240} max={2400} step={20} placeholder="Sin límite"
            value={ap.max_width_px ?? ""}
            onChange={(e) => set("max_width_px", num(e.target.value))} />
        </label>
      </div>
      <label>Alineación
        <select value={ap.align ?? ""}
          onChange={(e) => set("align", (e.target.value || null) as FormAppearance["align"])}>
          <option value="">Por defecto</option>
          {APPEARANCE_ALIGN.map((a) => <option key={a.value} value={a.value}>{a.label}</option>)}
        </select>
      </label>
      <span className="muted small">
        En el móvil (menos de 600 px) ocupa siempre todo el ancho.
      </span>
      <label>Tema
        <select value={ap.theme ?? ""}
          onChange={(e) => set("theme", (e.target.value || null) as FormAppearance["theme"])}>
          <option value="">Por defecto (el de siempre)</option>
          {APPEARANCE_THEMES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
        </select>
      </label>
      <ColorField label="Color principal (botón y foco)" value={ap.primary_color}
        fallback="#2563eb" onChange={(c) => set("primary_color", c)} />
      <ColorField label="Color del texto" value={ap.text_color}
        fallback="#0f172a" onChange={(c) => set("text_color", c)} />
      <ColorField label="Color de fondo del bloque" value={ap.background_color}
        fallback="#ffffff" onChange={(c) => set("background_color", c)} />
      <div className="wf-config-row">
        <label>Radio de borde (px)
          <input type="number" min={0} max={32} placeholder="Por defecto"
            value={ap.radius_px ?? ""}
            onChange={(e) => set("radius_px", num(e.target.value))} />
        </label>
        <label>Tamaño de letra (px)
          <input type="number" min={12} max={22} placeholder="Por defecto"
            value={ap.font_size_px ?? ""}
            onChange={(e) => set("font_size_px", num(e.target.value))} />
        </label>
      </div>
      <label>Tipografía
        <select value={ap.font ?? ""}
          onChange={(e) => set("font", (e.target.value || null) as FormAppearance["font"])}>
          <option value="">Por defecto</option>
          {APPEARANCE_FONTS.map((f) => <option key={f.value} value={f.value}>{f.label}</option>)}
        </select>
      </label>
      <label>Texto del botón
        <input type="text" maxLength={60} placeholder="Enviar"
          value={ap.submit_text ?? ""}
          onChange={(e) => set("submit_text", e.target.value)} />
      </label>
    </fieldset>
  );
}

function ColorField({
  label, value, fallback, onChange,
}: {
  label: string;
  value: string | null | undefined;
  fallback: string;
  onChange: (c: string | null) => void;
}) {
  return (
    <div className="wf-color-field">
      <span>{label}</span>
      {value ? (
        <>
          <input type="color" aria-label={label} value={value}
            onChange={(e) => onChange(e.target.value)} />
          <code className="small">{value}</code>
          <button type="button" className="link-button small" onClick={() => onChange(null)}>
            Por defecto
          </button>
        </>
      ) : (
        <button type="button" className="link-button small" onClick={() => onChange(fallback)}>
          Elegir color
        </button>
      )}
    </div>
  );
}
