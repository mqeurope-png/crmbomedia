"use client";

import { ArrowDownLeft, ArrowLeft, ArrowUpRight, Reply } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { EmailComposerModal } from "../../components/EmailComposerModal";
import { PageHeader } from "../../components/PageHeader";
import {
  getEmailThread,
  markThreadRead,
  type EmailMessage,
  type EmailThreadDetail,
} from "../../lib/emailsApi";
import { extractErrorMessage } from "../../lib/errors";

function formatDateTime(value: string): string {
  return new Date(value).toLocaleString("es-ES", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function EmailThreadPage() {
  const params = useParams<{ thread_id: string }>();
  const [thread, setThread] = useState<EmailThreadDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [replyTo, setReplyTo] = useState<EmailMessage | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await getEmailThread(params.thread_id);
      setThread(data);
      if (data.has_unread_replies) {
        await markThreadRead(data.id).catch(() => undefined);
      }
    } catch (err) {
      setError(
        extractErrorMessage(err, "No se pudo cargar el hilo."),
      );
    } finally {
      setLoading(false);
    }
  }, [params.thread_id]);

  useEffect(() => {
    load();
  }, [load]);

  // Prefill the reply with the latest inbound message's sender so
  // the operator doesn't end up replying to themselves when the
  // most recent message in the thread is their own outbound.
  // Hooks must stay above the conditional returns below so the
  // call order matches across renders.
  const lastInbound = useMemo(() => {
    const msgs = thread?.messages ?? [];
    return [...msgs].reverse().find((m) => m.direction === "inbound") ?? null;
  }, [thread?.messages]);

  if (loading)
    return (
      <main className="shell"><p className="muted">Cargando…</p></main>
    );
  if (error || !thread)
    return (
      <main className="shell"><p className="form-error">{error}</p></main>
    );

  const last = thread.messages[thread.messages.length - 1];
  const replyParent = lastInbound ?? last;
  // Reply target: the lead, NEVER the comercial. The backend computes
  // this by filtering out the operator's own aliases — `direction`
  // alone lies when a comercial replies straight from Gmail (it comes
  // back through the account watch labelled inbound). The client-side
  // fallbacks only matter during a rolling deploy where the API is a
  // version behind and hasn't started sending `reply_to_suggestion`.
  const replyTarget =
    thread.reply_to_suggestion ??
    lastInbound?.from_email ??
    thread.messages[0]?.to_emails?.[0] ??
    null;

  return (
    <main className="shell shell-wide">
      <PageHeader
        title={thread.subject || "(sin asunto)"}
        eyebrow="Email"
        crumbs={[
          { label: "Emails", href: "/emails" },
          { label: thread.subject || "(sin asunto)" },
        ]}
        actions={
          <>
            <Link href="/emails" className="button small secondary">
              <ArrowLeft size={11} aria-hidden /> Volver
            </Link>
            <button
              type="button"
              className="button small"
              onClick={() => setReplyTo(replyParent)}
            >
              <Reply size={11} aria-hidden /> Responder
            </button>
          </>
        }
      />
      <div className="email-thread-header">
        <p className="muted small">
          <strong>{thread.messages.length} mensaje
          {thread.messages.length === 1 ? "" : "s"}</strong>
          {" · Participantes: "}
          {thread.participants.join(", ")}
        </p>
        {thread.contact_id ? (
          <p className="muted small">
            Contacto:{" "}
            <Link href={`/contacts/${thread.contact_id}`}>
              ver ficha
            </Link>
          </p>
        ) : null}
      </div>
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
              <pre className="email-body-text">{m.body_text || m.snippet || ""}</pre>
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
    </main>
  );
}
