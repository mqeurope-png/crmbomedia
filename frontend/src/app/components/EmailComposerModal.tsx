"use client";

import { FolderOpen, Save, Sparkles } from "lucide-react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useEffect, useState } from "react";
import {
  getMyEmailAliases,
  sendEmail,
  type EmailMessage,
  type MyAlias,
} from "../lib/emailsApi";
import { extractErrorMessage } from "../lib/errors";
import { SaveTemplateModal } from "./email/SaveTemplateModal";
import {
  TemplatePicker,
  type TemplatePickerSelection,
} from "./email/TemplatePicker";

// TinyMCE touches `window` the moment its module loads, so the editor
// must never render on the server. `ssr: false` keeps it in a
// client-only chunk and shows a lightweight placeholder while the
// (~200 KB) editor bundle streams in.
const RichEditor = dynamic(
  () => import("./email/RichEditor").then((m) => m.RichEditor),
  {
    ssr: false,
    loading: () => <div className="re-loading">Cargando editor…</div>,
  },
);

type Props = {
  contactId?: string | null;
  contactEmail?: string | null;
  /** When set, the modal opens in reply mode with the parent
   *  message id passed straight to the backend. */
  replyTo?: { messageId: string; subject?: string | null } | null;
  onClose: () => void;
  onSent?: (message: EmailMessage) => void;
};

const MERGE_TOKEN_RE = /\{(nombre|empresa|email)\}/;

function hasMergeTokens(text: string): boolean {
  return MERGE_TOKEN_RE.test(text);
}

export function EmailComposerModal({
  contactId,
  contactEmail,
  replyTo,
  onClose,
  onSent,
}: Props) {
  const [aliases, setAliases] = useState<MyAlias[]>([]);
  const [loadingAliases, setLoadingAliases] = useState(true);
  const [fromAlias, setFromAlias] = useState("");
  const [to, setTo] = useState(contactEmail ?? "");
  const [cc, setCc] = useState("");
  const [subject, setSubject] = useState(
    replyTo?.subject
      ? replyTo.subject.toLowerCase().startsWith("re:")
        ? replyTo.subject
        : `Re: ${replyTo.subject}`
      : "",
  );
  const [bodyHtml, setBodyHtml] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [showPicker, setShowPicker] = useState(false);
  const [showSaveModal, setShowSaveModal] = useState(false);

  useEffect(() => {
    getMyEmailAliases()
      .then((items) => {
        setAliases(items);
        const def = items.find((a) => a.is_default) ?? items[0];
        if (def) setFromAlias(def.send_as_email);
      })
      .catch(() => setAliases([]))
      .finally(() => setLoadingAliases(false));
  }, []);

  function splitEmails(raw: string): string[] {
    return raw
      .split(/[,\n;]/)
      .map((s) => s.trim())
      .filter(Boolean);
  }

  function applyTemplate(selection: TemplatePickerSelection) {
    setShowPicker(false);
    setBodyHtml(selection.body_html);
    if (selection.subject) {
      setSubject((prev) => prev || selection.subject || "");
    }
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (submitting) return;
    if (!fromAlias) {
      setError(
        "Conecta Google con permisos Gmail desde /account antes de enviar.",
      );
      return;
    }
    const toList = splitEmails(to);
    if (toList.length === 0) {
      setError("Añade al menos un destinatario en el campo Para.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const message = await sendEmail({
        from_alias: fromAlias,
        to: toList,
        cc: cc.trim() ? splitEmails(cc) : null,
        subject,
        body_html: bodyHtml.trim() || null,
        body_text: null,
        contact_id: contactId ?? null,
        in_reply_to_message_id: replyTo?.messageId ?? null,
      });
      onSent?.(message);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo enviar el email."));
      setSubmitting(false);
    }
  }

  const hasMerge =
    hasMergeTokens(bodyHtml) || hasMergeTokens(subject);

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true">
      <div className="modal modal-wide email-compose-modal">
        <header>
          <h2>{replyTo ? "Responder" : "Nuevo email"}</h2>
        </header>
        {error ? <p className="form-error">{error}</p> : null}
        {!loadingAliases && aliases.length === 0 ? (
          <p className="form-warning">
            No has marcado ningún alias en{" "}
            <Link href="/account">/account</Link>. Marca al menos uno para
            enviar emails desde el CRM.
          </p>
        ) : null}
        <form onSubmit={handleSubmit}>
          {aliases.length === 1 ? (
            <p className="muted small">
              Enviando desde:{" "}
              <strong>
                {aliases[0].display_name
                  ? `${aliases[0].display_name} <${aliases[0].send_as_email}>`
                  : aliases[0].send_as_email}
              </strong>
            </p>
          ) : (
            <label className="field">
              De
              <select
                value={fromAlias}
                onChange={(e) => setFromAlias(e.target.value)}
                disabled={loadingAliases || aliases.length === 0}
              >
                {aliases.map((a) => (
                  <option key={a.send_as_email} value={a.send_as_email}>
                    {a.display_name
                      ? `${a.display_name} <${a.send_as_email}>`
                      : a.send_as_email}
                    {a.is_default ? " (por defecto)" : ""}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label className="field">
            Para
            <input
              type="text"
              value={to}
              onChange={(e) => setTo(e.target.value)}
              placeholder="email@dominio.com, otro@dominio.com"
            />
          </label>
          <label className="field">
            Cc
            <input
              type="text"
              value={cc}
              onChange={(e) => setCc(e.target.value)}
              placeholder="opcional"
            />
          </label>
          <label className="field">
            Asunto
            <input
              type="text"
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              maxLength={500}
            />
          </label>

          <div className="email-compose-tools">
            <button
              type="button"
              className="button secondary small"
              onClick={() => setShowPicker(true)}
            >
              <FolderOpen size={12} aria-hidden /> Cargar plantilla
            </button>
            <button
              type="button"
              className="button secondary small"
              onClick={() => setShowSaveModal(true)}
              disabled={!bodyHtml.trim()}
              title={
                bodyHtml.trim()
                  ? undefined
                  : "Escribe algo antes de guardar la plantilla."
              }
            >
              <Save size={12} aria-hidden /> Guardar como plantilla
            </button>
          </div>

          <label className="field">
            Cuerpo
            <RichEditor
              value={bodyHtml}
              onChange={setBodyHtml}
              placeholder="Escribe tu email. Usa {nombre}, {empresa}, {email} para personalizar."
              minHeight={460}
            />
          </label>

          {hasMerge ? (
            <p className="email-merge-hint">
              <Sparkles size={12} aria-hidden /> El email contiene{" "}
              <code>{"{nombre}"}</code>/<code>{"{empresa}"}</code>/
              <code>{"{email}"}</code>. Se reemplazarán al enviar con los
              datos del contacto destinatario.
            </p>
          ) : null}

          <div className="actions">
            <button
              type="button"
              className="button secondary"
              onClick={onClose}
              disabled={submitting}
            >
              Cancelar
            </button>
            <button
              type="submit"
              className="button"
              disabled={submitting || !fromAlias}
            >
              {submitting ? "Enviando…" : "Enviar"}
            </button>
          </div>
        </form>
      </div>
      {showPicker ? (
        <TemplatePicker
          onSelect={applyTemplate}
          onClose={() => setShowPicker(false)}
        />
      ) : null}
      {showSaveModal ? (
        <SaveTemplateModal
          bodyHtml={bodyHtml}
          subject={subject}
          onClose={() => setShowSaveModal(false)}
          onSaved={() => {
            /* no-op: SaveTemplateModal closes itself */
          }}
        />
      ) : null}
    </div>
  );
}
