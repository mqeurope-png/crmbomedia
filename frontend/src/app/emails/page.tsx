"use client";

import { Inbox, Search } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { EmailTrackingStatsWidget } from "../components/dashboard/EmailTrackingStatsWidget";
import { PageHeader } from "../components/PageHeader";
import {
  listEmailThreads,
  type EmailThread,
} from "../lib/emailsApi";
import { extractErrorMessage } from "../lib/errors";

/** Gmail-style date column: time-only when the message landed
 *  today, day+month when in the same calendar year, full date
 *  otherwise. */
function formatRelative(value: string): string {
  const d = new Date(value);
  const now = new Date();
  if (d.toDateString() === now.toDateString()) {
    return d.toLocaleTimeString("es-ES", {
      hour: "2-digit",
      minute: "2-digit",
    });
  }
  if (d.getFullYear() === now.getFullYear()) {
    return d.toLocaleDateString("es-ES", {
      day: "2-digit",
      month: "short",
    });
  }
  return d.toLocaleDateString("es-ES", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}

export default function EmailsPage() {
  const [threads, setThreads] = useState<EmailThread[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchInput, setSearchInput] = useState("");
  const [debounced, setDebounced] = useState("");

  // Debounce search input by 300 ms — same pattern the contacts
  // page uses for free-text search.
  useEffect(() => {
    const handle = window.setTimeout(() => {
      setDebounced(searchInput.trim());
    }, 300);
    return () => window.clearTimeout(handle);
  }, [searchInput]);

  useEffect(() => {
    setLoading(true);
    listEmailThreads(undefined, debounced || undefined)
      .then((page) => setThreads(page.items))
      .catch((err) =>
        setError(
          extractErrorMessage(err, "No se pudieron cargar los hilos."),
        ),
      )
      .finally(() => setLoading(false));
  }, [debounced]);

  return (
    <main className="shell shell-wide">
      <PageHeader
        title="Emails"
        eyebrow="Productividad"
        description="Hilos iniciados desde el CRM."
      />
      <div className="email-inbox-stats">
        <EmailTrackingStatsWidget />
      </div>
      <div className="email-toolbar">
        <div className="email-search">
          <Search size={13} aria-hidden />
          <input
            type="search"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="Buscar en emails…"
            aria-label="Buscar hilos por contacto, asunto o cuerpo"
          />
        </div>
      </div>
      {error ? <p className="form-error">{error}</p> : null}
      {loading ? (
        <p className="muted">Cargando…</p>
      ) : threads.length === 0 ? (
        debounced ? (
          <p className="muted">
            Ningún hilo coincide con &quot;{debounced}&quot;.
          </p>
        ) : (
          <p className="muted">
            <Inbox size={14} aria-hidden /> Aún no has enviado ningún email
            desde el CRM. Abre la ficha de un contacto y pulsa
            &quot;Email&quot; para empezar.
          </p>
        )
      ) : (
        <table className="data-table emails-gmail-table">
          <thead>
            <tr>
              <th>Contacto</th>
              <th>Email</th>
              <th>Asunto · vista previa</th>
              <th>Último mensaje</th>
            </tr>
          </thead>
          <tbody>
            {threads.map((t) => {
              const contactName = t.contact_name || "(sin nombre)";
              const count = t.message_count;
              const countSuffix = count > 1 ? ` (${count})` : "";
              const subject = t.subject || "(sin asunto)";
              const snippet = t.last_message_snippet ?? "";
              return (
                <tr
                  key={t.id}
                  className={t.has_unread_replies ? "email-row-unread" : undefined}
                >
                  <td className="email-cell-contact">
                    <Link href={`/emails/${t.id}`}>
                      {contactName}
                      {countSuffix ? (
                        <span className="muted small">{countSuffix}</span>
                      ) : null}
                    </Link>
                  </td>
                  <td className="email-cell-email muted small">
                    {t.last_message_from ?? "—"}
                  </td>
                  <td className="email-cell-subject">
                    <Link href={`/emails/${t.id}`}>
                      <span
                        className={
                          t.has_unread_replies
                            ? "email-subject email-subject-unread"
                            : "email-subject"
                        }
                      >
                        {subject}
                      </span>
                      {snippet ? (
                        <>
                          <span className="email-subject-sep"> · </span>
                          <span className="email-snippet">{snippet}</span>
                        </>
                      ) : null}
                    </Link>
                  </td>
                  <td className="muted small email-cell-date">
                    {formatRelative(t.last_message_at)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </main>
  );
}
