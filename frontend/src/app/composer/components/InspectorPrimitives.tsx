"use client";

/**
 * Inspector primitives — literal port of `Field`, `Toggle`, `Section`
 * from `bomedia-v4/app-inspector.jsx` (lines 153-188).
 *
 * Used by every per-type editor (PimpamHeroEditor, ProductEditor,
 * TextEditor, etc.) to wrap a labeled control or a collapsible
 * section. Class names match the original so the CSS already in
 * `composer.css` (`.field`, `.field-label-row`, `.field-label`,
 * `.field-hint`, `.toggle`, `.insp-section`, …) lights them up.
 */

import { useState, type ReactNode } from "react";

import { Icon } from "./Icon";

export interface FieldProps {
  label: ReactNode;
  hint?: ReactNode;
  children: ReactNode;
  action?: ReactNode;
}

export function Field({ label, hint, children, action }: FieldProps) {
  return (
    <div className="field">
      <div className="field-label-row">
        <label className="field-label">{label}</label>
        {action}
      </div>
      {children}
      {hint && <div className="field-hint">{hint}</div>}
    </div>
  );
}

export interface ToggleProps {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: ReactNode;
}

export function Toggle({ checked, onChange, label }: ToggleProps) {
  return (
    <button
      type="button"
      className={"toggle" + (checked ? " on" : "")}
      onClick={() => onChange(!checked)}
    >
      <span className="toggle-track">
        <span className="toggle-thumb" />
      </span>
      <span className="toggle-label">{label}</span>
    </button>
  );
}

export interface SectionProps {
  title: ReactNode;
  children: ReactNode;
  defaultOpen?: boolean;
}

export function Section({ title, children, defaultOpen = true }: SectionProps) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="insp-section">
      <button
        type="button"
        className="insp-section-header"
        onClick={() => setOpen((o) => !o)}
      >
        <span
          style={{
            transform: open ? "rotate(90deg)" : "none",
            transition: "transform .15s",
            display: "inline-flex",
          }}
        >
          <Icon name="chevron" size={12} />
        </span>
        {title}
      </button>
      {open && <div className="insp-section-body">{children}</div>}
    </div>
  );
}
