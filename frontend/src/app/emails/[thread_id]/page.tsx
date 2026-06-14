"use client";

import {
  Archive,
  ArrowDownLeft,
  ArrowUpRight,
  Clock,
  Folder as FolderIcon,
  MailWarning,
  Reply,
  Star,
  Tag,
  Trash2,
  Undo2,
} from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { EmailComposerModal } from "../../components/EmailComposerModal";
import { EmailEventBadges } from "../../components/email/EmailEventBadges";
import {
  type EmailFolder,
  type EmailLabel,
  type EmailMessage,
  type EmailThreadDetail,
  addThreadLabel,
  archiveThread,
  getEmailThread,
  listEmailFolders,
  listEmailLabels,
  markThreadRead,
  moveThread,
  removeThreadLabel,
  restoreThread,
  spamThread,
  starThread,
  trashThread,
  unstarThread,
} from "../../lib/emailsApi";
import {
  snoozeThread,
} from "../../lib/emailsApi";
import {
  getMessageEvents,
  type EmailEvent,
} from "../../lib/emailTrackingApi";
import { extractErrorMessage } from "../../lib/errors";
import { useEmailKeyboardShortcuts } from "../../lib/useEmailKeyboardShortcuts";

function formatDateTime(value: string): string {
  return new Date(value).toLocaleString("es-ES", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** Right-pane thread view. The sidebar + list stay mounted in
 *  `layout.tsx`; this component fills the remaining column. */
export default function EmailThreadPage() {
  const params = useParams<{ thread_id: string }>();
  const router = useRouter();
  const [thread, setThread] = useState<EmailThreadDetail | null>(null);
  const [folders, setFolders] = useState<EmailFolder[]>([]);
  const [labels, setLabels] = useState<EmailLabel[]>([]);
  const [eventsByMessage, setEventsByMessage] = useState<
    Record<string, EmailEvent[]>
  >({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [replyTo, setReplyTo] = useState<EmailMessage | null>(null);
  const [moveOpen, setMoveOpen] = useState(false);
  const [labelOpen, setLabelOpen] = useState(false);
  const [snoozeOpen, setSnoozeOpen] = useState(false);
  const moveRef = useRef<HTMLDivElement>(null);
  const labelRef = useRef<HTMLDivElement>(null);
  const snoozeRef = useRef<HTMLDivElement>(null);

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

  // Fetch folders + labels for the inline pickers. The layout has
  // its own copy but the right pane lives below `children`, so it
  // can't reach into the layout's state without a context. A
  // dedicated fetch keeps the boundary clean for v2.4b.
  useEffect(() => {
    listEmailFolders().then(setFolders).catch(() => setFolders([]));
    listEmailLabels().then(setLabels).catch(() => setLabels([]));
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

  // Thread-context shortcuts. Hooks must be called unconditionally —
  // the `enabled` flag inside the hook gates whether keys do anything
  // until the thread has finished loading.
  useEmailKeyboardShortcuts(
    {
      onArchive: () => {
        if (thread)
          runMutation(() =>
            thread.state === "inbox"
              ? archiveThread(thread.id)
              : restoreThread(thread.id),
          );
      },
      onTrash: () => {
        if (!thread) return;
        runMutation(async () => {
          await trashThread(thread.id);
          router.push("/emails");
        });
      },
      onStar: () => {
        if (!thread) return;
        runMutation(() =>
          thread.is_starred ? unstarThread(thread.id) : starThread(thread.id),
        );
      },
      onReply: () => {
        if (!thread) return;
        const msgs = thread.messages;
        const tail = msgs[msgs.length - 1];
        const lastIn =
          [...msgs].reverse().find((m) => m.direction === "inbound") ?? null;
        setReplyTo(lastIn ?? tail);
      },
      onLabel: () => setLabelOpen(true),
      onSnooze: () => setSnoozeOpen(true),
    },
    !!thread,
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

  const onArchiveOrRestore = () =>
    thread.state === "inbox"
      ? runMutation(() => archiveThread(thread.id))
      : runMutation(() => restoreThread(thread.id));

  return (
    <div className="email-thread-view">
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

        <div className="email-thread-action-buttons">
          <ActionButton
            icon={Star}
            label={thread.is_starred ? "Quitar estrella" : "Estrella"}
            active={thread.is_starred}
            onClick={() =>
              runMutation(() =>
                thread.is_starred ? unstarThread(thread.id) : starThread(thread.id),
              )
            }
          />
          <ActionButton
            icon={thread.state === "inbox" ? Archive : Undo2}
            label={thread.state === "inbox" ? "Archivar" : "Restaurar"}
            onClick={onArchiveOrRestore}
          />
          {thread.state !== "trashed" ? (
            <ActionButton
              icon={Trash2}
              label="Papelera"
              onClick={() =>
                runMutation(async () => {
                  await trashThread(thread.id);
                  router.push("/emails");
                })
              }
            />
          ) : null}
          {thread.state !== "spam" ? (
            <ActionButton
              icon={MailWarning}
              label="Spam"
              onClick={() =>
                runMutation(async () => {
                  await spamThread(thread.id);
                  router.push("/emails");
                })
              }
            />
          ) : null}

          <div className="email-bulk-dropdown-wrap" ref={moveRef}>
            <ActionButton
              icon={FolderIcon}
              label="Mover"
              onClick={() => {
                setMoveOpen((v) => !v);
                setLabelOpen(false);
              }}
            />
            {moveOpen ? (
              <div className="email-bulk-dropdown">
                <button
                  type="button"
                  className="email-bulk-dropdown-item"
                  onClick={async () => {
                    setMoveOpen(false);
                    await runMutation(() => moveThread(thread.id, null));
                  }}
                >
                  Bandeja (sin carpeta)
                </button>
                {folders.map((f) => (
                  <button
                    key={f.id}
                    type="button"
                    className="email-bulk-dropdown-item"
                    onClick={async () => {
                      setMoveOpen(false);
                      await runMutation(() => moveThread(thread.id, f.id));
                    }}
                  >
                    <FolderIcon
                      size={12}
                      aria-hidden
                      color={f.color ?? "#9ca3af"}
                    />
                    {f.name}
                  </button>
                ))}
              </div>
            ) : null}
          </div>

          <div className="email-bulk-dropdown-wrap" ref={labelRef}>
            <ActionButton
              icon={Tag}
              label="Etiquetar"
              onClick={() => {
                setLabelOpen((v) => !v);
                setMoveOpen(false);
              }}
            />
            {labelOpen ? (
              <div className="email-bulk-dropdown">
                {labels.length === 0 ? (
                  <span className="muted small email-bulk-dropdown-empty">
                    Aún no tienes etiquetas.
                  </span>
                ) : (
                  labels.map((l) => {
                    const applied = appliedLabelIds.has(l.id);
                    return (
                      <button
                        key={l.id}
                        type="button"
                        className={`email-bulk-dropdown-item${applied ? " is-applied" : ""}`}
                        onClick={async () => {
                          setLabelOpen(false);
                          await runMutation(() =>
                            applied
                              ? removeThreadLabel(thread.id, l.id)
                              : addThreadLabel(thread.id, l.id),
                          );
                        }}
                      >
                        <Tag
                          size={12}
                          aria-hidden
                          color={l.color ?? "#9ca3af"}
                          fill={applied ? l.color ?? "#9ca3af" : "transparent"}
                        />
                        {l.name}
                        {applied ? <span className="muted small"> (aplicada)</span> : null}
                      </button>
                    );
                  })
                )}
              </div>
            ) : null}
          </div>

          <div className="email-bulk-dropdown-wrap" ref={snoozeRef}>
            <ActionButton
              icon={Clock}
              label="Posponer"
              onClick={() => {
                setSnoozeOpen((v) => !v);
                setMoveOpen(false);
                setLabelOpen(false);
              }}
            />
            {snoozeOpen ? (
              <SnoozePicker
                onPick={async (iso) => {
                  setSnoozeOpen(false);
                  await runMutation(async () => {
                    await snoozeThread(thread.id, iso);
                    // The thread is hidden from inbox right after
                    // snoozing — bounce back so the operator doesn't
                    // stare at an empty pane.
                    router.push("/emails");
                  });
                }}
                onCancel={() => setSnoozeOpen(false)}
              />
            ) : null}
          </div>

          <button
            type="button"
            className="button small"
            onClick={() => setReplyTo(replyParent)}
          >
            <Reply size={11} aria-hidden /> Responder
          </button>
        </div>
      </header>

      <ul className="email-thread-messages">
        {thread.messages.map((m) => (
          <li
            key={m.id}
            className={`email-message email-message-${m.direction}`}
          >
            <header className="email-message-header">
              <span className="email-message-avatar" aria-hidden>
                {m.direction === "outbound" ? (
                  <ArrowUpRight size={11} />
                ) : (
                  <ArrowDownLeft size={11} />
                )}
              </span>
              <div className="email-message-meta">
                <p className="email-message-from">
                  <strong>{m.from_name || m.from_email}</strong>
                  {m.from_name ? (
                    <span className="muted small"> &lt;{m.from_email}&gt;</span>
                  ) : null}
                  {m.direction === "outbound" ? (
                    <span className="badge ok"> Enviado desde el CRM</span>
                  ) : (
                    <span className="badge muted"> Respuesta</span>
                  )}
                </p>
                <p className="muted small">
                  Para: {m.to_emails.join(", ")}
                  {m.cc_emails && m.cc_emails.length > 0
                    ? ` · Cc: ${m.cc_emails.join(", ")}`
                    : ""}
                  {" · "}
                  {formatDateTime(m.sent_at)}
                </p>
                {m.direction === "outbound" ? (
                  <EmailEventBadges events={eventsByMessage[m.id] ?? []} />
                ) : null}
              </div>
            </header>
            {m.body_html ? (
              <iframe
                title={`Mensaje ${m.id}`}
                className="email-html-preview"
                sandbox=""
                srcDoc={m.body_html}
              />
            ) : (
              <pre className="email-body-text">
                {m.body_text || m.snippet || ""}
              </pre>
            )}
          </li>
        ))}
      </ul>

      {replyTo ? (
        <EmailComposerModal
          contactId={thread.contact_id}
          contactEmail={replyTarget}
          replyTo={{
            messageId: replyTo.id,
            subject: thread.subject,
          }}
          onClose={() => setReplyTo(null)}
          onSent={async () => {
            setReplyTo(null);
            await load();
          }}
        />
      ) : null}
    </div>
  );
}

function ActionButton({
  icon: Icon,
  label,
  active,
  onClick,
}: {
  icon: React.ComponentType<{ size?: number; "aria-hidden"?: boolean }>;
  label: string;
  active?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className={`email-bulk-btn${active ? " is-active" : ""}`}
      onClick={onClick}
      title={label}
    >
      <Icon size={13} aria-hidden />
      <span className="email-bulk-btn-label">{label}</span>
    </button>
  );
}

/** Snooze quick-picker. Presets compute their target on every click
 *  so "mañana" doesn't drift if the picker stays open over midnight.
 *  Custom takes a datetime-local value so the operator can pick any
 *  point — backend validates `> now` and 400s a past date. */
function SnoozePicker({
  onPick,
  onCancel,
}: {
  onPick: (iso: string) => void;
  onCancel: () => void;
}) {
  const [custom, setCustom] = useState("");

  const preset = (offset: () => Date) => () =>
    onPick(offset().toISOString());

  const inOneHour = () => {
    const d = new Date();
    d.setHours(d.getHours() + 1);
    return d;
  };
  const inFourHours = () => {
    const d = new Date();
    d.setHours(d.getHours() + 4);
    return d;
  };
  // 9 a.m. del día siguiente.
  const tomorrowMorning = () => {
    const d = new Date();
    d.setDate(d.getDate() + 1);
    d.setHours(9, 0, 0, 0);
    return d;
  };
  // Lunes de la semana próxima a las 9 a.m.
  const nextMonday = () => {
    const d = new Date();
    const day = d.getDay();
    const delta = ((1 + 7 - day) % 7) || 7;
    d.setDate(d.getDate() + delta);
    d.setHours(9, 0, 0, 0);
    return d;
  };

  return (
    <div className="email-bulk-dropdown email-snooze-dropdown">
      <button
        type="button"
        className="email-bulk-dropdown-item"
        onClick={preset(inOneHour)}
      >
        <Clock size={12} aria-hidden /> En 1 hora
      </button>
      <button
        type="button"
        className="email-bulk-dropdown-item"
        onClick={preset(inFourHours)}
      >
        <Clock size={12} aria-hidden /> En 4 horas
      </button>
      <button
        type="button"
        className="email-bulk-dropdown-item"
        onClick={preset(tomorrowMorning)}
      >
        <Clock size={12} aria-hidden /> Mañana 9:00
      </button>
      <button
        type="button"
        className="email-bulk-dropdown-item"
        onClick={preset(nextMonday)}
      >
        <Clock size={12} aria-hidden /> Próximo lunes 9:00
      </button>
      <div className="email-snooze-custom">
        <input
          type="datetime-local"
          value={custom}
          onChange={(e) => setCustom(e.target.value)}
        />
        <button
          type="button"
          className="btn btn-primary small"
          disabled={!custom}
          onClick={() => {
            if (custom) onPick(new Date(custom).toISOString());
          }}
        >
          Aplicar
        </button>
        <button type="button" className="btn small" onClick={onCancel}>
          Cancelar
        </button>
      </div>
    </div>
  );
}
