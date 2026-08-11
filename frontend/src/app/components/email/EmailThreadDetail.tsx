"use client";

import {
  ChevronDown,
  ChevronRight,
  Download,
  File as FileIcon,
  FileArchive,
  FileSpreadsheet,
  FileText,
  Image as ImageIcon,
  Plus,
  Tag,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  type EmailLabel,
  type EmailMessage,
  type EmailMessageAttachment,
  type EmailThreadDetail as EmailThreadDetailType,
  addMessageLabel,
  downloadEmailAttachment,
  removeMessageLabel,
} from "../../lib/emailsApi";
import {
  type EmailEvent,
} from "../../lib/emailTrackingApi";
import { formatBackendDateTime, formatRelative } from "../../lib/dates";
import { stripTrackingPixel } from "../../lib/emailPreview";
import { EmailEventBadges } from "./EmailEventBadges";

/** CRM-BANDEJA — thread detail estilo Gmail.
 *
 *  Cada mensaje es una card apilada verticalmente. Solo el ÚLTIMO
 *  mensaje arranca expandido; los anteriores quedan plegados como fila
 *  fina con snippet (click en el header → toggle individual). El body
 *  expandido se muestra a altura natural — sin scroll interno: el
 *  iframe sandboxed se auto-redimensiona a su contenido y es el panel
 *  derecho de /emails el que scrollea.
 */

type Props = {
  thread: EmailThreadDetailType;
  eventsByMessage: Record<string, EmailEvent[]>;
  /** Alias del operador (lowercase) para renderizar «para mí». */
  ownEmails?: Set<string>;
  /** CRM-ETIQUETAS-GMAIL — etiquetas org (labels de Gmail) disponibles
   *  para el dropdown «+» de cada mensaje. Sin ellas los chips se
   *  renderizan igual pero no se puede añadir. */
  gmailLabels?: EmailLabel[];
  onLabelsChanged?: () => void;
};

export function EmailThreadDetail({
  thread,
  eventsByMessage,
  ownEmails,
  gmailLabels,
  onLabelsChanged,
}: Props) {
  const messages = thread.messages;
  const lastId = messages[messages.length - 1]?.id ?? null;
  const [expanded, setExpanded] = useState<Set<string>>(
    () => new Set(lastId ? [lastId] : []),
  );

  // Al navegar a otro hilo, resetea al estado Gmail: solo el último
  // mensaje expandido.
  useEffect(() => {
    setExpanded(new Set(lastId ? [lastId] : []));
  }, [thread.id, lastId]);

  const allExpanded = messages.every((m) => expanded.has(m.id));

  const toggleAll = () => {
    setExpanded(
      allExpanded
        ? new Set()
        : new Set(messages.map((m) => m.id)),
    );
  };

  const toggleOne = (id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  return (
    <div className="email-thread-detail">
      {messages.length > 1 ? (
        <div className="email-thread-expand-row">
          <button
            type="button"
            className="email-filter-chip"
            onClick={toggleAll}
          >
            {allExpanded ? "Colapsar todo" : "Expandir todo"}
          </button>
        </div>
      ) : null}
      <ul className="email-thread-messages">
        {messages.map((m) => (
          <MessageCard
            key={m.id}
            message={m}
            expanded={expanded.has(m.id)}
            onToggle={() => toggleOne(m.id)}
            events={eventsByMessage[m.id] ?? []}
            ownEmails={ownEmails}
            gmailLabels={gmailLabels}
            onLabelsChanged={onLabelsChanged}
          />
        ))}
      </ul>
    </div>
  );
}

/** «para mí» / «para X, Y» / «para X +3 más» — resumen Gmail-style. */
export function formatToSummary(
  toEmails: string[],
  ownEmails?: Set<string>,
): string {
  if (!toEmails.length) return "para —";
  const names = toEmails.map((addr) => {
    if (ownEmails?.has(addr.toLowerCase())) return "mí";
    const local = addr.split("@")[0] ?? addr;
    return local;
  });
  if (names.length <= 2) return `para ${names.join(", ")}`;
  return `para ${names.slice(0, 2).join(", ")} +${names.length - 2} más`;
}

function initialOf(message: EmailMessage): string {
  const source = message.from_name || message.from_email || "?";
  return source.trim().charAt(0).toUpperCase() || "?";
}

function MessageCard({
  message: m,
  expanded,
  onToggle,
  events,
  ownEmails,
  gmailLabels,
  onLabelsChanged,
}: {
  message: EmailMessage;
  expanded: boolean;
  onToggle: () => void;
  events: EmailEvent[];
  ownEmails?: Set<string>;
  gmailLabels?: EmailLabel[];
  onLabelsChanged?: () => void;
}) {
  const [detailsOpen, setDetailsOpen] = useState(false);

  const stateChip =
    m.scheduled_status === "pending" ? (
      <span className="badge warn">
        📅 Programado para {formatBackendDateTime(m.scheduled_for ?? null)}
      </span>
    ) : m.direction === "outbound" ? (
      <span className="badge ok">🟢 Enviado desde CRM</span>
    ) : (
      <span className="badge info">📧 Respuesta entrante</span>
    );

  return (
    <li
      className={`email-message email-message-${m.direction}${
        expanded ? " is-expanded" : " is-collapsed"
      }${m.is_spam ? " is-spam" : ""}`}
      data-testid={`email-message-${m.id}`}
      data-expanded={expanded}
    >
      {/* Header SIEMPRE visible — click = toggle expand/collapse. */}
      <button
        type="button"
        className="email-message-headerbtn"
        onClick={onToggle}
        aria-expanded={expanded}
        aria-label={
          expanded ? "Colapsar mensaje" : "Expandir mensaje"
        }
      >
        <span className="email-message-avatar" aria-hidden>
          {initialOf(m)}
        </span>
        <span className="email-message-headmain">
          <span className="email-message-headline">
            <strong className="email-message-fromname">
              {m.from_name || m.from_email}
            </strong>
            {m.from_name ? (
              <span className="muted small email-message-fromaddr">
                &lt;{m.from_email}&gt;
              </span>
            ) : null}
            {stateChip}
            {m.is_spam ? <span className="badge bad">Spam</span> : null}
          </span>
          {expanded ? (
            <span className="muted small email-message-tosummary">
              {formatToSummary(m.to_emails, ownEmails)}
            </span>
          ) : (
            <span className="muted small email-message-snippetline">
              {m.snippet || m.body_text?.slice(0, 140) || ""}
            </span>
          )}
        </span>
        <span
          className="muted small email-message-date"
          title={formatBackendDateTime(m.sent_at)}
        >
          {formatRelative(m.sent_at)}
        </span>
      </button>

      {expanded ? (
        <div className="email-message-body-wrap">
          {m.gmail_status === "deleted_gmail" ? (
            <p
              className="email-deleted-gmail-banner"
              data-testid="deleted-gmail-banner"
            >
              ⚠ Este mensaje ya no existe en Gmail. Los adjuntos no se
              pueden descargar.
            </p>
          ) : null}
          <MessageLabels
            message={m}
            available={gmailLabels}
            onChanged={onLabelsChanged}
          />
          <button
            type="button"
            className="email-message-details-toggle"
            onClick={() => setDetailsOpen((v) => !v)}
            aria-expanded={detailsOpen}
          >
            {detailsOpen ? (
              <ChevronDown size={12} aria-hidden />
            ) : (
              <ChevronRight size={12} aria-hidden />
            )}{" "}
            Detalles
          </button>
          {detailsOpen ? (
            <dl className="email-message-details">
              <dt>De</dt>
              <dd>
                {m.from_name ? `${m.from_name} ` : ""}
                &lt;{m.from_email}&gt;
              </dd>
              <dt>Para</dt>
              <dd>{m.to_emails.join(", ") || "—"}</dd>
              {m.cc_emails && m.cc_emails.length > 0 ? (
                <>
                  <dt>Cc</dt>
                  <dd>{m.cc_emails.join(", ")}</dd>
                </>
              ) : null}
              <dt>Fecha</dt>
              <dd>{formatBackendDateTime(m.sent_at)}</dd>
              {m.delivered_to ? (
                <>
                  <dt>Entregado a</dt>
                  <dd>{m.delivered_to}</dd>
                </>
              ) : null}
              {m.subject ? (
                <>
                  <dt>Asunto</dt>
                  <dd>{m.subject}</dd>
                </>
              ) : null}
            </dl>
          ) : null}

          {m.direction === "outbound" && events.length > 0 ? (
            <EmailEventBadges events={events} />
          ) : null}

          {m.body_html ? (
            <AutoHeightHtmlBody html={m.body_html} messageId={m.id} />
          ) : (
            <pre className="email-body-text email-body-natural">
              {m.body_text || m.snippet || ""}
            </pre>
          )}

          {(m.attachments ?? []).length > 0 ? (
            <AttachmentCards
              messageId={m.id}
              attachments={m.attachments ?? []}
              gmailDeleted={m.gmail_status === "deleted_gmail"}
            />
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

/** Iframe sandboxed que crece hasta la altura natural del contenido.
 *
 *  Seguridad: `sandbox="allow-same-origin"` SIN `allow-scripts` — el
 *  HTML del email no puede ejecutar JS jamás, pero el padre sí puede
 *  medir `scrollHeight` para eliminar el scroll interno (la queja #1
 *  de Bart). Se re-mide cuando cargan las imágenes del cuerpo. */
function AutoHeightHtmlBody({
  html,
  messageId,
}: {
  html: string;
  messageId: string;
}) {
  const ref = useRef<HTMLIFrameElement>(null);
  const [height, setHeight] = useState(80);

  const clean = useMemo(() => stripTrackingPixel(html) ?? "", [html]);

  const measure = () => {
    const doc = ref.current?.contentDocument;
    if (!doc) return;
    const h = Math.max(
      doc.documentElement?.scrollHeight ?? 0,
      doc.body?.scrollHeight ?? 0,
    );
    if (h > 0) setHeight(h + 16);
    // Las imágenes cargan después del load del doc — re-medimos al
    // completarse cada una.
    doc.querySelectorAll("img").forEach((img) => {
      if (!img.complete) {
        img.addEventListener("load", () => {
          const nh = Math.max(
            doc.documentElement?.scrollHeight ?? 0,
            doc.body?.scrollHeight ?? 0,
          );
          if (nh > 0) setHeight(nh + 16);
        });
      }
    });
  };

  return (
    <iframe
      ref={ref}
      title={`Mensaje ${messageId}`}
      className="email-html-preview email-html-preview-auto"
      sandbox="allow-same-origin"
      srcDoc={clean}
      onLoad={measure}
      style={{ height: `${height}px`, overflow: "hidden" }}
      scrolling="no"
    />
  );
}

/** CRM-ETIQUETAS-GMAIL — chips de etiquetas del MENSAJE (labels de Gmail)
 *  con «×» para quitar y dropdown «+» para añadir. El backend propaga el
 *  cambio a Gmail (messages.modify) antes de persistir; si Gmail falla se
 *  muestra el error y no cambia nada. */
function MessageLabels({
  message: m,
  available,
  onChanged,
}: {
  message: EmailMessage;
  available?: EmailLabel[];
  onChanged?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const applied = m.labels ?? [];
  const appliedIds = new Set(applied.map((l) => l.id));
  // Solo etiquetas org (espejo de Gmail) — las personales van a nivel de
  // hilo en el toolbar. Sin gmail_message_id (envío programado pendiente)
  // no hay nada que etiquetar en Gmail.
  const candidates = (available ?? []).filter(
    (l) => l.gmail_label_id && !appliedIds.has(l.id),
  );
  const canEdit = Boolean(m.gmail_message_id);

  if (applied.length === 0 && !(canEdit && candidates.length > 0)) {
    return null;
  }

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      onChanged?.();
    } catch {
      setError("No se pudo actualizar la etiqueta en Gmail.");
    } finally {
      setBusy(false);
      setOpen(false);
    }
  };

  return (
    <div className="email-message-labels" data-testid="message-labels">
      {applied.map((label) => (
        <span
          key={label.id}
          className="email-list-label-chip email-message-label-chip"
          style={{
            backgroundColor: (label.color ?? "#e5e7eb") + "33",
            color: label.color ?? "#1d2940",
            borderColor: label.color ?? "#e5e7eb",
          }}
        >
          <Tag size={10} aria-hidden />
          {label.name}
          {canEdit ? (
            <button
              type="button"
              className="email-message-label-remove"
              aria-label={`Quitar etiqueta ${label.name}`}
              disabled={busy}
              onClick={() =>
                run(() => removeMessageLabel(m.id, label.id))
              }
            >
              <X size={10} aria-hidden />
            </button>
          ) : null}
        </span>
      ))}
      {canEdit && candidates.length > 0 ? (
        <span className="email-message-label-addwrap">
          <button
            type="button"
            className="email-message-label-add"
            aria-label="Añadir etiqueta"
            title="Añadir etiqueta"
            disabled={busy}
            onClick={() => setOpen((v) => !v)}
          >
            <Plus size={11} aria-hidden />
          </button>
          {open ? (
            <ul className="email-message-label-menu" role="menu">
              {candidates.map((label) => (
                <li key={label.id}>
                  <button
                    type="button"
                    role="menuitem"
                    disabled={busy}
                    onClick={() =>
                      run(() => addMessageLabel(m.id, label.id))
                    }
                  >
                    <Tag
                      size={10}
                      aria-hidden
                      color={label.color ?? "#9ca3af"}
                    />
                    {label.name}
                  </button>
                </li>
              ))}
            </ul>
          ) : null}
        </span>
      ) : null}
      {error ? <span className="form-error small">{error}</span> : null}
    </div>
  );
}

function humanSize(bytes: number | null): string {
  if (!bytes || bytes <= 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** CRM-ADJUNTOS-UX — icono + etiqueta de tipo por extensión/mime. */
function attachmentKind(
  filename: string,
  mime: string | null,
): { Icon: typeof FileIcon; label: string } {
  const ext = filename.split(".").pop()?.toLowerCase() ?? "";
  const m = (mime ?? "").toLowerCase();
  if (m.startsWith("image/") || ["jpg", "jpeg", "png", "gif", "webp", "bmp", "svg"].includes(ext)) {
    return { Icon: ImageIcon, label: ext ? ext.toUpperCase() : "Imagen" };
  }
  if (ext === "pdf" || m === "application/pdf") {
    return { Icon: FileText, label: "PDF" };
  }
  if (["zip", "rar", "7z", "gz", "tar"].includes(ext)) {
    return { Icon: FileArchive, label: ext.toUpperCase() };
  }
  if (["xls", "xlsx", "csv", "ods"].includes(ext)) {
    return { Icon: FileSpreadsheet, label: ext.toUpperCase() };
  }
  if (["doc", "docx", "txt", "rtf", "odt"].includes(ext)) {
    return { Icon: FileText, label: ext.toUpperCase() };
  }
  return { Icon: FileIcon, label: ext ? ext.toUpperCase() : "Archivo" };
}

function AttachmentCards({
  messageId,
  attachments,
  gmailDeleted = false,
}: {
  messageId: string;
  attachments: EmailMessageAttachment[];
  /** CRM-ADJUNTOS-PURGE — el mensaje ya no existe en Gmail: cards en
   *  gris y descarga deshabilitada. */
  gmailDeleted?: boolean;
}) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  return (
    <div className="email-attachments" data-testid="email-attachments">
      {attachments.map((a, idx) => {
        const attachmentId = a.id;
        const { Icon, label } = attachmentKind(a.filename, a.mime_type);
        const size = humanSize(a.size_bytes);
        const meta = [size, label].filter(Boolean).join(" · ");
        return (
          <div
            key={attachmentId ?? `${a.filename}-${idx}`}
            className={`email-attachment-card${gmailDeleted ? " is-unavailable" : ""}`}
          >
            <span className="email-attachment-icon" aria-hidden>
              <Icon size={28} />
            </span>
            <span className="email-attachment-info">
              <span className="email-attachment-name" title={a.filename}>
                {a.filename}
              </span>
              {meta ? (
                <span className="email-attachment-meta">{meta}</span>
              ) : null}
            </span>
            {attachmentId && a.downloadable ? (
              <button
                type="button"
                className="button small email-attachment-dl-btn"
                title={
                  gmailDeleted ? "No disponible" : `Descargar ${a.filename}`
                }
                aria-label={
                  gmailDeleted ? "No disponible" : `Descargar ${a.filename}`
                }
                disabled={gmailDeleted || busy === attachmentId}
                onClick={() => {
                  setBusy(attachmentId);
                  setError(null);
                  downloadEmailAttachment(messageId, attachmentId, a.filename)
                    .catch((err) =>
                      setError(
                        err instanceof Error
                          ? err.message
                          : "No se pudo descargar el adjunto.",
                      ),
                    )
                    .finally(() => setBusy(null));
                }}
              >
                <Download size={14} aria-hidden />{" "}
                {busy === attachmentId ? "Descargando…" : "Descargar"}
              </button>
            ) : null}
          </div>
        );
      })}
      {error ? <p className="form-error small">{error}</p> : null}
    </div>
  );
}
