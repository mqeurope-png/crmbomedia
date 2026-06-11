"use client";

import { Check, Pencil, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

type BaseProps = {
  label: string;
  /** Pretty value rendered while not editing — same string the user
   *  sees in a non-edit row. */
  display: string;
  /** Persist handler. Throw an Error to surface the message inline. */
  onSave: (next: string) => Promise<void> | void;
  /** Disable edit affordances (e.g. for viewer-role). */
  readOnly?: boolean;
};

type TextProps = BaseProps & {
  kind?: "text";
  placeholder?: string;
};

type SelectProps = BaseProps & {
  kind: "select";
  options: ReadonlyArray<[value: string, label: string]>;
};

type Props = TextProps | SelectProps;

/** Inline-editable field — click → input → blur / Enter to save.
 *  Drop-in replacement for `<dt>/<dd>` rows on the contact detail
 *  sidebar so the operator doesn't have to open a separate form to
 *  change a status. */
export function EditableField(props: Props) {
  const { label, display, onSave, readOnly } = props;
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(display);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement | HTMLSelectElement | null>(null);

  useEffect(() => {
    if (!editing) setValue(display);
  }, [display, editing]);

  useEffect(() => {
    if (editing) inputRef.current?.focus();
  }, [editing]);

  async function commit() {
    if (value === display) {
      setEditing(false);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await onSave(value);
      setEditing(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "No se pudo guardar.");
    } finally {
      setBusy(false);
    }
  }

  function cancel() {
    setValue(display);
    setEditing(false);
    setError(null);
  }

  if (!editing) {
    return (
      <div className="editable-field editable-field-display">
        <span className="editable-field-label">{label}</span>
        <span className="editable-field-value">{display || "—"}</span>
        {!readOnly ? (
          <button
            type="button"
            className="editable-field-edit"
            onClick={() => setEditing(true)}
            title={`Editar ${label.toLowerCase()}`}
          >
            <Pencil size={11} aria-hidden />
          </button>
        ) : null}
      </div>
    );
  }

  return (
    <div className="editable-field editable-field-editing">
      <span className="editable-field-label">{label}</span>
      <div className="editable-field-controls">
        {props.kind === "select" ? (
          <select
            ref={inputRef as React.Ref<HTMLSelectElement>}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            disabled={busy}
          >
            {props.options.map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
        ) : (
          <input
            ref={inputRef as React.Ref<HTMLInputElement>}
            type="text"
            value={value}
            placeholder={props.placeholder}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") commit();
              else if (e.key === "Escape") cancel();
            }}
            disabled={busy}
          />
        )}
        <button
          type="button"
          className="editable-field-save"
          onClick={commit}
          disabled={busy}
          title="Guardar"
        >
          <Check size={11} aria-hidden />
        </button>
        <button
          type="button"
          className="editable-field-cancel"
          onClick={cancel}
          disabled={busy}
          title="Cancelar"
        >
          <X size={11} aria-hidden />
        </button>
      </div>
      {error ? <p className="form-error">{error}</p> : null}
    </div>
  );
}
