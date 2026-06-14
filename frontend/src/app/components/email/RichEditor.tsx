"use client";

import { Editor } from "@tinymce/tinymce-react";
import { useRef } from "react";
import type { Editor as TinyMCEEditor } from "tinymce";

// Self-host every TinyMCE asset — never the cloud build (no API key,
// no external CDN, works offline / behind the VPN). The static imports
// below touch `window` at module load, so this file MUST only be loaded
// client-side: EmailComposerModal pulls it in via `next/dynamic` with
// `ssr: false`.
import "tinymce/tinymce";
import "tinymce/models/dom/model";
import "tinymce/themes/silver";
import "tinymce/icons/default";
import "tinymce/plugins/advlist";
import "tinymce/plugins/autolink";
import "tinymce/plugins/lists";
import "tinymce/plugins/link";
import "tinymce/plugins/image";
import "tinymce/plugins/charmap";
import "tinymce/plugins/preview";
import "tinymce/plugins/anchor";
import "tinymce/plugins/searchreplace";
import "tinymce/plugins/visualblocks";
import "tinymce/plugins/code";
import "tinymce/plugins/fullscreen";
import "tinymce/plugins/insertdatetime";
import "tinymce/plugins/media";
import "tinymce/plugins/table";
import "tinymce/plugins/help";
import "tinymce/plugins/wordcount";
import "tinymce/plugins/autosave";
import "tinymce/plugins/emoticons";
import "tinymce/plugins/emoticons/js/emojis";
// Spanish UI strings — the comerciales never see English chrome.
import "tinymce-i18n/langs8/es.js";
// Skins (light theme) + the default content stylesheet rendered inside
// the editor iframe.
import "tinymce/skins/ui/oxide/skin.min.css";
import "tinymce/skins/ui/oxide/content.min.css";
import "tinymce/skins/content/default/content.min.css";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const TOKEN_STORAGE_KEY = "crmbomedia_access_token";

type UploadResponse = {
  public_url: string;
  filename: string;
  content_type: string;
  size_bytes: number;
};

/** Push a blob to the shared email-assets endpoint and return a URL the
 *  recipient's inbox can resolve. The CRM authenticates with a Bearer
 *  token from localStorage (not cookies), so we attach it by hand —
 *  TinyMCE's default `credentials` handling wouldn't carry it. */
async function uploadBlob(blob: Blob, filename: string): Promise<string> {
  const form = new FormData();
  form.append("file", blob, filename);
  const token =
    typeof window !== "undefined"
      ? window.localStorage.getItem(TOKEN_STORAGE_KEY)
      : null;
  const response = await fetch(`${API_BASE_URL}/api/email-templates/assets`, {
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
  const body = (await response.json()) as UploadResponse;
  // Absolute in production (EMAIL_ASSETS_PUBLIC_BASE set); root-relative
  // in dev — absolutise so the editor preview resolves it either way.
  return body.public_url.startsWith("/")
    ? `${API_BASE_URL}${body.public_url}`
    : body.public_url;
}

type RichEditorProps = {
  value: string;
  onChange: (html: string) => void;
  placeholder?: string;
  minHeight?: number;
  /** Unique key for the built-in autosave plugin. The draft survives
   *  a refresh / accidental close and TinyMCE surfaces a "restore"
   *  toolbar button when one exists. Pass something stable per
   *  conversation, e.g. `reply-{threadId}` or `compose-new`. */
  draftKey?: string;
};

export function RichEditor({
  value,
  onChange,
  placeholder,
  minHeight = 400,
  draftKey = "default",
}: RichEditorProps) {
  const editorRef = useRef<TinyMCEEditor | null>(null);

  return (
    <Editor
      onInit={(_evt, editor) => {
        editorRef.current = editor;
      }}
      value={value}
      onEditorChange={(content) => onChange(content)}
      init={{
        height: minHeight,
        menubar: false,
        branding: false,
        promotion: false,
        language: "es",
        placeholder: placeholder ?? "Escribe tu email…",
        // Self-hosted skin assets are bundled via the CSS imports above;
        // tell TinyMCE not to try to fetch them from a base URL.
        skin: false,
        content_css: false,
        plugins: [
          "advlist",
          "autolink",
          "lists",
          "link",
          "image",
          "charmap",
          "preview",
          "anchor",
          "searchreplace",
          "visualblocks",
          "code",
          "fullscreen",
          "insertdatetime",
          "media",
          "table",
          "help",
          "wordcount",
          "autosave",
          "emoticons",
        ],
        toolbar:
          "undo redo restoredraft | blocks | " +
          "bold italic underline strikethrough | forecolor backcolor | " +
          "alignleft aligncenter alignright alignjustify | " +
          "bullist numlist outdent indent | " +
          "link image media table emoticons | " +
          "removeformat code fullscreen | help",
        // Built-in draft autosave: writes to localStorage on a timer
        // so a refresh / accidental close doesn't lose the email. The
        // prefix is keyed per conversation so a reply draft doesn't
        // bleed into a fresh compose. `restoredraft` in the toolbar
        // lets the operator pull the draft back when one exists.
        autosave_prefix: `crmbo-email-{path}{query}-${draftKey}-`,
        autosave_interval: "10s",
        autosave_retention: "60m",
        autosave_restore_when_empty: true,
        autosave_ask_before_unload: true,
        // Paste: keep as much of the source email's look as possible.
        // The bar is "reads like the original", not pixel-perfect.
        paste_data_images: true,
        paste_as_text: false,
        paste_merge_formats: true,
        paste_block_drop: false,
        // Emails lean on inline styles + table layout; allow everything
        // through the schema so backgrounds, gradients and column widths
        // survive a paste.
        valid_elements: "*[*]",
        extended_valid_elements:
          "div[*],span[*],table[*],tr[*],td[*],th[*],tbody[*]," +
          "thead[*],tfoot[*],colgroup[*],col[*]",
        valid_children: "+body[style]",
        automatic_uploads: true,
        images_upload_handler: (blobInfo) =>
          uploadBlob(blobInfo.blob(), blobInfo.filename()),
        content_style: `
          body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif; font-size: 14px; line-height: 1.6; color: #1e293b; padding: 16px; }
          p { margin: 0 0 12px; }
          table { border-collapse: collapse; }
          img { max-width: 100%; height: auto; }
        `,
      }}
    />
  );
}
