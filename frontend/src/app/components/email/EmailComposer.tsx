"use client";

import { Code2, Sparkles } from "lucide-react";
import { useState } from "react";
import { RichEditor } from "./RichEditor";

type Mode = "visual" | "html";

type Props = {
  value: string;
  onChange: (html: string) => void;
  placeholder?: string;
};

/** Wrapper around RichEditor that lets the operator drop down to a
 *  raw `<textarea>` when they need to paste a precomposed HTML email
 *  (Brevo template, Composer export). Toggling preserves the buffer
 *  so the visual edits don't disappear when the operator peeks at the
 *  HTML. */
export function EmailComposer({ value, onChange, placeholder }: Props) {
  const [mode, setMode] = useState<Mode>("visual");
  return (
    <div className="email-composer-wrap">
      <div className="composer-modes" role="tablist" aria-label="Modo editor">
        <button
          type="button"
          role="tab"
          aria-selected={mode === "visual"}
          className={`composer-mode-btn${mode === "visual" ? " is-active" : ""}`}
          onClick={() => setMode("visual")}
        >
          <Sparkles size={12} aria-hidden /> Visual
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={mode === "html"}
          className={`composer-mode-btn${mode === "html" ? " is-active" : ""}`}
          onClick={() => setMode("html")}
        >
          <Code2 size={12} aria-hidden /> HTML
        </button>
      </div>
      {mode === "visual" ? (
        <RichEditor
          value={value}
          onChange={onChange}
          placeholder={placeholder}
        />
      ) : (
        <textarea
          className="composer-html-raw"
          rows={18}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder ?? "<p>…</p>"}
        />
      )}
    </div>
  );
}
