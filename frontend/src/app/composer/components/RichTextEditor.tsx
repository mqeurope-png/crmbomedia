"use client";

/**
 * Rich text editor — contentEditable + execCommand.
 *
 * Ported literal from `bomedia-v4/src/app-rich-editor.jsx`. Kept the
 * deprecated `document.execCommand` API on purpose: the original gets
 * away with zero dependencies and the toolbar features (selection
 * save/restore, block-format wrappers, removeFormat + unlink chained)
 * have subtle behavior that any rewrite to Lexical/Tiptap would lose.
 * The Sprint Email v2.2 reply composer reuses this component as-is.
 *
 * Why selection save/restore: clicking a toolbar button moves focus
 * out of the editable and execCommand silently no-ops. Saving the
 * Range on `mousedown` + restoring it before each command keeps the
 * caret where the user expects.
 *
 * Why `formatBlock` with angle brackets: WebKit-based browsers need
 * the `<tag>` form for `formatBlock`; without that and the saved
 * range, the caret ends up outside the editor and the block change
 * silently no-ops.
 *
 * Why `unlink` after `removeFormat`: `removeFormat` doesn't strip
 * `<a>` tags. One click should clean everything — bold/italic/color
 * AND the link.
 */

import { useEffect, useRef, type ReactNode } from "react";

export interface RichTextEditorProps {
  value: string;
  onChange: (html: string) => void;
  placeholder?: string;
  minHeight?: number;
  fontSize?: string | number;
}

export function RichTextEditor({
  value,
  onChange,
  placeholder,
  minHeight = 120,
  fontSize,
}: RichTextEditorProps) {
  const editorRef = useRef<HTMLDivElement | null>(null);
  const isInternalUpdate = useRef(false);
  const savedRangeRef = useRef<Range | null>(null);

  // Sync external value → DOM without wiping the caret while typing.
  useEffect(() => {
    const el = editorRef.current;
    if (!el) return;
    if (isInternalUpdate.current) {
      isInternalUpdate.current = false;
      return;
    }
    const current = el.innerHTML;
    const incoming = value || "";
    if (current !== incoming) el.innerHTML = incoming;
  }, [value]);

  const emit = (): void => {
    const el = editorRef.current;
    if (!el) return;
    isInternalUpdate.current = true;
    onChange(el.innerHTML);
  };

  const saveSelection = (): void => {
    const sel = window.getSelection && window.getSelection();
    if (!sel || sel.rangeCount === 0) return;
    const range = sel.getRangeAt(0);
    if (
      editorRef.current &&
      editorRef.current.contains(range.commonAncestorContainer)
    ) {
      savedRangeRef.current = range;
    }
  };

  const restoreSelection = (): void => {
    const range = savedRangeRef.current;
    if (!range) {
      // No saved selection — focus + place caret at end.
      const el = editorRef.current;
      if (!el) return;
      el.focus();
      const r = document.createRange();
      r.selectNodeContents(el);
      r.collapse(false);
      const sel = window.getSelection();
      if (!sel) return;
      sel.removeAllRanges();
      sel.addRange(r);
      return;
    }
    editorRef.current?.focus();
    const sel = window.getSelection();
    if (!sel) return;
    sel.removeAllRanges();
    sel.addRange(range);
  };

  const exec = (cmd: string, arg?: string): void => {
    restoreSelection();
    document.execCommand(cmd, false, arg);
    emit();
  };

  const format = (tag: string): void => {
    restoreSelection();
    document.execCommand("formatBlock", false, "<" + tag + ">");
    emit();
  };

  const insertLink = (): void => {
    const url = window.prompt("URL del enlace:", "https://");
    if (url) exec("createLink", url);
  };

  const removeLink = (): void => exec("unlink");

  const clearFormat = (): void => {
    restoreSelection();
    document.execCommand("removeFormat", false);
    document.execCommand("unlink", false);
    emit();
  };

  const btn = (
    label: ReactNode,
    action: () => void,
    title?: string,
  ): ReactNode => (
    <button
      type="button"
      className="rte-btn"
      title={title || (typeof label === "string" ? label : "")}
      onMouseDown={(e) => {
        e.preventDefault();
        saveSelection();
      }}
      onClick={action}
    >
      {label}
    </button>
  );

  return (
    <div className="rte">
      <div className="rte-toolbar">
        {btn("P", () => format("p"), "Párrafo")}
        {btn(<span style={{ fontWeight: 700 }}>H1</span>, () => format("h1"), "Encabezado 1")}
        {btn(<span style={{ fontWeight: 700 }}>H2</span>, () => format("h2"), "Encabezado 2")}
        {btn(<span style={{ fontWeight: 700 }}>H3</span>, () => format("h3"), "Encabezado 3")}
        <span className="rte-sep" />
        {btn(<b>B</b>, () => exec("bold"), "Negrita")}
        {btn(<i>I</i>, () => exec("italic"), "Cursiva")}
        {btn(<u>U</u>, () => exec("underline"), "Subrayado")}
        {btn(<s>S</s>, () => exec("strikeThrough"), "Tachado")}
        <span className="rte-sep" />
        {btn("• Lista", () => exec("insertUnorderedList"), "Lista")}
        {btn("1. Lista", () => exec("insertOrderedList"), "Lista numerada")}
        <span className="rte-sep" />
        {btn("←", () => exec("justifyLeft"), "Izquierda")}
        {btn("↔", () => exec("justifyCenter"), "Centrar")}
        {btn("→", () => exec("justifyRight"), "Derecha")}
        <span className="rte-sep" />
        {btn("🔗", insertLink, "Insertar enlace")}
        {btn("🔗✕", removeLink, "Quitar enlace")}
        {btn("⨯", clearFormat, "Limpiar formato (también quita enlaces)")}
      </div>
      <div
        ref={editorRef}
        className="rte-editor"
        contentEditable
        suppressContentEditableWarning
        onInput={emit}
        onBlur={() => {
          saveSelection();
          emit();
        }}
        onKeyUp={saveSelection}
        onMouseUp={saveSelection}
        data-placeholder={placeholder || "Escribe aquí…"}
        style={{
          minHeight,
          fontSize:
            typeof fontSize === "number"
              ? fontSize + "px"
              : (fontSize as string | undefined),
        }}
      />
    </div>
  );
}
