"use client";

import {
  Ban,
  CalendarClock,
  FolderOpen,
  Paperclip,
  PenLine,
  Save,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { getCurrentUser } from "../lib/api";
import {
  deleteEmailDraftAttachment,
  listEmailDraftAttachments,
  uploadEmailDraftAttachment,
  type EmailDraftAttachment,
} from "../lib/emailDraftAttachmentsApi";
import {
  listEmailSignatures,
  type EmailSignature,
} from "../lib/emailSignaturesApi";
import {
  createEmailDraft,
  deleteEmailDraft,
  getMyEmailAliases,
  sendEmail,
  sendEmailDraft,
  type EmailDraft,
  type EmailDraftWrite,
  type EmailMessage,
  type MyAlias,
  updateEmailDraft,
} from "../lib/emailsApi";
import { extractErrorMessage } from "../lib/errors";
import { SaveTemplateModal } from "./email/SaveTemplateModal";
import { ScheduleSendDialog } from "./email/ScheduleSendDialog";
import {
  TemplatePicker,
  type TemplatePickerSelection,
} from "./email/TemplatePicker";

// Gmail's hard cap is 25 MB total per message (body + attachments +
// headers + MIME overhead). We enforce it client-side as a soft cap
// at 25 MB across the attachment binaries; the backend re-checks at
// upload time so a stale tab can't bypass it.
const MAX_ATTACHMENTS_TOTAL_BYTES = 25 * 1024 * 1024;

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

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
  /** When set, the modal hydrates from the existing draft and
   *  auto-saves under the same id. Send-flow deletes the draft
   *  on success. */
  initialDraft?: EmailDraft | null;
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
  initialDraft,
  onClose,
  onSent,
}: Props) {
  const [aliases, setAliases] = useState<MyAlias[]>([]);
  const [loadingAliases, setLoadingAliases] = useState(true);
  const [fromAlias, setFromAlias] = useState(initialDraft?.from_alias ?? "");
  const [to, setTo] = useState(
    initialDraft?.to_emails?.join(", ") ?? contactEmail ?? "",
  );
  const [cc, setCc] = useState(initialDraft?.cc_emails?.join(", ") ?? "");
  const [subject, setSubject] = useState(
    initialDraft?.subject ??
      (replyTo?.subject
        ? replyTo.subject.toLowerCase().startsWith("re:")
          ? replyTo.subject
          : `Re: ${replyTo.subject}`
        : ""),
  );
  const [bodyHtml, setBodyHtml] = useState(initialDraft?.body_html ?? "");
  const [draftId, setDraftId] = useState<string | null>(
    initialDraft?.id ?? null,
  );
  const [draftSavedAt, setDraftSavedAt] = useState<Date | null>(
    initialDraft ? new Date(initialDraft.updated_at) : null,
  );
  const dirtyRef = useRef(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [showPicker, setShowPicker] = useState(false);
  const [showSaveModal, setShowSaveModal] = useState(false);
  const [showScheduleDialog, setShowScheduleDialog] = useState(false);
  const [signatures, setSignatures] = useState<EmailSignature[]>([]);
  const [activeSignatureId, setActiveSignatureId] = useState<string>(
    initialDraft?.signature_id ?? "",
  );
  const [includeUnsubscribe, setIncludeUnsubscribe] = useState(
    initialDraft?.include_unsubscribe ?? false,
  );
  const [attachments, setAttachments] = useState<EmailDraftAttachment[]>([]);
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const editorRef = useRef<RichEditorHandle | null>(null);
  const draftIdRef = useRef<string | null>(initialDraft?.id ?? null);
  draftIdRef.current = draftId;

  // Auto-save every 5s when the operator has changed something.
  // The dirty flag is a ref so typing doesn't re-render the
  // effect dependencies; we read the latest values inside the
  // timer.
  useEffect(() => {
    const timer = window.setInterval(async () => {
      if (!dirtyRef.current) return;
      const payload = {
        thread_id: null,
        contact_id: contactId ?? null,
        from_alias: fromAlias || null,
        subject: subject || null,
        body_html: bodyHtml || null,
        to_emails: splitEmails(to),
        cc_emails: cc.trim() ? splitEmails(cc) : null,
        in_reply_to_message_id: replyTo?.messageId ?? null,
        signature_id: activeSignatureId || null,
        include_unsubscribe: includeUnsubscribe,
      };
      try {
        if (draftId) {
          const fresh = await updateEmailDraft(draftId, payload);
          setDraftSavedAt(new Date(fresh.updated_at));
        } else {
          const fresh = await createEmailDraft(payload);
          setDraftId(fresh.id);
          setDraftSavedAt(new Date(fresh.updated_at));
        }
        dirtyRef.current = false;
      } catch {
        /* keep dirty; retry on next tick */
      }
    }, 5000);
    return () => window.clearInterval(timer);
  }, [
    contactId,
    fromAlias,
    subject,
    bodyHtml,
    to,
    cc,
    replyTo?.messageId,
    activeSignatureId,
    includeUnsubscribe,
    draftId,
  ]);

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

  // PR-Fix-Modal-Nuevo-Email-Layout. Escape cierra como X. Lo
  // registramos en document para capturarlo aunque el foco esté en
  // un input / botón del composer. TinyMCE no consume Escape por
  // defecto, así que llega hasta este listener.
  useEffect(() => {
    function handleKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.stopPropagation();
        onClose();
      }
    }
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [onClose]);

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

  // Sprint Email v2.5 — A. Pull existing attachments for a hydrated
  // draft. New composes start with an empty list; the row gets
  // created the first time the operator drops a file.
  useEffect(() => {
    if (!initialDraft?.id) return;
    listEmailDraftAttachments(initialDraft.id)
      .then(setAttachments)
      .catch(() => setAttachments([]));
  }, [initialDraft?.id]);

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

  // The draft row is needed before we can attach a binary (the
  // attachment FK points at email_drafts.id). Auto-save normally
  // creates it on the 5 s tick; uploading a file forces it now so
  // the operator doesn't lose the binary while typing.
  async function ensureDraft(): Promise<string> {
    if (draftIdRef.current) return draftIdRef.current;
    const payload: EmailDraftWrite = {
      thread_id: null,
      contact_id: contactId ?? null,
      from_alias: fromAlias || null,
      subject: subject || null,
      body_html: bodyHtml || null,
      to_emails: splitEmails(to),
      cc_emails: cc.trim() ? splitEmails(cc) : null,
      in_reply_to_message_id: replyTo?.messageId ?? null,
      signature_id: activeSignatureId || null,
      include_unsubscribe: includeUnsubscribe,
    };
    const created = await createEmailDraft(payload);
    setDraftId(created.id);
    setDraftSavedAt(new Date(created.updated_at));
    draftIdRef.current = created.id;
    dirtyRef.current = false;
    return created.id;
  }

  async function handleFilesPicked(files: FileList | File[]) {
    const list = Array.from(files);
    if (list.length === 0) return;
    const currentTotal = attachments.reduce(
      (sum, a) => sum + a.size_bytes,
      0,
    );
    const incomingTotal = list.reduce((sum, f) => sum + f.size, 0);
    if (currentTotal + incomingTotal > MAX_ATTACHMENTS_TOTAL_BYTES) {
      setError(
        `Gmail no acepta más de 25 MB de adjuntos por mensaje. Llevas ${formatBytes(
          currentTotal,
        )} y estás añadiendo ${formatBytes(incomingTotal)}.`,
      );
      return;
    }
    setError(null);
    setUploading(true);
    try {
      const id = await ensureDraft();
      for (const file of list) {
        const created = await uploadEmailDraftAttachment(id, file);
        setAttachments((prev) => [...prev, created]);
      }
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo subir el adjunto."));
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }

  async function handleRemoveAttachment(attachmentId: string) {
    if (!draftId) return;
    try {
      await deleteEmailDraftAttachment(draftId, attachmentId);
      setAttachments((prev) => prev.filter((a) => a.id !== attachmentId));
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo borrar el adjunto."));
    }
  }

  function applyTemplate(selection: TemplatePickerSelection) {
    // QoL: si el operador ya tiene contenido en el editor, pedimos
    // confirmación antes de sobreescribirlo. Comparamos contra el
    // signature de "editor recién inicializado" (TinyMCE emite `<p></p>`
    // o cadena vacía) para no preguntar si solo hay placeholder.
    const stripped = (bodyHtml || "")
      .replace(/<p>(?:&nbsp;|\s)*<\/p>/g, "")
      .trim();
    if (
      stripped.length > 0 &&
      !window.confirm(
        "Ya hay contenido en el editor. ¿Reemplazar con la plantilla seleccionada?",
      )
    ) {
      return;
    }
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

      let message: EmailMessage;
      // Sprint Email v2.5 — A. Cuando hay adjuntos, el binario vive
      // en email_draft_attachments y solo lo materializa el send
      // route a través de `send_draft`. Por eso forzamos primero un
      // flush del estado actual al draft (PUT) y luego llamamos al
      // endpoint que lee los attachments + send. Sin adjuntos
      // seguimos por el atajo `sendEmail` para no introducir latency
      // al flujo histórico.
      if (attachments.length > 0) {
        const id = await ensureDraft();
        const payload: EmailDraftWrite = {
          thread_id: null,
          contact_id: contactId ?? null,
          from_alias: fromAlias,
          subject,
          body_html: bodyHtml.trim() || null,
          body_text: null,
          to_emails: toList,
          cc_emails: cc.trim() ? splitEmails(cc) : null,
          in_reply_to_message_id: replyMessageId,
          signature_id: activeSignatureId || null,
          include_unsubscribe: includeUnsubscribe,
          scheduled_for: scheduledFor,
        };
        await updateEmailDraft(id, payload);
        message = await sendEmailDraft(id);
        // sendEmailDraft borra la row en éxito → no llamar deleteEmailDraft.
        setDraftId(null);
        draftIdRef.current = null;
      } else {
        message = await sendEmail({
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
        // v2.4d — the draft row only mirrored the in-flight compose;
        // once the message is actually sent (or scheduled), the
        // operator doesn't expect to see it under Borradores anymore.
        if (draftId) {
          await deleteEmailDraft(draftId).catch(() => undefined);
          setDraftId(null);
          draftIdRef.current = null;
        }
      }
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

  async function handleDiscard() {
    if (!confirm("¿Descartar el borrador? Se perderá el contenido.")) return;
    if (draftId) {
      await deleteEmailDraft(draftId).catch(() => undefined);
    }
    editorRef.current?.clearDraft();
    onClose();
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
        <header className="composer-header">
          <h2>{replyTo ? "Responder" : "Nuevo email"}</h2>
          <ComposerDraftStatus
            savedAt={draftSavedAt}
            hasDraft={draftId !== null}
          />
          {/* PR-Fix-Modal-Nuevo-Email-Layout. Botón X visible para
              cerrar el panel — antes solo se podía cancelar con el
              botón "Cancelar" a pie del form. */}
          <button
            type="button"
            className="composer-header-close"
            aria-label="Cerrar"
            onClick={onClose}
          >
            <X size={18} aria-hidden />
          </button>
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
                {aliases[0].resolved_display_name
                  ? `${aliases[0].resolved_display_name} <${aliases[0].send_as_email}>`
                  : aliases[0].send_as_email}
              </strong>
            </p>
          ) : (
            <label className="field">
              De
              <select
                value={fromAlias}
                onChange={(e) => {
                  setFromAlias(e.target.value);
                  dirtyRef.current = true;
                }}
                disabled={loadingAliases || aliases.length === 0}
              >
                {aliases.map((a) => (
                  <option key={a.send_as_email} value={a.send_as_email}>
                    {a.resolved_display_name
                      ? `${a.resolved_display_name} <${a.send_as_email}>`
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
              onChange={(e) => {
                setTo(e.target.value);
                dirtyRef.current = true;
              }}
              placeholder="email@dominio.com, otro@dominio.com"
            />
          </label>
          <label className="field">
            Cc
            <input
              type="text"
              value={cc}
              onChange={(e) => {
                setCc(e.target.value);
                dirtyRef.current = true;
              }}
              placeholder="opcional"
            />
          </label>
          <label className="field">
            Asunto
            <input
              type="text"
              value={subject}
              onChange={(e) => {
                setSubject(e.target.value);
                dirtyRef.current = true;
              }}
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
            <button
              type="button"
              className="button secondary small"
              onClick={() => fileInputRef.current?.click()}
              disabled={uploading}
              title="Adjuntar archivo (máx. 25 MB en total)"
            >
              <Paperclip size={12} aria-hidden />{" "}
              {uploading ? "Subiendo…" : "Adjuntar"}
            </button>
            <input
              ref={fileInputRef}
              type="file"
              multiple
              hidden
              onChange={(e) => {
                if (e.target.files) void handleFilesPicked(e.target.files);
              }}
            />
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

          <div
            className={`field email-compose-body${dragging ? " dragging" : ""}`}
            onDragOver={(e) => {
              e.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragging(false);
              if (e.dataTransfer.files.length > 0) {
                void handleFilesPicked(e.dataTransfer.files);
              }
            }}
          >
            <label>
              Cuerpo
              <RichEditor
                ref={editorRef}
                value={bodyHtml}
                onChange={(html) => {
                  setBodyHtml(html);
                  dirtyRef.current = true;
                }}
                placeholder="Escribe tu email. Usa {nombre}, {empresa}, {email} para personalizar."
                minHeight={460}
                draftKey={draftKey}
              />
            </label>
            {dragging ? (
              <div className="email-compose-dropzone" aria-hidden>
                Suelta para adjuntar
              </div>
            ) : null}
          </div>

          {attachments.length > 0 ? (
            <div className="email-compose-attachments">
              <p className="muted small">
                Adjuntos ({attachments.length}) ·{" "}
                {formatBytes(
                  attachments.reduce((s, a) => s + a.size_bytes, 0),
                )}{" "}
                de 25 MB
              </p>
              <ul className="email-compose-attachments-list">
                {attachments.map((a) => (
                  <li key={a.id}>
                    <Paperclip size={11} aria-hidden />
                    <span className="email-compose-attachment-name">
                      {a.filename}
                    </span>
                    <span className="muted small">
                      {formatBytes(a.size_bytes)}
                    </span>
                    <button
                      type="button"
                      className="icon-button"
                      onClick={() => handleRemoveAttachment(a.id)}
                      title="Quitar adjunto"
                      aria-label={`Quitar ${a.filename}`}
                    >
                      <X size={12} aria-hidden />
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

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
            {draftId ? (
              <button
                type="button"
                className="button secondary"
                onClick={handleDiscard}
                disabled={submitting}
              >
                <Trash2 size={11} aria-hidden /> Descartar borrador
              </button>
            ) : null}
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

/** "Guardado hace Xs" indicator in the composer header. Re-renders
 *  every 15 s so the relative timestamp stays current without the
 *  parent paying a full composer re-render. */
function ComposerDraftStatus({
  savedAt,
  hasDraft,
}: {
  savedAt: Date | null;
  hasDraft: boolean;
}) {
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!savedAt) return;
    const timer = window.setInterval(() => setTick((n) => n + 1), 15000);
    return () => window.clearInterval(timer);
  }, [savedAt]);

  if (!hasDraft || !savedAt) return null;
  const secs = Math.max(0, Math.floor((Date.now() - savedAt.getTime()) / 1000));
  let label: string;
  if (secs < 10) label = "Guardado";
  else if (secs < 60) label = `Guardado hace ${secs}s`;
  else if (secs < 3600) {
    label = `Guardado hace ${Math.floor(secs / 60)} min`;
  } else {
    label = `Guardado hace ${Math.floor(secs / 3600)} h`;
  }
  return <span className="composer-draft-status">{label}</span>;
}
