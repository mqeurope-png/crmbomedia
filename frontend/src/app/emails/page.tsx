"use client";

import { ArrowDownLeft, ArrowUpRight, Inbox, Search } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { PageHeader } from "../components/PageHeader";
import {
  listEmailThreads,
  type EmailThread,
} from "../lib/emailsApi";
import { extractErrorMessage } from "../lib/errors";

function formatDateTime(value: string): string {
  const d = new Date(value);
  return d.toLocaleString("es-ES", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatSender(thread: EmailThread): React.ReactNode {
  if (!thread.last_message_from) return <span className="muted">—</span>;
  if (thread.last_message_direction === "outbound") {
    return (
      <span title={thread.last_message_from}>
        Tú · {thread.last_message_from}
      </span>
    );
  }
  return <span title={thread.last_message_from}>{thread.last_message_from}</span>;
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
      <div className="email-toolbar">
        <div className="email-search">
          <Search size={13} aria-hidden />
          <input
            type="search"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="Buscar en emails…"
            aria-label="Buscar hilos por asunto, remitente o cuerpo"
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
        <table className="data-table emails-list-table">
          <thead>
            <tr>
              <th>Asunto</th>
              <th>Remitente último</th>
              <th>Vista previa</th>
              <th>Último mensaje</th>
              <th>#</th>
            </tr>
          </thead>
          <tbody>
            {threads.map((t) => {
              const visibleParticipants = t.participants.slice(0, 3);
              const overflow = t.participants.length - visibleParticipants.length;
              const snippet = t.last_message_snippet ?? "";
              return (
                <tr
                  key={t.id}
                  className={t.has_unread_replies ? "is-selected" : undefined}
                >
                  <td>
                    <Link href={`/emails/${t.id}`}>
                      <strong>{t.subject || "(sin asunto)"}</strong>
                    </Link>
                    {t.has_unread_replies ? (
                      <span className="badge ok"> Nuevo</span>
                    ) : null}
                    {visibleParticipants.length > 0 ? (
                      <p className="muted small emails-participants">
                        {visibleParticipants.join(", ")}
                        {overflow > 0 ? ` +${overflow} más` : ""}
                      </p>
                    ) : null}
                  </td>
                  <td className="muted small">
                    {t.last_message_direction === "outbound" ? (
                      <ArrowUpRight size={11} aria-hidden />
                    ) : t.last_message_direction === "inbound" ? (
                      <ArrowDownLeft size={11} aria-hidden />
                    ) : null}{" "}
                    {formatSender(t)}
                  </td>
                  <td
                    className="muted small emails-snippet"
                    title={snippet.length > 80 ? snippet : undefined}
                  >
                    {snippet ? (
                      snippet.length > 80 ? `${snippet.slice(0, 80)}…` : snippet
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="muted small">
                    {formatDateTime(t.last_message_at)}
                  </td>
                  <td className="muted small">{t.message_count}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </main>
  );
}
