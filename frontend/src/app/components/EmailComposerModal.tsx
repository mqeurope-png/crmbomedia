"use client";

import { useEffect, useState } from "react";
import {
  getEmailAliases,
  sendEmail,
  type EmailAlias,
  type EmailMessage,
} from "../lib/emailsApi";
import { extractErrorMessage } from "../lib/errors";

type Props = {
  contactId?: string | null;
  contactEmail?: string | null;
  /** When set, the modal opens in reply mode with the parent
   *  message id passed straight to the backend. */
  replyTo?: { messageId: string; subject?: string | null } | null;
  onClose: () => void;
  onSent?: (message: EmailMessage) => void;
};

export function EmailComposerModal({
  contactId,
  contactEmail,
  replyTo,
  onClose,
  onSent,
}: Props) {
  const [aliases, setAliases] = useState<EmailAlias[]>([]);
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
  const [bodyText, setBodyText] = useState("");
  const [bodyHtml, setBodyHtml] = useState("");
  const [showHtmlPreview, setShowHtmlPreview] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getEmailAliases()
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
        body_text: bodyText.trim() || null,
        contact_id: contactId ?? null,
        in_reply_to_message_id: replyTo?.messageId ?? null,
      });
      onSent?.(message);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo enviar el email."));
      setSubmitting(false);
    }
  }

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true">
      <div className="modal modal-wide">
        <header>
          <h2>{replyTo ? "Responder" : "Nuevo email"}</h2>
        </header>
        {error ? <p className="form-error">{error}</p> : null}
        {!loadingAliases && aliases.length === 0 ? (
          <p className="form-warning">
            No hay aliases &quot;Send mail as&quot; disponibles. Autoriza Gmail
            desde <a href="/account">/account</a>.
          </p>
        ) : null}
        <form onSubmit={handleSubmit}>
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
                  {a.is_primary ? " (primario)" : ""}
                </option>
              ))}
            </select>
          </label>
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
          <label className="field">
            Cuerpo (texto)
            <textarea
              rows={6}
              value={bodyText}
              onChange={(e) => setBodyText(e.target.value)}
            />
          </label>
          <details>
            <summary>HTML opcional</summary>
            <textarea
              rows={6}
              value={bodyHtml}
              onChange={(e) => setBodyHtml(e.target.value)}
              placeholder="<p>...</p>"
            />
            {bodyHtml ? (
              <button
                type="button"
                className="button small secondary"
                onClick={() => setShowHtmlPreview((v) => !v)}
              >
                {showHtmlPreview ? "Ocultar preview" : "Preview HTML"}
              </button>
            ) : null}
            {showHtmlPreview ? (
              <iframe
                title="Preview HTML"
                className="email-html-preview"
                srcDoc={bodyHtml}
              />
            ) : null}
          </details>
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
    </div>
  );
}
