"use client";

import { Mail, Plus } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import {
  listEmailThreads,
  type EmailThread,
} from "../lib/emailsApi";
import { extractErrorMessage } from "../lib/errors";

function formatDateTime(value: string): string {
  return new Date(value).toLocaleString("es-ES", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** "Emails" tab inside the contact detail. Lists threads where the
 *  contact participates and offers a CTA to open the composer. */
export function ContactEmailsSection({
  contactId,
  contactEmail,
  onCompose,
}: {
  contactId: string;
  contactEmail?: string | null;
  onCompose: () => void;
}) {
  const [threads, setThreads] = useState<EmailThread[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  void contactEmail;

  useEffect(() => {
    listEmailThreads(contactId)
      .then((page) => setThreads(page.items))
      .catch((err) =>
        setError(extractErrorMessage(err, "No se pudieron cargar los hilos.")),
      )
      .finally(() => setLoading(false));
  }, [contactId]);

  return (
    <div>
      <div className="section-title">
        <h3>
          <Mail size={12} aria-hidden /> Emails
        </h3>
        <button type="button" className="button small" onClick={onCompose}>
          <Plus size={11} aria-hidden /> Nuevo
        </button>
      </div>
      {error ? <p className="form-error">{error}</p> : null}
      {loading ? (
        <p className="muted small">Cargando…</p>
      ) : threads.length === 0 ? (
        <p className="muted small">Aún no has enviado emails a este contacto.</p>
      ) : (
        <ul className="widget-list">
          {threads.map((t) => (
            <li key={t.id} className="widget-row">
              <div className="widget-row-main">
                <p className="widget-row-title">
                  <Link href={`/emails/${t.id}`}>
                    {t.subject || "(sin asunto)"}
                  </Link>
                  {t.has_unread_replies ? (
                    <span className="badge ok"> Nuevo</span>
                  ) : null}
                </p>
                <p className="widget-row-meta muted small">
                  {formatDateTime(t.last_message_at)} · {t.message_count} mensaje
                  {t.message_count === 1 ? "" : "s"}
                </p>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
