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
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  type EmailMessage,
  type EmailMessageAttachment,
  type EmailThreadDetail as EmailThreadDetailType,
  downloadEmailAttachment,
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
};

export function EmailThreadDetail({
  thread,
  eventsByMessage,
  ownEmails,
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
}: {
  message: EmailMessage;
  expanded: boolean;
  onToggle: () => void;
  events: EmailEvent[];
  ownEmails?: Set<string>;
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
