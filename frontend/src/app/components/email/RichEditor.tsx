"use client";

import Color from "@tiptap/extension-color";
import Image from "@tiptap/extension-image";
import Link from "@tiptap/extension-link";
import Placeholder from "@tiptap/extension-placeholder";
import { Table } from "@tiptap/extension-table";
import { TableCell } from "@tiptap/extension-table-cell";
import { TableHeader } from "@tiptap/extension-table-header";
import { TableRow } from "@tiptap/extension-table-row";
import TextAlign from "@tiptap/extension-text-align";
import { TextStyle } from "@tiptap/extension-text-style";
import Underline from "@tiptap/extension-underline";
import { EditorContent, useEditor, type Editor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import { PreserveStyles } from "./extensions/PreserveStyles";
import {
  AlignCenter,
  AlignJustify,
  AlignLeft,
  AlignRight,
  Bold,
  Eraser,
  Heading1,
  Heading2,
  Heading3,
  Image as ImageIcon,
  Italic,
  Link as LinkIcon,
  List,
  ListOrdered,
  Minus,
  Pilcrow,
  Strikethrough,
  Underline as UnderlineIcon,
} from "lucide-react";
import { useCallback, useEffect, useRef } from "react";

type RichEditorProps = {
  value: string;
  onChange: (html: string) => void;
  placeholder?: string;
  minHeight?: number;
};

type UploadImageResponse = {
  public_url: string;
  filename: string;
  content_type: string;
  size_bytes: number;
};

async function uploadImage(file: File): Promise<string> {
  const form = new FormData();
  form.append("file", file);
  const token = (typeof window !== "undefined"
    ? window.localStorage.getItem("crmbomedia_access_token")
    : null);
  const base = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
  const response = await fetch(`${base}/api/email-templates/assets`, {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
    body: form,
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => null);
    throw new Error(
      (detail && (detail as { detail?: string }).detail) ||
        `No se pudo subir la imagen (${response.status}).`,
    );
  }
  const body = (await response.json()) as UploadImageResponse;
  // Backend already returns an absolute URL when
  // EMAIL_ASSETS_PUBLIC_BASE is set (required in production so the
  // image renders in recipients' inboxes). In dev / tests the URL is
  // root-relative; absolutise it against the API base so the editor
  // preview shows it even before nginx is in front.
  if (body.public_url.startsWith("/")) {
    return `${base}${body.public_url}`;
  }
  return body.public_url;
}

function ToolbarButton({
  active,
  onClick,
  title,
  children,
}: {
  active?: boolean;
  onClick: () => void;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      className={`re-toolbar-btn${active ? " is-active" : ""}`}
      title={title}
      onMouseDown={(e) => e.preventDefault()}
      onClick={onClick}
    >
      {children}
    </button>
  );
}

function ToolbarDivider() {
  return <span className="re-toolbar-divider" aria-hidden />;
}

function Toolbar({ editor }: { editor: Editor }) {
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const handleAddLink = useCallback(() => {
    const current = editor.getAttributes("link").href as string | undefined;
    const href = window.prompt("URL del enlace", current ?? "https://");
    if (href === null) return;
    if (href === "") {
      editor.chain().focus().extendMarkRange("link").unsetLink().run();
      return;
    }
    editor.chain().focus().extendMarkRange("link").setLink({ href }).run();
  }, [editor]);

  const handleAddImage = useCallback(() => {
    fileInputRef.current?.click();
  }, []);

  const handleFileChosen = useCallback(
    async (event: React.ChangeEvent<HTMLInputElement>) => {
      const file = event.target.files?.[0];
      event.target.value = "";
      if (!file) return;
      try {
        const url = await uploadImage(file);
        editor.chain().focus().setImage({ src: url }).run();
      } catch (err) {
        window.alert(
          err instanceof Error ? err.message : "Error subiendo imagen",
        );
      }
    },
    [editor],
  );

  return (
    <div className="re-toolbar" role="toolbar" aria-label="Formato">
      <ToolbarButton
        title="Heading 1"
        active={editor.isActive("heading", { level: 1 })}
        onClick={() =>
          editor.chain().focus().toggleHeading({ level: 1 }).run()
        }
      >
        <Heading1 size={14} aria-hidden />
      </ToolbarButton>
      <ToolbarButton
        title="Heading 2"
        active={editor.isActive("heading", { level: 2 })}
        onClick={() =>
          editor.chain().focus().toggleHeading({ level: 2 }).run()
        }
      >
        <Heading2 size={14} aria-hidden />
      </ToolbarButton>
      <ToolbarButton
        title="Heading 3"
        active={editor.isActive("heading", { level: 3 })}
        onClick={() =>
          editor.chain().focus().toggleHeading({ level: 3 }).run()
        }
      >
        <Heading3 size={14} aria-hidden />
      </ToolbarButton>
      <ToolbarButton
        title="Párrafo"
        active={editor.isActive("paragraph")}
        onClick={() => editor.chain().focus().setParagraph().run()}
      >
        <Pilcrow size={14} aria-hidden />
      </ToolbarButton>
      <ToolbarDivider />
      <ToolbarButton
        title="Negrita"
        active={editor.isActive("bold")}
        onClick={() => editor.chain().focus().toggleBold().run()}
      >
        <Bold size={14} aria-hidden />
      </ToolbarButton>
      <ToolbarButton
        title="Cursiva"
        active={editor.isActive("italic")}
        onClick={() => editor.chain().focus().toggleItalic().run()}
      >
        <Italic size={14} aria-hidden />
      </ToolbarButton>
      <ToolbarButton
        title="Subrayado"
        active={editor.isActive("underline")}
        onClick={() => editor.chain().focus().toggleUnderline().run()}
      >
        <UnderlineIcon size={14} aria-hidden />
      </ToolbarButton>
      <ToolbarButton
        title="Tachado"
        active={editor.isActive("strike")}
        onClick={() => editor.chain().focus().toggleStrike().run()}
      >
        <Strikethrough size={14} aria-hidden />
      </ToolbarButton>
      <label
        className="re-toolbar-color"
        title="Color de texto"
        onMouseDown={(e) => e.preventDefault()}
      >
        <input
          type="color"
          aria-label="Color de texto"
          onChange={(e) =>
            editor.chain().focus().setColor(e.target.value).run()
          }
        />
      </label>
      <ToolbarDivider />
      <ToolbarButton
        title="Lista"
        active={editor.isActive("bulletList")}
        onClick={() => editor.chain().focus().toggleBulletList().run()}
      >
        <List size={14} aria-hidden />
      </ToolbarButton>
      <ToolbarButton
        title="Lista numerada"
        active={editor.isActive("orderedList")}
        onClick={() => editor.chain().focus().toggleOrderedList().run()}
      >
        <ListOrdered size={14} aria-hidden />
      </ToolbarButton>
      <ToolbarDivider />
      <ToolbarButton
        title="Alinear izquierda"
        active={editor.isActive({ textAlign: "left" })}
        onClick={() => editor.chain().focus().setTextAlign("left").run()}
      >
        <AlignLeft size={14} aria-hidden />
      </ToolbarButton>
      <ToolbarButton
        title="Centrar"
        active={editor.isActive({ textAlign: "center" })}
        onClick={() => editor.chain().focus().setTextAlign("center").run()}
      >
        <AlignCenter size={14} aria-hidden />
      </ToolbarButton>
      <ToolbarButton
        title="Alinear derecha"
        active={editor.isActive({ textAlign: "right" })}
        onClick={() => editor.chain().focus().setTextAlign("right").run()}
      >
        <AlignRight size={14} aria-hidden />
      </ToolbarButton>
      <ToolbarButton
        title="Justificar"
        active={editor.isActive({ textAlign: "justify" })}
        onClick={() => editor.chain().focus().setTextAlign("justify").run()}
      >
        <AlignJustify size={14} aria-hidden />
      </ToolbarButton>
      <ToolbarDivider />
      <ToolbarButton
        title="Insertar enlace"
        active={editor.isActive("link")}
        onClick={handleAddLink}
      >
        <LinkIcon size={14} aria-hidden />
      </ToolbarButton>
      <ToolbarButton title="Insertar imagen" onClick={handleAddImage}>
        <ImageIcon size={14} aria-hidden />
      </ToolbarButton>
      <input
        ref={fileInputRef}
        type="file"
        accept="image/png,image/jpeg,image/gif,image/webp"
        hidden
        onChange={handleFileChosen}
      />
      <ToolbarButton
        title="Línea horizontal"
        onClick={() => editor.chain().focus().setHorizontalRule().run()}
      >
        <Minus size={14} aria-hidden />
      </ToolbarButton>
      <ToolbarDivider />
      <ToolbarButton
        title="Limpiar formato"
        onClick={() =>
          editor.chain().focus().unsetAllMarks().clearNodes().run()
        }
      >
        <Eraser size={14} aria-hidden />
      </ToolbarButton>
    </div>
  );
}

export function RichEditor({
  value,
  onChange,
  placeholder,
  minHeight = 240,
}: RichEditorProps) {
  // Track the last HTML we emitted to the parent. Tiptap normalises
  // empty content to `<p></p>` while the parent stores it as `""`, so
  // a naive `editor.getHTML() !== value` check fires on every keystroke
  // and the resulting `setContent` blows away the cursor (the bug Bart
  // hit). We compare against what WE emitted instead — that way a
  // value change coming from outside (Cargar plantilla, controlled
  // reset) still syncs in.
  const lastEmittedRef = useRef<string>(value);

  const editor = useEditor({
    extensions: [
      StarterKit.configure({
        // Tiptap warns about an internal link collision if we don't
        // disable the built-in link mark before mounting our own.
        link: false,
      }),
      Underline,
      Link.configure({
        autolink: true,
        openOnClick: false,
        HTMLAttributes: { rel: "noopener noreferrer", target: "_blank" },
      }),
      Image.configure({ inline: false, allowBase64: false }),
      TextAlign.configure({ types: ["heading", "paragraph"] }),
      TextStyle,
      Color,
      Placeholder.configure({ placeholder: placeholder ?? "" }),
      Table.configure({ resizable: true }),
      TableRow,
      TableHeader,
      TableCell,
      // Keep inline style="" / bgcolor / width / height / align attrs
      // alive through Tiptap's schema pass; the matching
      // transformPastedHTML below only strips scripts and HTML
      // comments so most of the pasted look survives.
      PreserveStyles,
    ],
    editorProps: {
      transformPastedHTML(html: string) {
        // Minimal sanitisation: the bar is "what an external email
        // editor would have rendered". We can't accept <script> or
        // HTML comments (Outlook ships `<!--[if mso]>` blocks that
        // Tiptap chokes on), but inline `style="..."`, `<style>`
        // blocks, `bgcolor`, and on-event attrs we now keep — the
        // PreserveStyles extension surfaces the inline attrs and
        // CSP at the email-send layer is the right place to block
        // scripts at delivery time.
        return html
          .replace(/<script[\s\S]*?<\/script>/gi, "")
          .replace(/<!--[\s\S]*?-->/g, "");
      },
    },
    content: value || "<p></p>",
    autofocus: "end",
    onUpdate: ({ editor: e }) => {
      const html = e.getHTML();
      lastEmittedRef.current = html;
      onChange(html);
    },
    immediatelyRender: false,
  });

  // Sync external value changes (e.g. "Cargar plantilla") into Tiptap.
  // Skip when the incoming value matches what we just emitted —
  // otherwise typing each character would tear the cursor out of the
  // editor on every re-render.
  useEffect(() => {
    if (!editor) return;
    if (value === lastEmittedRef.current) return;
    editor.commands.setContent(value || "<p></p>", { emitUpdate: false });
    lastEmittedRef.current = value;
  }, [value, editor]);

  if (!editor) {
    return (
      <div className="re-shell" aria-busy>
        <p className="muted small">Cargando editor…</p>
      </div>
    );
  }

  return (
    <div className="re-shell">
      <Toolbar editor={editor} />
      <EditorContent
        editor={editor}
        className="re-content"
        style={{ minHeight }}
      />
    </div>
  );
}
