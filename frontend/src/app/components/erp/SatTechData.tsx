"use client";

import { useEffect, useId, useRef, useState } from "react";
import { extractErrorMessage } from "../../lib/errors";
import { updateSeguimientoFields, type SeguimientoFieldsPatch } from "../../lib/erpApi";

/** Lote 2 · PR-2 — Cola SAT: los bloques que el taller lee de pie, con las
 *  manos ocupadas y a un brazo de distancia (revisión de diseño, sección 8):
 *
 *  - «Observaciones del comercial»: si hay nota, es lo primero que se lee.
 *    Va arriba y en ámbar; si no hay, el bloque no aparece.
 *  - Datos técnicos (nº de serie, licencia WhiteRIP): se teclean o se cotejan
 *    con el equipo físico, así que van grandes (`--fs-tech`), en monoespaciada
 *    y cada uno en su caja con botón de copiar de 36 px y confirmación
 *    visible («Copiado»). En la card la caja se pinta siempre (con «—» si no
 *    hay dato) para que el layout no baile; en la lista compacta se omite.
 *  - Origen del envío (OFI-TER-SAT) como pastilla.
 *
 *  Los usan las dos cards, la fila de la vista lista y el modo trabajo. */

/** Copia `value` al portapapeles: `navigator.clipboard` y, si no está (http
 *  sin TLS en la tablet del taller, navegador viejo), el `execCommand("copy")`
 *  de toda la vida sobre un textarea temporal. Devuelve si se copió. */
export async function copyText(value: string): Promise<boolean> {
  if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(value);
      return true;
    } catch {
      // cae al fallback (permiso denegado, contexto no seguro…)
    }
  }
  if (typeof document === "undefined" || typeof document.execCommand !== "function") {
    return false;
  }
  const ta = document.createElement("textarea");
  ta.value = value;
  ta.setAttribute("readonly", "");
  ta.style.position = "fixed";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.select();
  let ok = false;
  try {
    ok = document.execCommand("copy");
  } catch {
    ok = false;
  }
  document.body.removeChild(ta);
  return ok;
}

const COPIED_MS = 1800;

/** Botón de copiar de 36 px con confirmación visible al lado («Copiado» /
 *  «No se pudo copiar») que se apaga sola. `what` nombra el dato para el
 *  lector de pantalla («Copiar nº de serie»). */
export function SatCopyButton({ value, what }: { value: string; what: string }) {
  const [state, setState] = useState<"idle" | "done" | "fail">("idle");
  const timer = useRef<number | null>(null);

  useEffect(() => () => {
    if (timer.current !== null) window.clearTimeout(timer.current);
  }, []);

  async function onCopy(e: React.MouseEvent) {
    e.preventDefault();
    e.stopPropagation();
    const ok = await copyText(value);
    setState(ok ? "done" : "fail");
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setState("idle"), COPIED_MS);
  }

  return (
    <span className="sat-copy">
      <button
        type="button"
        className={`sat-copy-btn${state === "done" ? " is-done" : ""}`}
        aria-label={`Copiar ${what}`}
        title={`Copiar ${what}`}
        onClick={onCopy}
      >
        {state === "done" ? "✓" : "⧉"}
      </button>
      <span className={`sat-copy-msg${state === "fail" ? " is-fail" : ""}`} role="status">
        {state === "done" ? "Copiado" : state === "fail" ? "No se pudo copiar" : ""}
      </span>
    </span>
  );
}

/** Una caja de dato técnico: etiqueta pequeña, valor grande en mono y botón
 *  de copiar (solo si hay valor). Sin valor pinta «—». */
export function SatTechField({
  label,
  value,
  what,
}: {
  label: string;
  value: string | null | undefined;
  what: string;
}) {
  const v = (value ?? "").trim();
  return (
    <div className="sat-tech-box">
      <span className="sat-tech-label">{label}</span>
      <div className="sat-tech-row">
        <span className="sat-tech-value">{v || "—"}</span>
        {v ? <SatCopyButton value={v} what={what} /> : null}
      </div>
    </div>
  );
}

/** Pastilla de origen del envío (OFI-TER-SAT). Sin valor → «—» en la card,
 *  nada en la lista compacta. */
export function SatOriginPill({
  value,
  compact = false,
}: {
  value: string | null | undefined;
  compact?: boolean;
}) {
  const v = (value ?? "").trim();
  if (!v && compact) return null;
  return (
    <span className="sat-origin">
      <span className="sat-tech-label">Origen</span>
      <span className="sat-origin-pill">{v || "—"}</span>
    </span>
  );
}

/** Observaciones del comercial: bloque ámbar, solo si hay nota. */
export function SatObservaciones({ notes }: { notes: string | null | undefined }) {
  const text = (notes ?? "").trim();
  if (!text) return null;
  return (
    <div className="sat-obs" role="note" aria-label="Observaciones del comercial">
      <span className="sat-obs-label">Observaciones del comercial</span>
      <span className="sat-obs-text">{text}</span>
    </div>
  );
}

/** Lote 3 · Cola SAT — habilita la edición inline de los datos técnicos SIN
 *  salir de la cola. Se pasa a `SatTechData` como prop `edit`: si falta, la
 *  vista es de solo lectura (la de siempre). `orderId` para el PATCH,
 *  `origins` para el desplegable de origen (mismo catálogo que la ficha) y
 *  `onSaved` recibe los tres campos ya guardados (actualización optimista). */
export type SatTechEdit = {
  orderId: string;
  origins?: string[];
  onSaved?: (patch: SeguimientoFieldsPatch) => void;
};

/** Caja de dato técnico editable (nº de serie / licencia WhiteRIP): mismo
 *  aspecto que `SatTechField` con un botón «Editar»; al pulsarlo, campo de
 *  texto con «Guardar» / «Cancelar» (Enter guarda, Esc cancela). Conserva el
 *  botón «copiar». Guarda solo su campo con `updateSeguimientoFields`. */
function SatTechEditBox({
  label,
  value,
  what,
  field,
  edit,
}: {
  label: string;
  value: string | null | undefined;
  what: string;
  field: "serial_number" | "whiterip_license";
  edit: SatTechEdit;
}) {
  const v = (value ?? "").trim();
  const [editing, setEditing] = useState(false);
  // El borrador se siembra con el valor actual al entrar en edición (start).
  const [draft, setDraft] = useState(v);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function start() {
    setDraft(v);
    setError(null);
    setEditing(true);
  }
  function cancel() {
    setEditing(false);
    setError(null);
  }
  async function save() {
    const next = draft.trim();
    if (next === v) {
      setEditing(false);
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const r = await updateSeguimientoFields(edit.orderId, { [field]: next });
      edit.onSaved?.(r);
      setEditing(false);
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo guardar."));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="sat-tech-box">
      <span className="sat-tech-label">{label}</span>
      {editing ? (
        <div className="sat-tech-edit">
          <input
            className="sat-tech-input"
            aria-label={`Editar ${what}`}
            value={draft}
            disabled={saving}
            autoFocus
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") { e.preventDefault(); void save(); }
              else if (e.key === "Escape") { e.preventDefault(); cancel(); }
            }}
          />
          <button type="button" className="button small" disabled={saving} onClick={save}>
            {saving ? "Guardando…" : "Guardar"}
          </button>
          <button type="button" className="button secondary small" disabled={saving} onClick={cancel}>
            Cancelar
          </button>
        </div>
      ) : (
        <div className="sat-tech-row">
          <span className="sat-tech-value">{v || "—"}</span>
          <span className="sat-tech-actions">
            {v ? <SatCopyButton value={v} what={what} /> : null}
            <button
              type="button"
              className="sat-tech-edit-btn"
              aria-label={`Editar ${what}`}
              title={`Editar ${what}`}
              onClick={start}
            >
              ✎
            </button>
          </span>
        </div>
      )}
      {error ? <span className="form-error small" role="alert">{error}</span> : null}
    </div>
  );
}

/** Origen del envío editable: pastilla + «Editar»; al pulsarlo, combobox con
 *  el catálogo de orígenes (`<input list>` + `<datalist>`, igual que la
 *  ficha) para elegir uno o escribir otro. */
function SatOriginEdit({ value, edit }: { value: string | null | undefined; edit: SatTechEdit }) {
  const v = (value ?? "").trim();
  const listId = useId();
  const origins = edit.origins ?? [];
  const [editing, setEditing] = useState(false);
  // El borrador se siembra con el valor actual al entrar en edición (start).
  const [draft, setDraft] = useState(v);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function start() {
    setDraft(v);
    setError(null);
    setEditing(true);
  }
  function cancel() {
    setEditing(false);
    setError(null);
  }
  async function save() {
    const next = draft.trim();
    if (next === v) {
      setEditing(false);
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const r = await updateSeguimientoFields(edit.orderId, { shipping_origin: next });
      edit.onSaved?.(r);
      setEditing(false);
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo guardar."));
    } finally {
      setSaving(false);
    }
  }

  return (
    <span className="sat-origin">
      <span className="sat-tech-label">Origen</span>
      {editing ? (
        <span className="sat-origin-edit">
          <input
            className="sat-tech-input"
            aria-label="Editar origen del envío"
            list={listId}
            value={draft}
            disabled={saving}
            autoFocus
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") { e.preventDefault(); void save(); }
              else if (e.key === "Escape") { e.preventDefault(); cancel(); }
            }}
          />
          <datalist id={listId}>
            {origins.map((o) => <option key={o} value={o} />)}
          </datalist>
          <button type="button" className="button small" disabled={saving} onClick={save}>
            {saving ? "Guardando…" : "Guardar"}
          </button>
          <button type="button" className="button secondary small" disabled={saving} onClick={cancel}>
            Cancelar
          </button>
          {error ? <span className="form-error small" role="alert">{error}</span> : null}
        </span>
      ) : (
        <>
          <span className="sat-origin-pill">{v || "—"}</span>
          <button
            type="button"
            className="sat-tech-edit-btn"
            aria-label="Editar origen del envío"
            title="Editar origen del envío"
            onClick={start}
          >
            ✎
          </button>
        </>
      )}
    </span>
  );
}

/** Datos técnicos del pedido (nº de serie · licencia WhiteRIP · origen).
 *  `compact` (fila de la lista): solo lo que tenga valor, en cuerpo.
 *  `edit` (Lote 3): habilita la edición inline en la cola (nunca en `compact`;
 *  la lista sigue siendo de consulta). Sin `edit`, todo es de solo lectura. */
export function SatTechData({
  serial,
  license,
  origin,
  compact = false,
  edit,
}: {
  serial: string | null | undefined;
  license: string | null | undefined;
  origin: string | null | undefined;
  compact?: boolean;
  edit?: SatTechEdit;
}) {
  const s = (serial ?? "").trim();
  const l = (license ?? "").trim();
  const o = (origin ?? "").trim();
  if (compact && !s && !l && !o) return null;
  const editable = Boolean(edit) && !compact;
  if (editable && edit) {
    return (
      <div className="sat-tech">
        <SatTechEditBox label="Nº de serie" value={s} what="nº de serie" field="serial_number" edit={edit} />
        <SatTechEditBox
          label="Licencia WhiteRIP" value={l} what="licencia WhiteRIP"
          field="whiterip_license" edit={edit}
        />
        <SatOriginEdit value={o} edit={edit} />
      </div>
    );
  }
  return (
    <div className={`sat-tech${compact ? " is-compact" : ""}`}>
      {!compact || s ? <SatTechField label="Nº de serie" value={s} what="nº de serie" /> : null}
      {!compact || l ? (
        <SatTechField label="Licencia WhiteRIP" value={l} what="licencia WhiteRIP" />
      ) : null}
      <SatOriginPill value={o} compact={compact} />
    </div>
  );
}
