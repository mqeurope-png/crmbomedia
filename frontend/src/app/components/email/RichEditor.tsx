"use client";

import { Editor } from "@tinymce/tinymce-react";
import {
  forwardRef,
  useImperativeHandle,
  useRef,
} from "react";
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

/** Inline string escape for attribute values we paste into HTML
 *  templates we ourselves write — keeps a video URL with a stray `"`
 *  from breaking the surrounding tag. NOT a generic sanitiser. */
function escapeAttr(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/"/g, "&quot;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

/** Vimeo's thumbnail URL isn't predictable from the video id, so we
 *  ask the public oEmbed endpoint. It's CORS-friendly, no API key
 *  needed. Returns "" on failure so the caller can warn the operator
 *  instead of inserting a broken `<img>`. */
async function fetchVimeoThumbnail(videoId: string): Promise<string> {
  const target = `https://vimeo.com/${videoId}`;
  try {
    const response = await fetch(
      `https://vimeo.com/api/oembed.json?url=${encodeURIComponent(target)}`,
    );
    if (!response.ok) return "";
    const meta = (await response.json()) as { thumbnail_url?: string };
    return meta.thumbnail_url ?? "";
  } catch {
    return "";
  }
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

/** Imperative surface so the send-modal can call `clearDraft()` after
 *  a successful send — the autosave entry survives a soft close and
 *  would otherwise leak into the next conversation. */
export type RichEditorHandle = {
  clearDraft: () => void;
};

export const RichEditor = forwardRef<RichEditorHandle, RichEditorProps>(
  function RichEditor(
    { value, onChange, placeholder, minHeight = 400, draftKey = "default" },
    ref,
  ) {
  const editorRef = useRef<TinyMCEEditor | null>(null);

  useImperativeHandle(
    ref,
    () => ({
      clearDraft() {
        // 1) Ask TinyMCE first so its in-memory cache also resets.
        const editor = editorRef.current;
        if (editor) {
          const autosave = (
            editor.plugins as Record<string, { removeDraft?: () => void }>
          ).autosave;
          autosave?.removeDraft?.();
        }
        // 2) Belt-and-braces: scan localStorage for any stale entries
        // keyed by this draftKey. TinyMCE's removeDraft only wipes the
        // current page-load's prefix; a prior session on the same
        // thread could have left a sibling key around.
        if (typeof window === "undefined") return;
        const suffix = `-${draftKey}-draft`;
        const remove: string[] = [];
        for (let i = 0; i < window.localStorage.length; i++) {
          const key = window.localStorage.key(i);
          if (key && key.startsWith("crmbo-email-") && key.endsWith(suffix)) {
            remove.push(key);
          }
        }
        for (const key of remove) window.localStorage.removeItem(key);
      },
    }),
    [draftKey],
  );

  return (
    <Editor
      onInit={(_evt, editor) => {
        editorRef.current = editor;
      }}
      // TinyMCE 7+ refuses to mount without a declared license key.
      // We're on the self-hosted GPL Community build; "gpl" makes
      // that explicit at no cost. The React wrapper requires this as
      // a top-level prop — passing it inside `init` is rejected with
      // a typed error. Swap to a paid key only if we ever move to
      // TinyMCE Cloud or Enterprise.
      licenseKey="gpl"
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
          "link insertimage insertvideo table emoticons | " +
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
        // Surface the "Subir" tab + a file picker button inside the
        // Insertar imagen modal so the operator doesn't need to drag
        // every time. The handler reuses the same Bearer-token
        // upload path as the inline drop / paste flow.
        image_uploadtab: true,
        image_title: true,
        image_description: true,
        file_picker_types: "image",
        file_picker_callback: (cb, _value, meta) => {
          const input = document.createElement("input");
          input.type = "file";
          input.accept = meta.filetype === "image" ? "image/*" : "*/*";
          input.onchange = async () => {
            const file = input.files?.[0];
            if (!file) return;
            try {
              const url = await uploadBlob(file, file.name);
              cb(url, { alt: file.name, title: file.name });
            } catch (err) {
              window.alert(
                err instanceof Error
                  ? err.message
                  : "Error subiendo imagen",
              );
            }
          };
          input.click();
        },
        // Toolbar overrides for Imagen + Video. The stock `image` and
        // `media` plugin dialogs are needlessly form-heavy ("Código
        // fuente", Ancho, Altura, Avanzado…) and the operators kept
        // hitting them by accident. Replace with two custom buttons
        // that go straight to what they actually want.
        setup: (editor) => {
          // Imagen: skip the modal entirely — file picker → upload →
          // image inserted. Drag-drop and clipboard paste keep working
          // via the editor's own automatic_uploads path, so URL-based
          // inserts (rare) are still possible from the Code view.
          editor.ui.registry.addButton("insertimage", {
            text: "Imagen",
            tooltip: "Insertar imagen desde tu PC",
            icon: "image",
            onAction: () => {
              const input = document.createElement("input");
              input.type = "file";
              input.accept = "image/png,image/jpeg,image/gif,image/webp";
              input.onchange = async () => {
                const file = input.files?.[0];
                if (!file) return;
                try {
                  const url = await uploadBlob(file, file.name);
                  editor.insertContent(
                    `<img src="${url}" alt="${escapeAttr(file.name)}" />`,
                  );
                } catch (err) {
                  editor.windowManager.alert(
                    err instanceof Error
                      ? err.message
                      : "Error subiendo imagen",
                  );
                }
              };
              input.click();
            },
          });

          // Video: optional preview image. When the operator skips it,
          // we fall back to YouTube/Vimeo's own thumbnail. Insert as
          // <a><img></a> — iframes don't render in any major email
          // client, so a clickable thumbnail is what actually reaches
          // the recipient.
          editor.ui.registry.addButton("insertvideo", {
            text: "Video",
            tooltip: "Insertar vídeo de YouTube o Vimeo",
            icon: "embed",
            onAction: () => {
              editor.windowManager.open({
                title: "Insertar vídeo",
                body: {
                  type: "panel",
                  items: [
                    {
                      type: "input",
                      name: "url",
                      label: "URL del vídeo (YouTube o Vimeo)",
                      placeholder: "https://www.youtube.com/watch?v=…",
                    },
                    {
                      type: "urlinput",
                      name: "thumb",
                      label:
                        "Imagen de preview (opcional — si la dejas en blanco, usamos la del vídeo)",
                      filetype: "image",
                    },
                  ],
                },
                buttons: [
                  { type: "cancel", text: "Cancelar" },
                  { type: "submit", text: "Insertar", primary: true },
                ],
                onSubmit: async (api) => {
                  const data = api.getData() as {
                    url?: string;
                    thumb?: string | { value?: string };
                  };
                  const url = String(data.url ?? "").trim();
                  const customThumb =
                    typeof data.thumb === "string"
                      ? data.thumb
                      : (data.thumb?.value ?? "");

                  const yt = url.match(
                    /(?:youtu\.be\/|youtube\.com\/(?:embed\/|v\/|watch\?v=|watch\?.+&v=))([\w-]+)/,
                  );
                  const vm = url.match(/vimeo\.com\/(\d+)/);
                  if (!yt && !vm) {
                    editor.windowManager.alert(
                      "URL no reconocida. Pega un enlace de YouTube o Vimeo.",
                    );
                    return;
                  }
                  const link = yt
                    ? `https://www.youtube.com/watch?v=${yt[1]}`
                    : `https://vimeo.com/${vm![1]}`;

                  let thumb = customThumb;
                  if (!thumb) {
                    thumb = yt
                      ? // YouTube serves predictable JPEGs without an
                        // API call — hqdefault is the universal one
                        // (480x360, always present on every video).
                        `https://img.youtube.com/vi/${yt[1]}/hqdefault.jpg`
                      : await fetchVimeoThumbnail(vm![1]);
                  }
                  if (!thumb) {
                    editor.windowManager.alert(
                      "No se pudo obtener la miniatura del vídeo. Sube una imagen de preview manualmente.",
                    );
                    return;
                  }
                  const html =
                    `<a href="${escapeAttr(link)}" target="_blank" rel="noopener" ` +
                    `style="display:inline-block;text-decoration:none;">` +
                    `<img src="${escapeAttr(thumb)}" alt="Ver vídeo" ` +
                    `style="max-width:560px;width:100%;height:auto;border-radius:6px;" />` +
                    `</a>`;
                  editor.insertContent(html);
                  api.close();
                },
              });
            },
          });
        },
        content_style: `
          body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif; font-size: 14px; line-height: 1.6; color: #1e293b; padding: 16px; }
          p { margin: 0 0 12px; }
          table { border-collapse: collapse; }
          img { max-width: 100%; height: auto; }
        `,
      }}
    />
  );
  },
);
