"use client";

import { Inbox } from "lucide-react";
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

export default function EmailsPage() {
  const [threads, setThreads] = useState<EmailThread[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listEmailThreads()
      .then((page) => setThreads(page.items))
      .catch((err) =>
        setError(
          extractErrorMessage(err, "No se pudieron cargar los hilos."),
        ),
      )
      .finally(() => setLoading(false));
  }, []);

  return (
    <main className="shell shell-wide">
      <PageHeader
        title="Emails"
        eyebrow="Productividad"
        description="Hilos iniciados desde el CRM."
      />
      {error ? <p className="form-error">{error}</p> : null}
      {loading ? (
        <p className="muted">Cargando…</p>
      ) : threads.length === 0 ? (
        <p className="muted">
          <Inbox size={14} aria-hidden /> Aún no has enviado ningún email
          desde el CRM. Abre la ficha de un contacto y pulsa
          &quot;Email&quot; para empezar.
        </p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>Asunto</th>
              <th>Participantes</th>
              <th>Último mensaje</th>
              <th>#</th>
            </tr>
          </thead>
          <tbody>
            {threads.map((t) => (
              <tr key={t.id} className={t.has_unread_replies ? "is-selected" : undefined}>
                <td>
                  <Link href={`/emails/${t.id}`}>
                    {t.subject || "(sin asunto)"}
                  </Link>
                  {t.has_unread_replies ? (
                    <span className="badge ok"> Nuevo</span>
                  ) : null}
                </td>
                <td className="muted small">
                  {t.participants.slice(0, 3).join(", ")}
                  {t.participants.length > 3 ? "…" : ""}
                </td>
                <td className="muted small">{formatDateTime(t.last_message_at)}</td>
                <td className="muted small">{t.message_count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </main>
  );
}
