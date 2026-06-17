"use client";

/**
 * Editor inline save-on-blur — PR-Db ficha contacto. Pensado para los
 * campos del header/strip que Bart quiere editables sin botón "guardar":
 * click → input → blur/Enter → PATCH → ✓ sutil.
 *
 * Cubre 3 kinds:
 *   - text: input text plain.
 *   - number: input numérico (lead_score, etc.).
 *   - select: dropdown con `options`. Save al cambiar (sin blur).
 *
 * El componente se encarga del estado local + busy indicator + revert
 * en caso de error; el caller solo pasa `value` actual + `onSave` que
 * hace la PATCH.
 */
import { Check } from "lucide-react";
import {
  type CSSProperties,
  type KeyboardEvent,
  useEffect,
  useRef,
  useState,
} from "react";

type Base = {
  display: React.ReactNode;
  emptyLabel?: string;
  ariaLabel: string;
  className?: string;
  style?: CSSProperties;
  inputStyle?: CSSProperties;
};

type TextProps = Base & {
  kind?: "text";
  value: string | null;
  onSave: (next: string) => Promise<void>;
};

type NumberProps = Base & {
  kind: "number";
  value: number | null;
  onSave: (next: number | null) => Promise<void>;
  step?: number;
  min?: number;
  max?: number;
};

type SelectProps = Base & {
  kind: "select";
  value: string;
  onSave: (next: string) => Promise<void>;
  options: ReadonlyArray<[string, string]>;
};

type Props = TextProps | NumberProps | SelectProps;

export function InlineEdit(props: Props) {
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [showDone, setShowDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState<string>(
    props.kind === "number"
      ? props.value === null
        ? ""
        : String(props.value)
      : String(props.value ?? ""),
  );
  const inputRef = useRef<HTMLInputElement | HTMLSelectElement>(null);

  useEffect(() => {
    if (editing) inputRef.current?.focus();
  }, [editing]);

  useEffect(() => {
    setDraft(
      props.kind === "number"
        ? props.value === null
          ? ""
          : String(props.value)
        : String(props.value ?? ""),
    );
  }, [props.kind, props.value]);

  async function save(raw: string) {
    setError(null);
    setBusy(true);
    try {
      if (props.kind === "number") {
        const trimmed = raw.trim();
        const next = trimmed === "" ? null : Number(trimmed);
        if (next !== null && Number.isNaN(next)) {
          throw new Error("Número no válido");
        }
        await props.onSave(next);
      } else if (props.kind === "select") {
        await props.onSave(raw);
      } else {
        await props.onSave(raw.trim());
      }
      setShowDone(true);
      setTimeout(() => setShowDone(false), 1500);
      setEditing(false);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Error guardando");
    } finally {
      setBusy(false);
    }
  }

  function onKey(event: KeyboardEvent) {
    if (event.key === "Enter" && props.kind !== "select") {
      event.preventDefault();
      save(draft);
    } else if (event.key === "Escape") {
      setEditing(false);
      setDraft(
        props.kind === "number"
          ? props.value === null
            ? ""
            : String(props.value)
          : String(props.value ?? ""),
      );
    }
  }

  if (!editing) {
    return (
      <button
        type="button"
        className={`inline-edit-trigger ${props.className ?? ""}`}
        style={props.style}
        onClick={() => setEditing(true)}
        aria-label={`${props.ariaLabel} (clic para editar)`}
      >
        {props.display ?? (
          <span className="muted">{props.emptyLabel ?? "—"}</span>
        )}
        {showDone ? (
          <span className="inline-edit-done" aria-hidden>
            <Check size={11} />
          </span>
        ) : null}
      </button>
    );
  }

  if (props.kind === "select") {
    return (
      <span className="inline-edit-editing">
        <select
          ref={inputRef as React.RefObject<HTMLSelectElement>}
          value={draft}
          disabled={busy}
          onChange={(e) => {
            setDraft(e.target.value);
            save(e.target.value);
          }}
          onBlur={() => setEditing(false)}
          onKeyDown={onKey}
          style={props.inputStyle}
          aria-label={props.ariaLabel}
        >
          {props.options.map(([v, label]) => (
            <option key={v} value={v}>
              {label}
            </option>
          ))}
        </select>
        {error ? <span className="form-error small">{error}</span> : null}
      </span>
    );
  }

  return (
    <span className="inline-edit-editing">
      <input
        ref={inputRef as React.RefObject<HTMLInputElement>}
        type={props.kind === "number" ? "number" : "text"}
        value={draft}
        disabled={busy}
        step={props.kind === "number" ? props.step ?? 1 : undefined}
        min={props.kind === "number" ? props.min : undefined}
        max={props.kind === "number" ? props.max : undefined}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => save(draft)}
        onKeyDown={onKey}
        style={props.inputStyle}
        aria-label={props.ariaLabel}
      />
      {error ? <span className="form-error small">{error}</span> : null}
    </span>
  );
}
