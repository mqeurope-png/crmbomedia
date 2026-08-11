"use client";

import { ChevronLeft, ChevronRight } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { EmailComposerModal } from "../../components/EmailComposerModal";
import { EmailThreadDetail } from "../../components/email/EmailThreadDetail";
import { EmailThreadToolbar } from "../../components/email/EmailThreadToolbar";
import {
  type EmailFolder,
  type EmailLabel,
  type EmailMessage,
  type EmailThreadDetail as EmailThreadDetailType,
  addThreadLabel,
  archiveThread,
  getEmailThread,
  getMyEmailAliases,
  listEmailFolders,
  listEmailLabels,
  markThreadRead,
  markThreadUnread,
  moveThread,
  removeThreadLabel,
  restoreThread,
  spamThread,
  starThread,
  trashThread,
  unstarThread,
} from "../../lib/emailsApi";
import {
  getMessageEvents,
  type EmailEvent,
} from "../../lib/emailTrackingApi";
import { formatBackendDateTime } from "../../lib/dates";
import { extractErrorMessage } from "../../lib/errors";

type ComposeState =
  | { mode: "reply" | "replyAll"; parent: EmailMessage }
  | { mode: "forward"; parent: EmailMessage }
  | null;

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

/** CRM-BANDEJA — right-pane del hilo. Breadcrumb + toolbar agrupada +
 *  mensajes Gmail-style (EmailThreadDetail). El sidebar y la lista
 *  siguen montados en `layout.tsx`. */
export default function EmailThreadPage() {
  const params = useParams<{ thread_id: string }>();
  const searchParams = useSearchParams();
  const router = useRouter();
  const [thread, setThread] = useState<EmailThreadDetailType | null>(null);
  const [folders, setFolders] = useState<EmailFolder[]>([]);
  const [labels, setLabels] = useState<EmailLabel[]>([]);
  const [ownEmails, setOwnEmails] = useState<Set<string>>(new Set());
  const [eventsByMessage, setEventsByMessage] = useState<
    Record<string, EmailEvent[]>
  >({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [compose, setCompose] = useState<ComposeState>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await getEmailThread(params.thread_id);
      setThread(data);
      if (data.has_unread_replies) {
        await markThreadRead(data.id).catch(() => undefined);
      }
      const outboundIds = data.messages
        .filter((m) => m.direction === "outbound")
        .map((m) => m.id);
      const settled = await Promise.allSettled(
        outboundIds.map((id) => getMessageEvents(id)),
      );
      const next: Record<string, EmailEvent[]> = {};
      settled.forEach((res, idx) => {
        const id = outboundIds[idx];
        next[id] = res.status === "fulfilled" ? res.value.events : [];
      });
      setEventsByMessage(next);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo cargar el hilo."));
    } finally {
      setLoading(false);
    }
  }, [params.thread_id]);

  useEffect(() => {
    void load();
  }, [load]);

  // Folders + labels para los pickers del toolbar, y los alias propios
  // para el «para mí» de los headers + el filtrado de Responder a todos.
  useEffect(() => {
    listEmailFolders().then(setFolders).catch(() => setFolders([]));
    listEmailLabels().then(setLabels).catch(() => setLabels([]));
    getMyEmailAliases()
      .then((aliases) =>
        setOwnEmails(
          new Set(aliases.map((a) => a.send_as_email.toLowerCase())),
        ),
      )
      .catch(() => setOwnEmails(new Set()));
  }, []);

  const lastInbound = useMemo(() => {
    const msgs = thread?.messages ?? [];
    return [...msgs].reverse().find((m) => m.direction === "inbound") ?? null;
  }, [thread?.messages]);

  const appliedLabelIds = useMemo(
    () => new Set((thread?.labels ?? []).map((l) => l.id)),
    [thread?.labels],
  );

  const runMutation = useCallback(
    async (fn: () => Promise<unknown>) => {
      try {
        await fn();
        await load();
      } catch (err) {
        setError(extractErrorMessage(err, "No se pudo aplicar la acción."));
      }
    },
    [load],
  );

  if (loading) return <p className="muted">Cargando…</p>;
  if (error || !thread) return <p className="form-error">{error}</p>;

  const last = thread.messages[thread.messages.length - 1];
  const replyParent = lastInbound ?? last;
  const replyTarget =
    thread.reply_to_suggestion ??
    lastInbound?.from_email ??
    thread.messages[0]?.to_emails?.[0] ??
    null;

  // «Responder a todos»: el resto de participantes del mensaje de
  // referencia van en Cc — quitando al destinatario principal y los
  // alias del propio operador.
  const replyAllCc = (parent: EmailMessage): string[] => {
    const seen = new Set<string>();
    const exclude = new Set<string>(ownEmails);
    if (replyTarget) exclude.add(replyTarget.toLowerCase());
    const candidates = [
      parent.from_email,
      ...parent.to_emails,
      ...(parent.cc_emails ?? []),
    ];
    const cc: string[] = [];
    for (const addr of candidates) {
      const key = (addr ?? "").toLowerCase().trim();
      if (!key || seen.has(key) || exclude.has(key)) continue;
      seen.add(key);
      cc.push(addr);
    }
    return cc;
  };

  const forwardBody = (parent: EmailMessage): string => {
    const headerLines = [
      "---------- Mensaje reenviado ----------",
      `De: ${escapeHtml(
        parent.from_name
          ? `${parent.from_name} <${parent.from_email}>`
          : parent.from_email,
      )}`,
      `Fecha: ${escapeHtml(formatBackendDateTime(parent.sent_at))}`,
      `Asunto: ${escapeHtml(parent.subject ?? thread.subject ?? "")}`,
      `Para: ${escapeHtml(parent.to_emails.join(", "))}`,
    ].join("<br>");
    const original =
      parent.body_html ??
      `<pre>${escapeHtml(parent.body_text ?? parent.snippet ?? "")}</pre>`;
    return `<p></p><p>${headerLines}</p>${original}`;
  };

  const onArchiveOrRestore = () =>
    thread.state === "inbox"
      ? runMutation(() => archiveThread(thread.id))
      : runMutation(() => restoreThread(thread.id));

  // Breadcrumb: por defecto «← Bandeja › [carpeta] › subject». Cuando el
  // hilo se abrió desde la ficha del contacto (`?from=ficha`) el ancla
  // vuelve a la ficha en lugar de a la bandeja.
  const fromFicha =
    searchParams.get("from") === "ficha" && thread.contact_id;
  const folderName =
    folders.find((f) => f.id === thread.folder_id)?.name ?? null;

  return (
    <div className="email-thread-view">
      {/* Botón mobile (CSS lo oculta en ≥768px). */}
      <Link href="/emails" className="email-mobile-back">
        <ChevronLeft size={16} aria-hidden /> Lista de hilos
      </Link>

      <nav className="email-breadcrumb" aria-label="Ruta">
        {fromFicha ? (
          <>
            <Link
              href={`/contacts/${thread.contact_id}`}
              className="email-breadcrumb-link"
            >
              <ChevronLeft size={13} aria-hidden /> Ficha
            </Link>
            <ChevronRight size={12} aria-hidden className="muted" />
            <span className="muted small">Historial</span>
          </>
        ) : (
          <>
            <Link href="/emails" className="email-breadcrumb-link">
              <ChevronLeft size={13} aria-hidden /> Bandeja
            </Link>
            {folderName ? (
              <>
                <ChevronRight size={12} aria-hidden className="muted" />
                <span className="muted small">{folderName}</span>
              </>
            ) : null}
          </>
        )}
        <ChevronRight size={12} aria-hidden className="muted" />
        <span className="email-breadcrumb-current">
          {thread.subject || "(sin asunto)"}
        </span>
      </nav>

      <header className="email-thread-actions">
        <div className="email-thread-actions-title">
          <h2>{thread.subject || "(sin asunto)"}</h2>
          <p className="muted small">
            {thread.messages.length} mensaje
            {thread.messages.length === 1 ? "" : "s"} · Participantes:{" "}
            {thread.participants.join(", ")}
            {thread.contact_id ? (
              <>
                {" · "}
                <Link href={`/contacts/${thread.contact_id}`}>
                  ver ficha
                </Link>
              </>
            ) : null}
          </p>
          {(thread.labels ?? []).length > 0 ? (
            <div className="email-thread-labels">
              {(thread.labels ?? []).map((label) => (
                <span
                  key={label.id}
                  className="email-list-label-chip"
                  style={{
                    backgroundColor: (label.color ?? "#e5e7eb") + "33",
                    color: label.color ?? "#1d2940",
                    borderColor: label.color ?? "#e5e7eb",
                  }}
                >
                  {label.name}
                </span>
              ))}
            </div>
          ) : null}
        </div>
      </header>

      <EmailThreadToolbar
        thread={thread}
        folders={folders}
        // Solo las personales — las etiquetas org (labels de Gmail) se
        // aplican a nivel de MENSAJE desde el detail, no al hilo.
        labels={labels.filter((l) => !l.gmail_label_id)}
        appliedLabelIds={appliedLabelIds}
        onStarToggle={() =>
          runMutation(() =>
            thread.is_starred
              ? unstarThread(thread.id)
              : starThread(thread.id),
          )
        }
        onArchiveOrRestore={onArchiveOrRestore}
        onTrash={() =>
          runMutation(async () => {
            await trashThread(thread.id);
            router.push("/emails");
          })
        }
        onMarkUnread={() =>
          runMutation(async () => {
            // Abrir el hilo lo re-marcaría como leído, así que tras
            // marcarlo volvemos a la bandeja (mismo patrón que Gmail).
            await markThreadUnread(thread.id);
            router.push("/emails");
          })
        }
        onSpam={() =>
          runMutation(async () => {
            await spamThread(thread.id);
            router.push("/emails");
          })
        }
        onMove={(folderId) =>
          runMutation(() => moveThread(thread.id, folderId))
        }
        onToggleLabel={(labelId, applied) =>
          runMutation(() =>
            applied
              ? removeThreadLabel(thread.id, labelId)
              : addThreadLabel(thread.id, labelId),
          )
        }
        onReply={() => setCompose({ mode: "reply", parent: replyParent })}
        onReplyAll={() =>
          setCompose({ mode: "replyAll", parent: replyParent })
        }
        onForward={() => setCompose({ mode: "forward", parent: last })}
      />

      <EmailThreadDetail
        thread={thread}
        eventsByMessage={eventsByMessage}
        ownEmails={ownEmails}
        gmailLabels={labels.filter((l) => Boolean(l.gmail_label_id))}
        onLabelsChanged={() => void load()}
      />

      {compose ? (
        compose.mode === "forward" ? (
          <EmailComposerModal
            contactId={thread.contact_id}
            forwardOf={{
              subject: thread.subject,
              bodyHtml: forwardBody(compose.parent),
            }}
            onClose={() => setCompose(null)}
            onSent={async () => {
              setCompose(null);
              await load();
            }}
          />
        ) : (
          <EmailComposerModal
            contactId={thread.contact_id}
            contactEmail={replyTarget}
            initialCc={
              compose.mode === "replyAll"
                ? replyAllCc(compose.parent)
                : null
            }
            replyTo={{
              messageId: compose.parent.id,
              subject: thread.subject,
            }}
            onClose={() => setCompose(null)}
            onSent={async () => {
              setCompose(null);
              await load();
            }}
          />
        )
      ) : null}
    </div>
  );
}
