"use client";

import { ArrowLeft, Reply } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
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

  if (loading)
    return (
      <main className="shell"><p className="muted">Cargando…</p></main>
    );
  if (error || !thread)
    return (
      <main className="shell"><p className="form-error">{error}</p></main>
    );

  const last = thread.messages[thread.messages.length - 1];

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
              onClick={() => setReplyTo(last)}
            >
              <Reply size={11} aria-hidden /> Responder
            </button>
          </>
        }
      />
      <p className="muted small">
        Participantes: {thread.participants.join(", ")}
      </p>
      <ul className="email-thread-messages">
        {thread.messages.map((m) => (
          <li
            key={m.id}
            className={`email-message email-message-${m.direction}`}
          >
            <header>
              <strong>{m.from_name || m.from_email}</strong>
              <span className="muted small">
                {" "}
                · para {m.to_emails.join(", ")}
              </span>
              <span className="muted small"> · {formatDateTime(m.sent_at)}</span>
            </header>
            {m.body_html ? (
              <iframe
                title={`Mensaje ${m.id}`}
                className="email-html-preview"
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
          contactEmail={
            replyTo.from_email === thread.participants[0]
              ? replyTo.from_email
              : null
          }
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
