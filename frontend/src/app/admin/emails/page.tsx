"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { PageHeader } from "../../components/PageHeader";
import {
  listAdminEmailThreads,
  type EmailThread,
} from "../../lib/emailsApi";
import { extractErrorMessage } from "../../lib/errors";

function formatDateTime(value: string): string {
  return new Date(value).toLocaleString("es-ES", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function AdminEmailsPage() {
  const [threads, setThreads] = useState<EmailThread[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listAdminEmailThreads()
      .then((page) => {
        setThreads(page.items);
        setTotal(page.total);
      })
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
        title="Todos los emails"
        eyebrow="Admin"
        description={`${total} hilos en total.`}
      />
      {error ? <p className="form-error">{error}</p> : null}
      {loading ? (
        <p className="muted">Cargando…</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>Asunto</th>
              <th>Iniciado por</th>
              <th>Cuenta Gmail</th>
              <th>Último mensaje</th>
              <th>Mensajes</th>
            </tr>
          </thead>
          <tbody>
            {threads.map((t) => (
              <tr key={t.id}>
                <td>
                  <Link href={`/emails/${t.id}`}>
                    {t.subject || "(sin asunto)"}
                  </Link>
                </td>
                <td className="muted small">{t.initiated_by_user_id}</td>
                <td className="muted small">{t.gmail_account_user_id}</td>
                <td className="muted small">
                  {formatDateTime(t.last_message_at)}
                </td>
                <td className="muted small">{t.message_count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </main>
  );
}
