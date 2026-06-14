"use client";

import {
  Ban,
  CalendarClock,
  FolderOpen,
  PenLine,
  Save,
  Sparkles,
} from "lucide-react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { getCurrentUser } from "../lib/api";
import {
  listEmailSignatures,
  type EmailSignature,
} from "../lib/emailSignaturesApi";
import {
  getMyEmailAliases,
  sendEmail,
  type EmailMessage,
  type MyAlias,
} from "../lib/emailsApi";
import { extractErrorMessage } from "../lib/errors";
import { SaveTemplateModal } from "./email/SaveTemplateModal";
import { ScheduleSendDialog } from "./email/ScheduleSendDialog";
import {
  TemplatePicker,
  type TemplatePickerSelection,
} from "./email/TemplatePicker";

// TinyMCE touches `window` the moment its module loads, so the editor
// must never render on the server. `ssr: false` keeps it in a
// client-only chunk and shows a lightweight placeholder while the
// (~200 KB) editor bundle streams in.
type RichEditorHandle = {
  clearDraft: () => void;
};
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

// HTML comment delimiters around the signature block. We use them to
// find + replace a previously-inserted signature without parsing the
// editor DOM. Avoids accidentally clobbering the operator's own
// content that happens to look like a signature.
const SIG_OPEN = "<!--crmbo:signature-->";
const SIG_CLOSE = "<!--/crmbo:signature-->";
const SIG_BLOCK_RE = new RegExp(
  `${SIG_OPEN}[\\s\\S]*?${SIG_CLOSE}`,
  "g",
);

function buildBodyWithSignature(
  current: string,
  _previousSigId: string,
  next: EmailSignature | null,
): string {
  // Drop any signature block we ourselves inserted earlier. We
  // intentionally don't try to detect raw signatures the operator
  // typed by hand — the markers are how we discriminate.
  const stripped = current.replace(SIG_BLOCK_RE, "").trimEnd();
  if (!next) return stripped;
  const block = `${SIG_OPEN}<p>&mdash;</p>${next.html_content}${SIG_CLOSE}`;
  if (!stripped) return block;
  return `${stripped}<p></p>${block}`;
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
  const [showScheduleDialog, setShowScheduleDialog] = useState(false);
  const [signatures, setSignatures] = useState<EmailSignature[]>([]);
  const [activeSignatureId, setActiveSignatureId] = useState<string>("");
  const [includeUnsubscribe, setIncludeUnsubscribe] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const editorRef = useRef<RichEditorHandle | null>(null);

  // draftKey isolates the autosave entries per conversation. Replies
  // carry the parent gmail message id (one per thread by construction);
  // fresh composes use the contact id, falling back to "new" when
  // composing from /emails. Without enough uniqueness here a draft
  // from contact A's send leaks into contact B's next compose because
  // TinyMCE keys off the same {path}{query} on /contacts/:id.
  const draftKey = replyTo
    ? `reply-${replyTo.messageId}`
    : `compose-${contactId ?? "new"}`;

  // The composer is rendered inline at the bottom of the thread page
  // (the `.modal-backdrop` class isn't an overlay), so on "Responder"
  // it lands below the fold. Pull it into view on mount — a short
  // delay lets the lazily-loaded editor begin laying out first.
  useEffect(() => {
    const handle = window.setTimeout(() => {
      rootRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 100);
    return () => window.clearTimeout(handle);
  }, []);

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

  // Load signatures + auto-insert the default at mount. Guarded with
  // `inserted` so a re-render (the body state mutating) doesn't keep
  // re-inserting the signature on every keystroke.
  useEffect(() => {
    let inserted = false;
    listEmailSignatures()
      .then((rows) => {
        setSignatures(rows);
        const def = rows.find((s) => s.is_default);
        if (def && !inserted) {
          inserted = true;
          setActiveSignatureId(def.id);
          setBodyHtml((prev) => buildBodyWithSignature(prev, "", def));
        }
      })
      .catch(() => {
        /* signatures are optional; never block the modal */
      });
  }, []);

  // Seed the unsubscribe toggle from the operator's stored default.
  // Reads /api/auth/me's `email_include_unsubscribe_default`, which is
  // the same hydrated user the rest of the app uses — no extra round
  // trip on every send.
  useEffect(() => {
    getCurrentUser()
      .then((u) => {
        if (u.email_include_unsubscribe_default) setIncludeUnsubscribe(true);
      })
      .catch(() => {
        /* falls back to the unchecked default */
      });
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

  async function handleSubmit(
    event: React.FormEvent,
    scheduledFor: string | null = null,
  ) {
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
      // Subject-change ⇒ new thread. Gmail's send API also requires
      // the Subject to match for chaining; if the operator typed a
      // different subject we honour that intent by stripping the
      // in_reply_to id so the server doesn't try to thread.
      const normalise = (s: string) =>
        s.replace(/^re:\s*/i, "").trim().toLowerCase();
      const subjectChanged =
        replyTo != null &&
        normalise(subject) !== normalise(replyTo.subject ?? "");
      const replyMessageId = subjectChanged
        ? null
        : (replyTo?.messageId ?? null);

      const message = await sendEmail({
        from_alias: fromAlias,
        to: toList,
        cc: cc.trim() ? splitEmails(cc) : null,
        subject,
        body_html: bodyHtml.trim() || null,
        body_text: null,
        contact_id: contactId ?? null,
        in_reply_to_message_id: replyMessageId,
        include_unsubscribe: includeUnsubscribe,
        scheduled_for: scheduledFor,
      });
      // Wipe the autosave entry for this conversation BEFORE handing
      // control back to the parent so a quick "compose another"
      // doesn't restore the just-sent body. We deliberately do NOT
      // clear on Cancel — the operator may want to come back to the
      // half-written reply later.
      editorRef.current?.clearDraft();
      onSent?.(message);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo enviar el email."));
      setSubmitting(false);
    }
  }

  const hasMerge =
    hasMergeTokens(bodyHtml) || hasMergeTokens(subject);

  return (
    <div
      className="modal-backdrop"
      role="dialog"
      aria-modal="true"
      ref={rootRef}
    >
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
            <label className="email-compose-signature">
              <span className="email-compose-signature-label">
                <PenLine size={12} aria-hidden /> Firma
              </span>
              <select
                value={activeSignatureId}
                onChange={(e) => {
                  const nextId = e.target.value;
                  const next =
                    signatures.find((s) => s.id === nextId) ?? null;
                  setActiveSignatureId(nextId);
                  setBodyHtml((prev) =>
                    buildBodyWithSignature(prev, activeSignatureId, next),
                  );
                }}
                disabled={signatures.length === 0}
                title={
                  signatures.length === 0
                    ? "Crea una firma en /account/firmas"
                    : undefined
                }
              >
                <option value="">Sin firma</option>
                {signatures.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                    {s.is_default ? " (predeterminada)" : ""}
                  </option>
                ))}
              </select>
            </label>
            <label
              className="email-compose-unsubscribe"
              title={
                includeUnsubscribe
                  ? "El email incluirá el enlace y la cabecera List-Unsubscribe."
                  : "Recomendado para mailings / newsletters. Para 1-a-1, déjalo apagado."
              }
            >
              <input
                type="checkbox"
                checked={includeUnsubscribe}
                onChange={(e) => setIncludeUnsubscribe(e.target.checked)}
              />
              <span className="email-compose-unsubscribe-label">
                <Ban size={12} aria-hidden /> Incluir opción de baja
              </span>
            </label>
          </div>

          <label className="field">
            Cuerpo
            <RichEditor
              ref={editorRef}
              value={bodyHtml}
              onChange={setBodyHtml}
              placeholder="Escribe tu email. Usa {nombre}, {empresa}, {email} para personalizar."
              minHeight={460}
              draftKey={draftKey}
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
              type="button"
              className="button secondary"
              onClick={() => setShowScheduleDialog(true)}
              disabled={submitting || !fromAlias}
            >
              <CalendarClock size={11} aria-hidden /> Programar envío
            </button>
            <button
              type="submit"
              className="button"
              disabled={submitting || !fromAlias}
            >
              {submitting ? "Enviando…" : "Enviar ahora"}
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
      <ScheduleSendDialog
        open={showScheduleDialog}
        onClose={() => setShowScheduleDialog(false)}
        onSchedule={(iso) => {
          setShowScheduleDialog(false);
          // Fake a form-submit event to reuse the same validation
          // path; the second arg routes the call through the
          // backend's pending-message branch.
          void handleSubmit(
            { preventDefault: () => undefined } as unknown as React.FormEvent,
            iso,
          );
        }}
      />
    </div>
  );
}
